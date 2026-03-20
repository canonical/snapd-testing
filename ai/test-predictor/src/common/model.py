import gc, pickle, os, time, threading, shutil
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder, MinMaxScaler

from keras import backend as K
from keras.models import Sequential, load_model
from keras.layers import LSTM, Dense, Dropout, Input
from keras.utils import pad_sequences

from common import config
from common.utils import setup_logging

logger = setup_logging("model-manager")

class ModelManager:
    def __init__(self, model_path, metadata_path):
        self.model_path = model_path
        self.metadata_path = metadata_path
        self.model = None
        self.encoders = None
        self.last_updated = 0
        self._lock = threading.RLock() 
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
        cat_cols = ['verb', 'level', 'backend', 'system', 'name', 'scenario']
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
            features = group[['duration_ms', 'attempt', 'verb', 'level', 'backend', 'system', 'name', 'scenario']].values
            target = group['success'].iloc[-1] 
            sequences.append(features)
            targets.append(target)
        
        X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='post', dtype='float32')
        logger.info(f"Prepared {len(X)} sequences with {X.shape[2]} features")
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

        # Use default input shape if not provided
        if input_shape is None:
            input_shape = (config.SEQUENCE_LENGTH, config.NUM_FEATURES)

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
                    logger.info("Model loaded successfully from disk.")
                else:
                    logger.error("Failed to load model from disk. No fallback available.")
                    return None

            # Case C: Brand new (Requires input_shape)
            else:
                logger.info(f"Building fresh model with input shape {input_shape}")
                model = Sequential([
                    Input(shape=input_shape),
                    LSTM(config.LSTM_UNITS),
                    Dropout(config.DROPOUT_RATE),
                    Dense(config.DENSE_UNITS, activation=config.HIDDEN_ACTIVATION),
                    Dense(config.OUTPUT_UNITS, activation=config.OUTPUT_ACTIVATION)
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
                
                # Load model using the expected shape
                model = self.load_or_build_model()

                logger.info(f"Starting training on {len(valid_data)} validated files...")

                # 3. Training Loop using pre-loaded DataFrames
                for i, (df, ts_path) in enumerate(valid_data):
                    logger.info(f"[{i+1}/{len(valid_data)}] Training on: {os.path.basename(ts_path)}")
                    
                    # Process the dataframe already in memory
                    proc_df = self._preprocess_dataframe(df, enc, scal)
                    X, y = self._prepare_sequences(proc_df)

                    if len(X) > 0:
                        model.fit(X, y, epochs=config.EPOCHS, batch_size=config.BATCH_SIZE, verbose=config.TRAINING_VERBOSE)

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

    def unload_model(self):
        """
        Forcefully unloads the model from memory and clears the TensorFlow session.
        """
        with self._lock:
            if self.model is not None:
                logger.info("Unloading model and clearing TensorFlow session...")
                
                # Clear the Keras/TF backend session
                # This destroys the underlying C++ graph and variables
                K.clear_session()
                
                # Remove the Python reference
                self.model = None
                self.encoders = None
                
                # Explicitly trigger Python Garbage Collection
                gc.collect()
                
                logger.info("Model unloaded successfully.")
            else:
                logger.info("No model was loaded in memory to unload.")

    def reload_model(self):
        """
        Public method to trigger a reload from disk, used by API and Predictor.
        """
        with self._lock:
            logger.info("Manual reload triggered.")
            self.unload_model()

        return self._load_from_disk()
