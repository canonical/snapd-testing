import gc, logging, pickle, os, time, threading, shutil
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, MinMaxScaler

# Silence TensorFlow logs BEFORE importing it
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

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

        with self.training_lock:
            try:
                # 1. Get Metadata once for the whole loop
                enc, scal = self._get_metadata()
                
                # 2. Get/Build the model once
                # We peek at the first file just to get the shape if building fresh
                sample_df = pd.read_csv(ts_files[0])
                sample_proc = self._preprocess_dataframe(sample_df.copy(), enc, scal)
                X_sample, _ = self._prepare_sequences(sample_proc)
                
                # Use a lock-free internal call to avoid deadlocking on self._lock
                model = self._get_or_build_internal((X_sample.shape[1], X_sample.shape[2]))

                logger.info(f"Starting training on {len(ts_files)} files...")

                for i, ts_file in enumerate(ts_files):
                    logger.info(f"[{i+1}/{len(ts_files)}] Training on: {os.path.basename(ts_file)}")
                    
                    df = pd.read_csv(ts_file)
                    proc_df = self._preprocess_dataframe(df, enc, scal)
                    X, y = self._prepare_sequences(proc_df)

                    # Train on this single file
                    # We use verbose=1 so you can see it moving in the logs
                    model.fit(X, y, epochs=2, batch_size=4, verbose=1)

                    # Move file to processed immediately so we don't re-train if we crash
                    shutil.move(ts_file, os.path.join(processed_dir, os.path.basename(ts_file)))

                # Save everything once at the end
                model.save(self.model_path)
                self._save_metadata(enc, scal)
                
                logger.info("All files processed. Syncing in-memory model...")
                self._load_from_disk()
                return True

            except Exception as e:
                logger.error(f"1-by-1 training failed: {e}", exc_info=True)
                return False

    def get_state(self):
        # If not loaded yet, try a one-time load
        if self.model is None:
            logger.info("First request detected. Triggering lazy load...")
            self._load_from_disk()

        with self._lock:
            return self.model, self.encoders, self.last_updated

