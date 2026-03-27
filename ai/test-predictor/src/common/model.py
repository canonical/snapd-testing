import gc, pickle, os, time, threading, shutil
import numpy as np
import pandas as pd

from sklearn.preprocessing import LabelEncoder, MinMaxScaler
from sklearn.utils import class_weight

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
                # Force string type for consistent categorical treatment
                col_data = df[col].astype(str)
                
                if col not in encoders:
                    # INITIAL CREATION: Establish a stable base by sorting unique labels
                    encoders[col] = LabelEncoder()
                    base_labels = sorted(col_data.unique())
                    encoders[col].classes_ = np.array(base_labels)
                    logger.info(f"Initialized encoder for {col} with {len(base_labels)} classes.")
                else:
                    # INCREMENTAL UPDATE: Append only to prevent ID shifting
                    existing_classes = encoders[col].classes_
                    new_labels = sorted([l for l in col_data.unique() if l not in existing_classes])

                    if new_labels:
                        # Append new items to the END so existing IDs stay the same
                        updated_classes = np.concatenate([existing_classes, new_labels])
                        encoders[col].classes_ = updated_classes
                        logger.info(f"Appended {len(new_labels)} new labels to {col} encoder.")
                
                # 1. Transform to Integer IDs
                df[col] = encoders[col].transform(col_data).astype('float32')

                # 2. NEW: SCALE IDs to 0.0 - 1.0 range
                # This ensures Name ID 500 doesn't "drown out" Success 1.0
                num_classes = len(encoders[col].classes_)
                if num_classes > 1:
                    df[col] = df[col] / (num_classes - 1)
                else:
                    df[col] = 0.0

        # Prevent Scaler Reset for duration_ms
        if not hasattr(scaler, 'scale_'):
            logger.info("Initializing global scaler for duration_ms...")
            df[['duration_ms']] = scaler.fit_transform(df[['duration_ms']])
        else:
            df[['duration_ms']] = scaler.transform(df[['duration_ms']])

        logger.info(f"Preprocessed {len(df)} rows.")
        return df

    def _prepare_sequences(self, df):
        """
        Generates sliding window sequences for all rows (Preparing, Executing, Restoring)
        to capture the full test lifecycle.
        """
        # Ensure chronological order
        if 'start' in df.columns:
            df['start'] = pd.to_datetime(df['start'])
            df = df.sort_values(by='start')

        sequences, targets = [], []
        
        # Group by system to maintain separate timelines
        for _, group in df.groupby('system'):
            # Must match config.NUM_FEATURES = 9
            feature_cols = [
                'duration_ms', 'attempt', 'verb', 'level', 
                'backend', 'system', 'name', 'scenario', 'success'
            ]
            
            group_features = group[feature_cols].values
            group_targets = group['success'].values
            
            # Create a sliding window for EVERY row in the group
            for i in range(len(group_features)):
                # Take the last N steps leading up to the current row
                start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                window = group_features[start_idx : i + 1]
                
                sequences.append(window)
                targets.append(group_targets[i])
        
        if not sequences:
            return np.array([]), np.array([])

        # Use pre-padding (standard for LSTMs to keep recent data at the end)
        X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='pre', dtype='float32')
        
        logger.info(f"Prepared {len(X)} sequences (Full lifecycle).")
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

        with self.training_lock:
            try:
                #  Store a copy of ALL files to move later
                all_ts_files_to_move = [f for f in ts_files if f.endswith(".ts")]
                
                # Sort and CAP the training set only
                ts_files.sort(key=os.path.getmtime, reverse=True)
                total_available = len(ts_files)
                training_subset = ts_files[:config.TRAINING_MAX_FILES]
                
                logger.info(f"Cleanup: {total_available} files will be moved. "
                            f"Training: Using {len(training_subset)} most recent.")

                enc, scal = self._get_metadata()
                all_X, all_y = [], []

                # Aggregation Loop (Only on the subset)
                for ts_file in training_subset:
                    try:
                        df = pd.read_csv(ts_file)
                        if df.empty:
                            continue
                        
                        proc_df = self._preprocess_dataframe(df, enc, scal)
                        X, y = self._prepare_sequences(proc_df)
                        
                        if len(X) > 0:
                            all_X.append(X)
                            all_y.append(y)
                            
                    except Exception as e:
                        logger.error(f"Error reading {ts_file}: {e}")

                if not all_X:
                    logger.warning("No valid training data found in subset.")
                    # Even if training fails, we don't move files to avoid losing data
                    return False

                # Training
                X_train = np.concatenate(all_X, axis=0)
                y_train = np.concatenate(all_y, axis=0)
                
                model = self.load_or_build_model(input_shape=(X_train.shape[1], X_train.shape[2]))
                
                total_samples = len(X_train)
                chunk_size = config.TRAINING_CHUNKS_SIZE
                
                logger.info(f"Starting chunked training on {total_samples} sequences...")
                # Loop through data in chunks
                for i in range(0, total_samples, chunk_size):
                    end = min(i + chunk_size, total_samples)
                    X_chunk = X_train[i:end]
                    y_chunk = y_train[i:end]
                    
                    logger.info(f"Training on chunk {i//chunk_size + 1}: samples {i} to {end}")
                    
                    # Use a smaller number of epochs per chunk to keep it moving
                    model.fit(
                        X_chunk, 
                        y_chunk, 
                        epochs=config.EPOCHS, 
                        batch_size=config.BATCH_SIZE, 
                        verbose=1,
                        shuffle=True 
                    )
                    
                    # Force garbage collection to free RAM after each chunk
                    del X_chunk, y_chunk
                    import gc
                    gc.collect()

                # Persist
                model.save(self.model_path)
                self._save_metadata(enc, scal)
                
                # Finalize: Move ALL files originally in the directory
                files_moved = 0
                for path in all_ts_files_to_move:
                    if not os.path.exists(path): continue
                    dest = os.path.join(processed_dir, os.path.basename(path))
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(path, dest)
                    files_moved += 1

                logger.info(f"Training complete. Moved {files_moved} files to processed.")
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
