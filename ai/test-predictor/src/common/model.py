import gc
import json
import pickle
import os
import shutil
import time
import threading
import numpy as np
import pandas as pd

from datetime import datetime
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
        self.feature_index = { name: i for i, name in enumerate(config.FEATURE_COLUMNS) }
        self._lock = threading.RLock() 
        self.training_lock = threading.Lock()

    def _get_metadata(self):
        if os.path.exists(self.metadata_path):
            with open(self.metadata_path, 'rb') as f:
                return pickle.load(f)
        return {}, MinMaxScaler()

    def _save_model(self, model, model_path=None):
        if model_path is None:
            model_path = self.model_path
        model.save(model_path)
        logger.info(f"Model saved to {model_path}")

    def _save_metadata(self, encoders, scaler, metadata_path=None):
        if metadata_path is None:
            metadata_path = self.metadata_path

        with open(metadata_path, 'wb') as f:
            pickle.dump((encoders, scaler), f)

        logger.info(f"Metadata saved to {metadata_path}")

    def _save_stats(self, stats, stats_path=None):
        if stats_path is None:
            stats_path = os.path.join(config.MODEL_DIR, config.TRAINING_STATS)

        with open(stats_path, 'w') as f:
            json.dump(stats, f, indent=4)

        logger.info(f"Training stats saved to {stats_path}")

    def _focal_loss(self, gamma=config.FOCAL_LOSS_GAMMA, alpha=config.FOCAL_LOSS_ALPHA):
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
            df['success'] = pd.to_numeric(df['success'], errors='coerce').fillna(0)
            df['success'] = (df['success'] >= 1).astype('float32')

        # HANDLE ATTEMPT (Simple scaling to 0-1)
        if 'attempt' in df.columns:
            df['attempt'] = pd.to_numeric(df['attempt'], errors='coerce').fillna(1).astype('float32') / 10.0

        # CATEGORICAL ENCODING
        # Note: 'success' and 'attempt' are excluded from cat_cols as they are handled above
        cat_cols = ['verb', 'backend', 'system', 'name', 'scenario']
        
        for col in cat_cols:
            if col in df.columns:
                col_data = df[col].astype(str).fillna("unknown")
                
                if col not in encoders:
                    encoders[col] = LabelEncoder()
                    encoders[col].fit(col_data.unique())
                else:
                    existing_classes = encoders[col].classes_
                    new_labels = sorted([l for l in col_data.unique() if l not in existing_classes])
                    if new_labels:
                        encoders[col].classes_ = np.concatenate([existing_classes, new_labels])
                
                df[col] = encoders[col].transform(col_data).astype('int32')

        logger.info(f"Preprocessed {len(df)} rows.")
        return df

    def _prepare_sequences(self, df):
        if 'start' in df.columns:
            df['start'] = pd.to_datetime(df['start'])
            df = df.sort_values(by='start')

        feature_cols = config.FEATURE_COLUMNS
        sequences, targets = [], []
        
        for _, group in df.groupby(['system', 'name']):
            if not all(col in group.columns for col in feature_cols):
                continue

            # Ensure chronological order within the specific test history
            group = group.sort_values(by='start')
            
            group_features = group[feature_cols].values
            group_targets = group['success'].values
            
            for i in range(len(group_features)):
                start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                window = group_features[start_idx : i + 1].copy()
                
                # CRITICAL: If the window is shorter than SEQUENCE_LENGTH, 
                # pad_sequences handles it later, but ensure the window 
                # is actually relevant to this specific test's history.
                sequences.append(window)
                targets.append(group_targets[i])
        
        if not sequences:
            return np.array([]), np.array([])

        X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='pre', dtype='float32')
        
        logger.info(f"Prepared {len(X)} sequences.")
        return X, np.array(targets)

    def _load_from_disk(self):
        try:
            if not self.exists(): 
                return False
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

    def backup_model(self, encoders, scaler):
        """Saves current model and metadata to a new timestamped directory."""
        version_dir = self._get_timestamped_path()

        #Capture All Config Constants (e.g., SEQUENCE_LENGTH, LSTM_UNITS)
        config_snapshot = {
            key: getattr(config, key) 
            for key in dir(config) 
            if key.isupper() and not key.startswith("_")
        }

        # Save the standalone CONFIG_SNAPSHOT file
        config_snap_path = os.path.join(version_dir, config.CONFIG_SNAPSHOT)
        with open(config_snap_path, 'wb') as f:
            pickle.dump(config_snapshot, f)

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

    def load_or_build_model(self, input_shape=None, output_dir=None):
        """
        1. If output_dir is provided, build fresh (Shadow Training).
        2. If in memory, use it.
        3. If on disk, load it.
        4. Otherwise, build fresh.
        """
        if input_shape is None:
            input_shape = (config.SEQUENCE_LENGTH, config.NUM_FEATURES)

        try:
            model = None

            # Shadow Training - Always build fresh to match current config
            if output_dir is not None:
                logger.info(f"Shadow Training: Building fresh model in {output_dir} with shape {input_shape}")
                # We do NOT set self.model here yet to avoid affecting the LIVE predictor
                model = self._build_new_model_structure(input_shape)
            
            # Already in memory
            elif self.model is not None:
                logger.info("Using in-memory model instance.")
                model = self.model
            
            # Not in memory, but exists on disk
            elif os.path.exists(self.model_path):                    
                logger.info(f"Loading model from disk: {self.model_path}")
                # Try to load; if it fails, don't return None, just build fresh!
                if not self._load_from_disk():
                    logger.warning("Disk load failed (metadata mismatch?). Falling back to building fresh.")
                    model = self._build_new_model_structure(input_shape)
                else:
                    model = self.model

            # Brand new model
            else:
                logger.info(f"No model found. Building fresh with shape {input_shape}")
                model = self._build_new_model_structure(input_shape)

            if model is None:
                return None

            # Re-compile
            model.compile(
                optimizer=Adam(learning_rate=config.ADAM_LEARNING_RATE),
                loss=self._focal_loss(gamma=config.FOCAL_LOSS_GAMMA, alpha=config.FOCAL_LOSS_ALPHA),
                metrics=['accuracy', tf.keras.metrics.Precision(name='precision'), tf.keras.metrics.Recall(name='recall')]
            )
            
            # Only update the LIVE instance if we aren't in a shadow directory
            if output_dir is None:
                self.model = model
                self.last_updated = time.time()
                
            return model

        except Exception as e:
            logger.error(f"load_or_build_model failed: {e}")
            return None

    def _build_new_model_structure(self, input_shape):
        """Helper to define the architecture."""
        return Sequential([
            Input(shape=input_shape),
            LSTM(config.LSTM_UNITS, return_sequences=True),
            Dropout(config.DROPOUT_RATE),
            LSTM(config.SECOND_LSTM_UNITS),
            Dropout(config.DROPOUT_RATE),
            Dense(config.DENSE_UNITS, activation=config.HIDDEN_ACTIVATION),
            Dense(config.OUTPUT_UNITS, activation=config.OUTPUT_ACTIVATION)
        ])

    def _init_stats(self):
        # Initialize stats dictionary
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "files_processed": 0,
            "total_sequences": 0,
            "distribution": {},
            "final_loss": 0.0,
            "final_accuracy": 0.0
        }

    def _select_training_subset(self, ts_files, stats):
        ts_files.sort(key=os.path.getmtime, reverse=True)
        subset = ts_files[:config.TRAINING_MAX_FILES]

        stats["files_processed"] = len(subset)

        logger.info(f"Training using {len(subset)} most recent files")
        return subset
    
    def _load_training_data(self, files, enc, scal):
        """
        Loads, preprocesses, and aggregates training data from multiple CSV files.

        This method iterates through a list of file paths, cleans each dataframe, 
        generates sequences, and concatenates the results into final arrays for 
        model training.

        Args:
            files (list[str]): List of paths to the time-series CSV files.
            enc (OneHotEncoder): Fitted encoder for categorical feature transformation.
            scal (StandardScaler): Fitted scaler for numerical feature normalization.

        Returns:
            tuple: (X, y) as concatenated np.ndarrays if data is found; 
                   otherwise (None, None) if no valid sequences were processed.
        """
        all_X, all_y = [], []

        for ts_file in files:
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
            logger.warning("No valid training data found.")
            return None, None

        return (
            np.concatenate(all_X, axis=0),
            np.concatenate(all_y, axis=0),
        )

    def _augment_sequences(self, X, y, stats):
        """
        Performs synthetic data augmentation to balance class distribution and 
        teach the model specific failure patterns (Death Spirals, Deterioration).
        
        STRATEGY: "Only Augment Successes"
        To combat the 80%+ success bias in real-world data, this method targets 
        original 'Success' sequences (y=1) and transforms them into 'Failure' 
        sequences (y=0) by injecting synthetic noise into the tail of the features.
        
        Args:
            X (np.array): Input sequences of shape (Samples, 15, Features).
            y (np.array): Target labels (0 or 1).
            stats (dict): Dictionary to track augmentation counts and distributions.
            
        Returns:
            tuple: (final_X, final_y) The augmented dataset.
        """
        if config.AUGMENT_PROB <= 0.0:
            return X, y

        aug_X, aug_y = [X], [y]
        
        pattern_counts = {
            "failure_burst": 0,
            "deterioration": 0,
            "flaky": 0,
            "recovery": 0
        }

        for i in range(len(X)):
            if y[i] == 0 or np.random.rand() > config.AUGMENT_PROB:
                continue

            seq = X[i].copy()

            # Reset identifiers so the model learns the PATTERN, not the SPECIFIC test/system
            for feature in ["name", "system"]:
                idx = self.feature_index.get(feature)
                seq[:, idx] = np.random.randint(10000, 99999)

            r = np.random.rand()
            success_idx = self.feature_index["success"]

            if r < config.AUGMENT_FAILURE_RATIO:
                # Scenario: Sudden Death. 
                # Features show some success, but FUTURE is all zeros.
                self._inject_failure_burst(seq, success_idx)
                new_target = 0
                pattern_counts["failure_burst"] += 1
                
            elif r < (config.AUGMENT_FAILURE_RATIO + config.AUGMENT_DETERIORATION_RATIO):
                # Scenario: Deterioration.
                # Features show declining success, FUTURE is likely 0.
                self._inject_deterioration(seq, success_idx)
                # If the very end of the sequence is failing, the future label is 0
                new_target = 0 if seq[-1, success_idx] == 0 else 1
                pattern_counts["deterioration"] += 1

            elif r < (config.AUGMENT_FAILURE_RATIO + config.AUGMENT_DETERIORATION_RATIO + config.AUGMENT_FLAKY_RATIO):
                # Scenario: Flaky.
                # Features show a brief recovery, but FUTURE is likely 0.
                self._inject_flaky(seq, success_idx)
                new_target = 0 if seq[-1, success_idx] == 0 else 1
                pattern_counts["flaky"] += 1

            else:
                # Scenario: Recovery.
                # Features show messiness, but FUTURE is stable.
                self._inject_recovery(seq, success_idx)
                new_target = 1
                pattern_counts["recovery"] += 1

            aug_X.append(seq[np.newaxis, ...])
            aug_y.append(np.array([new_target]))

        final_X = np.concatenate(aug_X)
        final_y = np.concatenate(aug_y)

        # Calculate distribution statistics
        unique, counts = np.unique(final_y, return_counts=True)
        dist = dict(zip(unique, counts))
        total = len(final_y)

        stats["augmentation"] = {
            "total_samples": total,
            "original_samples": len(X),
            "synthetic_samples": total - len(X),
            "patterns": pattern_counts,
            "class_distribution": {
                int(k): {"count": int(v), "percent": round(float(v)/total * 100, 2)} 
                for k, v in dist.items()
            }
        }

        return final_X, final_y
 
    def _inject_failure_burst(self, seq, success_idx, burst_len=5):
        if len(seq) <= burst_len:
            return seq

        start = np.random.randint(0, len(seq) - burst_len)
        # ONLY zero out the success column
        seq[start:start+burst_len, success_idx] = 0
        # Force the tail end to be zeros to ensure the model sees the failure pattern at the prediction point
        seq[-burst_len:, success_idx] = 0

    def _inject_deterioration(self, seq, success_idx, failure_start=4):
        # Start healthy
        seq[:, success_idx] = 1

        # Apply the probabilistic drop
        for i in range(len(seq)):
            if np.random.rand() < (i / len(seq)):
                seq[i, success_idx] = 0
        
        # Force the last steps to 0
        # This ensures the model sees the deterioration at the prediction point
        seq[-failure_start:, success_idx] = 0

    def _inject_flaky(self, seq, success_idx, flaky_prob=0.5):
        # Scenario: Non-deterministic behavior. 
        # Randomly flips between 0 and 1 throughout the sequence.
        for i in range(len(seq)):
            seq[i, success_idx] = 1 if np.random.rand() > flaky_prob else 0
            
    def _inject_recovery(self, seq, success_idx, recovery_start=4):
        # Start by making the whole sequence a failure
        seq[:, success_idx] = 0 

        for i in range(len(seq)):
            # Probability of forcing a '1' increases over time
            if np.random.rand() < (i / len(seq)):
                seq[i, success_idx] = 1
        
        # Force the last steps to 1 to ensure the model sees the recovery pattern at the prediction point
        seq[-recovery_start:, success_idx] = 1

    def _update_stats_distribution(self, stats, y):
        unique, counts = np.unique(y, return_counts=True)
        dist = {str(int(k)): int(v) for k, v in zip(unique, counts)}

        stats["distribution"] = dist
        stats["total_sequences"] = len(y)

        logger.info(f"Target distribution: {dist}")

    def _compute_class_weights(self, y):
        unique = np.unique(y)

        if len(unique) < 2:
            return {0: 1.0, 1: 1.0}

        weights = class_weight.compute_class_weight(
            class_weight='balanced',
            classes=unique,
            y=y
        )

        cw = dict(zip(unique, weights))
        logger.info(f"Class weights: {cw}")
        return cw

    def _train_in_chunks(self, model, X, y, class_weights=None):
        total = len(X)
        chunk_size = config.TRAINING_CHUNKS_SIZE

        losses, accs = [], []

        for i in range(0, total, chunk_size):
            end = min(i + chunk_size, total)

            logger.info(f"Chunk {i//chunk_size + 1}: {i}-{end}")

            history = model.fit(
                X[i:end],
                y[i:end],
                epochs=config.EPOCHS,
                batch_size=config.BATCH_SIZE,
                class_weight=class_weights,
                shuffle=True,
                verbose=1,
            )

            losses.append(history.history['loss'][-1])
            accs.append(history.history['accuracy'][-1])

            gc.collect()

        return {
            "final_loss": float(np.mean(losses)) if losses else None,
            "final_accuracy": float(np.mean(accs)) if accs else None,
        }

    def _persist_artifacts(self, model, enc, scal, stats, output_dir):
        model_path = self.model_path
        metadata_path = self.metadata_path
        stats_path = os.path.join(config.MODEL_DIR, config.TRAINING_STATS)

        if output_dir:
            model_path = os.path.join(output_dir, config.MODEL_NAME)
            metadata_path = os.path.join(output_dir, config.METADATA_NAME)
            stats_path = os.path.join(output_dir, config.TRAINING_STATS)

        self._save_model(model, model_path)                
        self._save_metadata(enc, scal, metadata_path)
        self._save_stats(stats, stats_path)

    def train(self, ts_files, output_dir=None):
        if not ts_files:
            return False

        stats = self._init_stats()

        with self.training_lock:
            try:
                training_subset = self._select_training_subset(ts_files, stats)
                enc, scal = self._get_metadata()
                X_train, y_train = self._load_training_data(training_subset, enc, scal)

                if X_train is None:
                    return False

                X_train, y_train = self._augment_sequences(X_train, y_train, stats)
                self._update_stats_distribution(stats, y_train)
                class_weights = self._compute_class_weights(y_train)

                model = self.load_or_build_model(
                    input_shape=(X_train.shape[1], X_train.shape[2]),
                    output_dir=output_dir
                )

                stats.update(self._train_in_chunks(model, X_train, y_train, class_weights=class_weights))

                self._persist_artifacts(model, enc, scal, stats, output_dir)

                # Reload the LIVE model if we just did a live training (not shadow)
                if output_dir is None:
                    logger.info("Live training complete. Reloading model into memory...")
                    if not self._load_from_disk():
                        logger.error("Failed to reload model from disk after live training.")
                        return False
                else:
                    logger.info(f"Shadow training complete. Artifacts stored in {output_dir}. Skipping live reload.")

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
