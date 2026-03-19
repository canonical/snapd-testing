import gc, logging, pickle, os, time, threading, shutil
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, MinMaxScaler

# Silence TensorFlow logs BEFORE importing it
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ['TF_NUM_INTRAOP_THREADS'] = '1'
os.environ['TF_NUM_INTEROP_THREADS'] = '1'

import tensorflow as tf
tf.config.threading.set_intra_op_parallelism_threads(1)
tf.config.threading.set_inter_op_parallelism_threads(1)

from tensorflow.keras.models import load_model, Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
from tensorflow.keras.preprocessing.sequence import pad_sequences
from tensorflow.keras import backend as K

from common.config import setup_logging

logger = setup_logging("tp-model-manager")

class ModelManager:
    def __init__(self, model_path, metadata_path):
        self.model_path = model_path
        self.metadata_path = metadata_path
        self.model = None
        self.encoders = None
        self.last_updated = 0
        self._lock = threading.Lock()
        self.training_lock = threading.Lock()

    def _get_metadata(self):
        if os.path.exists(self.metadata_path):
            with open(self.metadata_path, 'rb') as f:
                return pickle.load(f)
        return {}, MinMaxScaler()

    def _save_metadata(self, encoders, scaler):
        with open(self.metadata_path, 'wb') as f:
            pickle.dump((encoders, scaler), f)

    def _preprocess_dataframe(self, df, encoders, scaler):
        cat_cols = ['verb', 'level', 'backend', 'system', 'name']
        for col in cat_cols:
            if col in df.columns:
                if col not in encoders:
                    encoders[col] = LabelEncoder()
                    df[col] = encoders[col].fit_transform(df[col].astype(str))
                else:
                    existing_classes = set(encoders[col].classes_)
                    new_labels = set(df[col].astype(str).unique())
                    if not new_labels.issubset(existing_classes):
                        combined = sorted(list(existing_classes | new_labels))
                        encoders[col].classes_ = np.array(combined)
                    df[col] = encoders[col].transform(df[col].astype(str))
        
        df[['duration_ms']] = scaler.fit_transform(df[['duration_ms']])
        logger.info(f"Preprocessed: {len(df)} rows")
        return df

    def _prepare_sequences(self, df):
        sequences, targets = [], []
        for _, group in df.groupby('instance'):
            features = group[['duration_ms', 'attempt', 'verb', 'level', 'backend', 'system', 'name']].values
            target = group['success'].iloc[-1] 
            sequences.append(features)
            targets.append(target)
        
        X = pad_sequences(sequences, padding='post', dtype='float32')
        logger.info(f"Prepared {len(X)} sequences")
        return X, np.array(targets)

    def exists(self):
        return os.path.exists(self.model_path) and os.path.exists(self.metadata_path)

    def _load_from_disk(self):
        try:
            if not self.exists(): return False
            with open(self.metadata_path, 'rb') as f:
                self.encoders, _ = pickle.load(f)
            
            K.clear_session()
            self.model = load_model(self.model_path, compile=False)
            self.last_updated = time.time()
            gc.collect()
            return True
        except Exception as e:
            logger.error(f"Reload failed: {e}")
            return False

    def load_or_build_model(self, input_shape=None):
        """
        Thread-safe: 
        1. If already in memory, use it.
        2. If on disk, load it (ignores input_shape).
        3. If neither, build fresh using input_shape.
        """
        try:
            # Case A: Already in memory
            if self.model is not None:
                logger.info("Using in-memory model instance.")
                model = self.model
            
            # Case B: Not in memory, but exists on disk
            elif os.path.exists(self.model_path):                    
                logger.info(f"Loading model from disk: {self.model_path}")
                if self._load_from_disk():
                    model = self.model
                else:
                    logger.error("Failed to load model from disk. No fallback available.")
                    return None

            # Case C: Brand new (Requires input_shape)
            else:
                if input_shape is None:
                    logger.error("No model found and no input_shape provided to build one.")
                    return None
                
                logger.info(f"Building fresh model with input shape {input_shape}")
                model = Sequential([
                    Input(shape=input_shape),
                    LSTM(64),
                    Dropout(0.2),
                    Dense(32, activation='relu'),
                    Dense(1, activation='sigmoid')
                ])

            # Step 2: Ensure we actually found or built a model
            if model is None:
                logger.error("No model found on disk and no input_shape provided to build one.")
                return None

            # Re-compile so it's ready for .fit() or .predict()
            model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
            
            self.model = model
            self.last_updated = time.time()
            return self.model

        except Exception as e:
            logger.error(f"load_or_build_model failed: {e}")
            return None

    def train(self, ts_files, processed_dir):
        if not ts_files:
            return False

        # Pre-validation and Loading
        valid_data = []
        for ts_file in ts_files:
            if not ts_file.endswith(".ts"):
                continue
            try:
                df = pd.read_csv(ts_file)
                if df.empty:
                    logger.warning(f"Removing empty file: {ts_file}")
                    os.remove(ts_file)
                    continue
                valid_data.append((df, ts_file))
            except Exception as e:
                logger.error(f"Error reading {ts_file}: {e}")

        if not valid_data:
            return False

        with self.training_lock:
            try:
                # Setup Metadata and Model
                enc, scal = self._get_metadata()
                
                # Peek at first valid dataframe to determine shape
                first_df, _ = valid_data[0]
                sample_proc = self._preprocess_dataframe(first_df.copy(), enc, scal)
                X_sample, _ = self._prepare_sequences(sample_proc)
                
                model = self.load_or_build_model((X_sample.shape[1], X_sample.shape[2]))

                logger.info(f"Starting training on {len(valid_data)} validated files...")

                # 3. Training Loop using pre-loaded DataFrames
                for i, (df, ts_path) in enumerate(valid_data):
                    logger.info(f"[{i+1}/{len(valid_data)}] Training on: {os.path.basename(ts_path)}")
                    
                    # Process the dataframe already in memory
                    proc_df = self._preprocess_dataframe(df, enc, scal)
                    X, y = self._prepare_sequences(proc_df)

                    if len(X) > 0:
                        model.fit(X, y, epochs=2, batch_size=4, verbose=0)

                    # 4. Cleanup: Move the file now that training for it is done
                    shutil.move(ts_path, os.path.join(processed_dir, os.path.basename(ts_path)))

                # 5. Final Persist and Sync
                model.save(self.model_path)
                self._save_metadata(enc, scal)
                
                logger.info("Syncing in-memory model...")
                self._load_from_disk()
                return True

            except Exception as e:
                logger.error(f"Training failed: {e}", exc_info=True)
                return False


    def get_state(self):
        # If not loaded yet, try a one-time load
        if self.model is None:
            logger.info("First request detected. Triggering lazy load...")
            self._load_from_disk()

        with self._lock:
            return self.model, self.encoders, self.last_updated

