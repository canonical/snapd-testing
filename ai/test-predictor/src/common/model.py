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

        logger.info(f"Preprocessed {len(df)} rows. All features normalized to [0, 1].")
        return df

    def _prepare_sequences(self, df):
        if 'start' in df.columns:
            df['start'] = pd.to_datetime(df['start'])
            df = df.sort_values(by='start')

        sequences, targets = [], []
        
        for _, group in df.groupby('system'):
            feature_cols = [
                'duration_ms', 'attempt', 'verb', 'level', 
                'backend', 'system', 'name', 'scenario', 'success'
            ]
            
            group_features = group[feature_cols].values
            group_targets = group['success'].values
            
            # ONLY CREATE SAMPLES FOR 'executing' ROWS ---
            # We use the history (including preparing/restoring) as the WINDOW,
            # but we only care about predicting the 'executing' outcome.
            
            # Find indices where verb is 'executing'
            # Note: You'll need to know the encoded ID for 'executing' 
            # or just use a mask on the original dataframe before .values
            exec_indices = group.index[group['verb'] == 'executing'].tolist()
            
            # Get the relative integer positions of 'executing' rows within this group
            group_positions = [group.index.get_loc(idx) for idx in exec_indices]

            for i in group_positions:
                start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                window = group_features[start_idx : i + 1]
                
                sequences.append(window)
                targets.append(group_targets[i])
        
        X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='pre', dtype='float32')
        logger.info(f"Prepared {len(X)} sequences (focused on executions).")
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
                # Load metadata and existing model (or build a fresh one)
                enc, scal = self._get_metadata()
                
                all_X = []
                all_y = []
                files_to_move = []

                logger.info(f"Aggregating {len(ts_files)} files for batch training...")

                # Collection Loop: Process all files into memory first
                for ts_file in ts_files:
                    if not ts_file.endswith(".ts"):
                        continue
                    try:
                        df = pd.read_csv(ts_file)
                        if df.empty:
                            os.remove(ts_file)
                            continue
                        
                        # Preprocess (using the updated _preprocess_dataframe logic)
                        proc_df = self._preprocess_dataframe(df, enc, scal)
                        X, y = self._prepare_sequences(proc_df)
                        
                        if len(X) > 0:
                            all_X.append(X)
                            all_y.append(y)
                            files_to_move.append(ts_file)
                            
                    except Exception as e:
                        logger.error(f"Error reading {ts_file}: {e}")

                if not all_X:
                    logger.warning("No valid training data found in provided files.")
                    return False

                # Concatenate all sequences into single arrays for the optimizer
                X_train = np.concatenate(all_X, axis=0)
                y_train = np.concatenate(all_y, axis=0)
                
                # Re-fetch or build model now that we know the input shape (X_train.shape[1:])
                model = self.load_or_build_model(input_shape=(X_train.shape[1], X_train.shape[2]))

                # Calculate Class Weights using sklearn
                # This fixes the "always 0.99" problem by making failures more important
                # unique_classes = np.unique(y_train)
                #weights = class_weight.compute_class_weight(
                #    class_weight='balanced',
                #    classes=unique_classes,
                #    y=y_train
                #)
                #class_weight_dict = dict(zip(unique_classes, weights))
                #class_weight_dict = None
                
                logger.info(f"Training on {len(X_train)} total sequences...")
                
                # Single fit call: Training on the full batch prevents catastrophic forgetting
                model.fit(
                    X_train, 
                    y_train, 
                    epochs=config.EPOCHS, 
                    batch_size=config.BATCH_SIZE, 
                    #class_weight=class_weight_dict,
                    verbose=config.TRAINING_VERBOSE,
                    shuffle=True 
                )

                # Save model and the updated metadata (encoders/scaler)
                model.save(self.model_path)
                self._save_metadata(enc, scal)
                
                # Move files to processed directory only after successful save
                for path in files_to_move:
                    dest = os.path.join(processed_dir, os.path.basename(path))
                    # Handle existing files in processed_dir
                    if os.path.exists(dest):
                        os.remove(dest)
                    shutil.move(path, dest)

                logger.info("Training complete. Syncing in-memory state...")
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
