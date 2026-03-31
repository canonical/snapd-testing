import datetime
import gc
import pickle
import os
import shutil
import time
import threading
import shutil
import numpy as np
import pandas as pd

from sklearn.preprocessing import LabelEncoder, MinMaxScaler
from sklearn.utils import class_weight

from keras import backend as K
from keras.models import Sequential, load_model
from keras.layers import LSTM, Dense, Dropout, Input
from keras.optimizers import Adam
from keras.utils import pad_sequences

import tensorflow as tf

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

    def _focal_loss(self, gamma=4.0, alpha=0.75):
        """
        Focuses on difficult/misclassified examples.
        gamma: balance between easy/hard (2.0 is standard).
        alpha: balance between classes (0.75 prioritizes failures in binary).
        """
        def loss(y_true, y_pred):
            # Clip to prevent log(0)
            y_pred = tf.clip_by_value(y_pred, tf.keras.backend.epsilon(), 1 - tf.keras.backend.epsilon())
            bce = tf.keras.losses.binary_crossentropy(y_true, y_pred)
            pt = tf.exp(-bce)
            return alpha * (1 - pt) ** gamma * bce
        return loss

    def _preprocess_dataframe(self, df, encoders, scaler):
        # HANDLE SUCCESS (Binary Force)
        if 'success' in df.columns:
            # Convert numeric/strings to float, force binary 0 or 1
            df['success'] = pd.to_numeric(df['success'], errors='coerce').fillna(0)
            df['success'] = (df['success'] >= 1).astype('float32')

        # HANDLE ATTEMPT (Simple scaling to 0-1)
        if 'attempt' in df.columns:
            # Assume 10 as a reasonable max attempt to keep value small
            df['attempt'] = pd.to_numeric(df['attempt'], errors='coerce').fillna(1).astype('float32') / 10.0

        # CATEGORICAL ENCODING
        # Note: 'success' and 'attempt' are excluded from cat_cols as they are handled above
        cat_cols = ['verb', 'backend', 'system', 'name', 'scenario']
        
        for col in cat_cols:
            if col in df.columns:
                col_data = df[col].astype(str).fillna("unknown")
                
                if col not in encoders:
                    encoders[col] = LabelEncoder()
                    base_labels = sorted(col_data.unique())
                    encoders[col].classes_ = np.array(base_labels)
                else:
                    existing_classes = encoders[col].classes_
                    new_labels = sorted([l for l in col_data.unique() if l not in existing_classes])
                    if new_labels:
                        encoders[col].classes_ = np.concatenate([existing_classes, new_labels])
                
                # Transform to IDs and Scale 0.0 - 1.0
                df[col] = encoders[col].transform(col_data).astype('float32')
                num_classes = len(encoders[col].classes_)
                if num_classes > 1:
                    df[col] = df[col] / (num_classes - 1)
                else:
                    df[col] = 0.0

        logger.info(f"Preprocessed {len(df)} rows.")
        return df

    def _prepare_sequences(self, df):
        """
        Transforms DataFrame into 3D sequences using global FEATURE_COLUMNS.
        Ensures chronological order and pads to SEQUENCE_LENGTH.
        Blinds the target step's success value to prevent data leakage.
        """
        if 'start' in df.columns:
            df['start'] = pd.to_datetime(df['start'])
            df = df.sort_values(by='start')

        feature_cols = config.FEATURE_COLUMNS
        # Find the index of 'success' to blind it correctly
        try:
            success_idx = feature_cols.index('success')
        except ValueError:
            logger.error(" 'success' not found in FEATURE_COLUMNS. Training will fail.")
            return np.array([]), np.array([])

        sequences, targets = [], []
        
        for _, group in df.groupby('system'):
            available_cols = [c for c in feature_cols if c in group.columns]
            if len(available_cols) < len(feature_cols):
                continue

            group_features = group[feature_cols].values
            group_targets = group['success'].values
            
            for i in range(len(group_features)):
                start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                # .copy() is essential so we don't modify the source data
                window = group_features[start_idx : i + 1].copy()
                
                # BLIND THE TARGET: Set the success of the CURRENT step to 0.0
                # This forces the model to use the PREVIOUS rows to predict.
                window[-1, success_idx] = 0.0
                
                sequences.append(window)
                targets.append(group_targets[i])
        
        if not sequences:
            return np.array([]), np.array([])

        X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='pre', dtype='float32')
        
        logger.info(f"Prepared {len(X)} sequences. Blinding applied to column index {success_idx}.")
        return X, np.array(targets)

    def _load_from_disk(self):
        try:
            if not self.exists(): return False
            with open(self.metadata_path, 'rb') as f:
                self.encoders, _ = pickle.load(f)
            
            K.clear_session()
            # Register custom loss so Keras can load the model if it was compiled with it
            custom_objects = {'loss': self._focal_loss()}
            self.model = load_model(self.model_path, custom_objects=custom_objects, compile=False)
            self.last_updated = time.time()
            gc.collect()
            return True
        except Exception as e:
            logger.error(f"Reload failed: {e}")
            return False

    def _get_timestamped_path(self):
        """Creates and returns a path like model/2023-10-27_14-30-05/"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Assuming model_path is something like 'model/model.h5'
        new_dir = os.path.join(config.OLD_MODELS_DIR, timestamp)
        os.makedirs(new_dir, exist_ok=True)
        return new_dir

    def exists(self):
        return os.path.exists(self.model_path) and os.path.exists(self.metadata_path)

    def save_model_version(self, encoders, scaler):
        """Saves current model and metadata to a new timestamped directory."""
        version_dir = self._get_timestamped_path()

        # Save Metadata to the new version folder
        ver_metadata_path = os.path.join(version_dir, config.METADATA_NAME)
        with open(ver_metadata_path, 'wb') as f:
            pickle.dump((encoders, scaler), f)

        # Save Model to the new version folder
        ver_model_path = os.path.join(version_dir, config.MODEL_NAME)
        self.model.save(ver_model_path)

        # Update 'latest' (copy files to the root model directory)
        # This ensures your API always loads the most recent one by default
        shutil.copy2(ver_metadata_path, self.metadata_path)
        shutil.copy2(ver_model_path, self.model_path)

        logger.info(f"Model version saved to {version_dir} and promoted to latest.")
        return version_dir

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
                    LSTM(config.LSTM_UNITS, return_sequences=True),
                    Dropout(config.DROPOUT_RATE),
                    LSTM(config.SECOND_LSTM_UNITS),
                    Dropout(config.DROPOUT_RATE),
                    Dense(config.DENSE_UNITS, activation=config.HIDDEN_ACTIVATION),
                    Dense(config.OUTPUT_UNITS, activation=config.OUTPUT_ACTIVATION)
                ])

            # Step 2: Ensure we actually found or built a model
            if model is None:
                logger.error("No model found on disk and no input_shape provided to build one.")
                return None

            # Re-compile so it's ready for .fit() or .predict()
            model.compile(
                optimizer=Adam(learning_rate=config.ADAM_LEARNING_RATE),
                loss=self._focal_loss(gamma=2.0, alpha=0.25),
                metrics=[
                    'accuracy', 
                    tf.keras.metrics.Precision(name='precision'), 
                    tf.keras.metrics.Recall(name='recall')
                ]
            )
            
            self.model = model
            self.last_updated = time.time()
            return self.model

        except Exception as e:
            logger.error(f"load_or_build_model failed: {e}")
            return None

    def train(self, ts_files, processed_dir):
        """
        Orchestrates the batch training process using a subset of the most recent data.

        This method performs the following lifecycle:
        1. Identification: Indexes all pending .ts files for later cleanup.
        2. Recency Capping: Selects the newest files (via TRAINING_MAX_FILES) to 
           ensure the model learns from the most relevant system states.
        3. Chunked Training: Processes sequences in blocks (via TRAINING_CHUNKS_SIZE) 
           to prevent memory exhaustion (RAM) and process hangs.
        4. Persistence: Saves the updated .keras model and .pkl metadata.
        5. Cleanup: Moves all pending files to the processed directory regardless 
           of whether they were used in the specific training subset.

        Args:
            ts_files (list): Paths to pending .ts files in the training queue.
            processed_dir (str): Directory where files are archived after training.

        Returns:
            bool: True if training and persistence succeeded, False otherwise.

        Example:
            If you have 1000 files in the queue and TRAINING_MAX_FILES = 300:
            - The model studies the 300 newest files to find patterns.
            - Training is split into 50,000-sequence chunks to stay under RAM limits.
            - After success, all 1000 files are moved to 'processed/' to clear the queue.
        """

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

                # LOG DISTRIBUTION: If you see 0 failures here, the model can't learn!
                unique, counts = np.unique(y_train, return_counts=True)
                dist = dict(zip(unique, counts))
                logger.info(f"Target distribution (0=Fail, 1=Pass): {dist}")

                # CALCULATE CLASS WEIGHTS
                # This makes the model 'fear' missing a failure (0)
                # Force the model to pay 50x more attention to Failures
                class_weight_dict = {0: config.WEIGHT_NEGATIVE_CLASS, 1: config.WEIGHT_POSITIVE_CLASS}
                if len(unique) > 1:
                    weights = class_weight.compute_class_weight(
                        class_weight='balanced',
                        classes=unique,
                        y=y_train
                    )
                    class_weight_dict = dict(zip(unique, weights))
                    logger.info(f"Calculated Class Weights: {class_weight_dict}")

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
                        class_weight=class_weight_dict,
                        verbose=1,
                        shuffle=False  # Don't shuffle to preserve sequence order 
                    )
                    
                    # Force garbage collection to free RAM after each chunk
                    del X_chunk, y_chunk

                    gc.collect()

                # Save the new model version with a timestamped folder for traceability
                self.save_model_version(enc, scal)

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
