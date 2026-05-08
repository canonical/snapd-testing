import gc
import json
import pickle
import os
import shutil
import time
import threading
from collections import defaultdict
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


class _TrainingProgressLogger(tf.keras.callbacks.Callback):
    """Logs epoch-level progress through the service logger."""

    def __init__(self, chunk_idx, total_chunks):
        super().__init__()
        self.chunk_idx = chunk_idx
        self.total_chunks = total_chunks

    def on_epoch_end(self, epoch, logs=None):
        logs = logs or {}
        logger.info(
            "Chunk %d/%d epoch %d/%d - loss=%.6f acc=%.4f precision=%.4f recall=%.4f | val_loss=%.6f val_acc=%.4f",
            self.chunk_idx,
            self.total_chunks,
            epoch + 1,
            int(config.EPOCHS),
            float(logs.get('loss', 0.0)),
            float(logs.get('accuracy', 0.0)),
            float(logs.get('precision', 0.0)),
            float(logs.get('recall', 0.0)),
            float(logs.get('val_loss', 0.0)),
            float(logs.get('val_accuracy', 0.0)),
        )

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
            df['attempt'] = pd.to_numeric(df['attempt'], errors='coerce').fillna(1).astype('float32')

        # CATEGORICAL ENCODING
        # Note: 'success' and 'attempt' are excluded from cat_cols as they are handled above
        for col in config.ENCODED_FEATURES:
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

                encoded = encoders[col].transform(col_data).astype('float32')
                max_index = max(len(encoders[col].classes_) - 1, 1)
                df[col] = encoded / float(max_index)

        logger.info(f"Preprocessed {len(df)} rows.")
        return df

    def _prepare_sequences(self, df):
        if 'start' in df.columns:
            df['start'] = pd.to_datetime(df['start'])
            df = df.sort_values(by='start')

        feature_cols = config.FEATURE_COLUMNS
        sequences, targets = [], []
        
        for _, group in df.groupby(config.GROUPED_BY_FEATURES):
            if not all(col in group.columns for col in feature_cols):
                continue

            # Ensure chronological order within the specific test history
            group = group.sort_values(by='start')
            
            group_features = group[feature_cols].values
            group_targets = group['success'].values
            
            for i in range(len(group_features)):
                start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                window = group_features[start_idx : i + 1].copy()

                # Prevent leakage: current-step success must be unknown while
                # predicting that same step. Keep historical success values.
                success_idx = self.feature_index.get('success')
                if success_idx is not None:
                    window[-1, success_idx] = config.CURRENT_SUCCESS_MASK_VALUE

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
        """
        Selects a subset of .ts files for training based on the most recent attempt and per-backend+system+scenario limits. 
        This method implements a strategy to prioritize recent data while ensuring diversity across different backend+system+scenario combinations.
        Strategy: 1. Group files by (backend, system, scenario) composite key (extracted from metadata).
                  2. Within each (backend, system, scenario) triple, sort files by modification time (newest first).
                  3. Select up to a configured maximum number of files per (backend, system, scenario) triple.
        """
        max_per_system = int(config.TRAINING_MAX_FILES_PER_SYSTEM)

        per_backend_system_scenario_files = defaultdict(list)
        file_mtime = {}

        total_files = len(ts_files)
        for idx, ts_file in enumerate(ts_files, start=1):
            try:
                df_meta = pd.read_csv(ts_file, usecols=['system', 'backend', 'scenario'])
            except Exception as e:
                logger.warning(f"Skipping file metadata read for {ts_file}: {e}")
                continue

            if df_meta.empty or 'system' not in df_meta.columns or 'backend' not in df_meta.columns or 'scenario' not in df_meta.columns:
                continue

            # Build triples row-wise (not cartesian product) to avoid O(n^3) blowups.
            triples_df = (
                df_meta[['backend', 'system', 'scenario']]
                .dropna()
                .astype(str)
                .apply(lambda col: col.str.strip())
            )
            triples_df = triples_df[
                (triples_df['backend'] != '')
                & (triples_df['system'] != '')
                & (triples_df['scenario'] != '')
            ]

            if triples_df.empty:
                continue

            backend_system_scenario_triples = {
                (row.backend, row.system, row.scenario)
                for row in triples_df.drop_duplicates().itertuples(index=False)
            }
            if not backend_system_scenario_triples:
                continue

            mtime = os.path.getmtime(ts_file)
            file_mtime[ts_file] = mtime
            for backend, system, scenario in backend_system_scenario_triples:
                per_backend_system_scenario_files[(backend, system, scenario)].append((mtime, ts_file))

            if idx % 100 == 0 or idx == total_files:
                logger.info(
                    "Stage 1 progress: scanned %d/%d files, discovered %d backend+system+scenario groups",
                    idx,
                    total_files,
                    len(per_backend_system_scenario_files),
                )

        selected_paths = set()
        for (backend, system, scenario), files in per_backend_system_scenario_files.items():
            files.sort(key=lambda item: item[0], reverse=True)
            selected = [path for _, path in files[:max_per_system]]
            selected_paths.update(selected)
            logger.info(
                "Backend %s, System %s, Scenario %s: selected %d/%d files",
                backend,
                system,
                scenario,
                len(selected),
                len(files),
            )

        subset = sorted(selected_paths, key=lambda path: file_mtime.get(path, 0.0), reverse=True)
        subset = subset[:config.TRAINING_MAX_FILES]

        stats["files_processed"] = len(subset)

        logger.info(
            "Training using %d files (up to %d files per backend+system+scenario; attempt verified at load)",
            len(subset),
            max_per_system,
        )
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

                if 'attempt' in df.columns:
                    attempts = pd.to_numeric(df['attempt'], errors='coerce')
                    df = df[attempts == int(config.TRAINING_ATTEMPT_FILTER)].copy()
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
        STRATEGY: "Label Repetition and Mixup Augmentation"
        To combat the 80%+ success bias in real-world data without manipulating success
        as a target:
        
        1. Label Repetition: Over-sample minority class (failures) by repeating sequences
          2. Mixup: Create synthetic sequences by blending non-success characteristics from
              different sequences while preserving a realistic success-history channel.
        
        Success is a lag feature in the input, but the current timestep remains masked
        to avoid target leakage.
        
        Args:
            X (np.array): Input sequences of shape (Samples, SEQUENCE_LENGTH, NUM_FEATURES).
            y (np.array): Target labels (0 or 1).
            stats (dict): Dictionary to track augmentation counts and distributions.
            
        Returns:
            tuple: (final_X, final_y) The augmented dataset.
        """
        if config.AUGMENT_PROB <= 0.0:
            return X, y

        aug_X, aug_y = [X.copy()], [y.copy()]

        pattern_counts = {
            "label_repetition": 0,
            "mixup": 0,
            "scenario_expansion": 0,
            "stable_pass": 0,
        }

        # Find minority class indices
        fail_indices = np.where(y == 0)[0]
        pass_indices = np.where(y == 1)[0]
        minority_count = len(fail_indices)
        majority_count = len(pass_indices)
        
        # Strategy 1: Label Repetition - repeat minority class sequences
        if minority_count > 0 and config.AUGMENT_PROB > 0.0:
            target_count = int(majority_count * config.AUGMENT_PROB)
            if target_count > minority_count:
                repeat_count = target_count - minority_count
                for _ in range(repeat_count):
                    idx = fail_indices[np.random.randint(0, len(fail_indices))]
                    aug_X.append(X[idx:idx+1].copy())
                    aug_y.append(np.array([0]))
                    pattern_counts["label_repetition"] += 1
        
        # Strategy 2: Mixup - blend features from different sequences
        mixup_count = int(len(X) * config.AUGMENT_PROB * 0.3)  # 30% of augmentation is mixup
        
        success_idx = self.feature_index.get('success')

        for _ in range(mixup_count):
            if len(fail_indices) > 0 and len(pass_indices) > 0:
                # Blend a failure sequence with a pass sequence
                fail_seq = X[fail_indices[np.random.randint(0, len(fail_indices))]].copy()
                pass_seq = X[pass_indices[np.random.randint(0, len(pass_indices))]].copy()
                
                alpha = np.random.rand()
                blended = alpha * fail_seq + (1 - alpha) * pass_seq

                # Keep lagged success realistic: do not interpolate the success channel.
                # Preserve the failure sequence success history (including masked tail).
                if success_idx is not None:
                    blended[:, success_idx] = fail_seq[:, success_idx]
                
                # Target: take the failure label (we're augmenting to balance failures)
                aug_X.append(blended[np.newaxis, ...])
                aug_y.append(np.array([0]))
                pattern_counts["mixup"] += 1

        # Strategy 3: Stable pass - reinforce clean positive histories so the raw
        # model learns pass streaks without relying only on post-processing.
        if success_idx is not None and len(pass_indices) > 0:
            stable_pass_count = max(
                1,
                int(len(X) * config.AUGMENT_PROB * config.AUGMENT_STABLE_PASS_RATIO),
            )

            for _ in range(stable_pass_count):
                idx = pass_indices[np.random.randint(0, len(pass_indices))]
                pass_seq = X[idx].copy()

                active_rows = np.any(pass_seq != 0, axis=1)
                active_indices = np.where(active_rows)[0]
                if len(active_indices) == 0:
                    continue

                history_indices = active_indices[:-1]
                if len(history_indices) > 0:
                    pass_seq[history_indices, success_idx] = 1.0

                # Preserve the no-leakage contract for the target timestep.
                pass_seq[active_indices[-1], success_idx] = config.CURRENT_SUCCESS_MASK_VALUE

                aug_X.append(pass_seq[np.newaxis, ...])
                aug_y.append(np.array([1]))
                pattern_counts["stable_pass"] += 1

        # Strategy 4: Scenario expansion - clone sequences while swapping scenario ID
        # to broaden scenario coverage without altering label semantics.
        scenario_idx = self.feature_index.get('scenario')
        if scenario_idx is not None and len(X) > 0:
            scenario_values = np.unique(X[:, :, scenario_idx])
            scenario_values = [float(v) for v in scenario_values if np.isfinite(v)]

            if len(scenario_values) > 1:
                scenario_count = int(len(X) * config.AUGMENT_PROB * config.AUGMENT_SCENARIO_EXPANSION_RATIO)

                for _ in range(scenario_count):
                    base_idx = np.random.randint(0, len(X))
                    base_seq = X[base_idx].copy()

                    # Keep pre-padding untouched by changing only rows that carry signal.
                    active_rows = np.any(base_seq != 0, axis=1)
                    if not np.any(active_rows):
                        continue

                    current_vals = np.unique(base_seq[active_rows, scenario_idx])
                    if len(current_vals) == 0:
                        continue

                    current_val = float(current_vals[-1])
                    candidate_vals = [v for v in scenario_values if v != current_val]
                    if not candidate_vals:
                        continue

                    sampled_val = candidate_vals[np.random.randint(0, len(candidate_vals))]
                    base_seq[active_rows, scenario_idx] = sampled_val

                    aug_X.append(base_seq[np.newaxis, ...])
                    aug_y.append(np.array([y[base_idx]]))
                    pattern_counts["scenario_expansion"] += 1
        
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
 
    def _inject_failure_burst(self, seq, success_idx, burst_len=3):
        if len(seq) <= burst_len:
            return seq

        start = np.random.randint(0, len(seq) - burst_len)
        # ONLY zero out the success column
        seq[start:start+burst_len, success_idx] = 0
        # Force the tail end to be zeros to ensure the model sees the failure pattern at the prediction point
        seq[-burst_len:, success_idx] = 0

    def _inject_deterioration(self, seq, success_idx, failure_start=3):
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

    def _inject_stable_pass(self, seq, success_idx):
        # Enforce a clean, stable pass history to preserve positive streak behavior.
        seq[:, success_idx] = 1

    def _update_stats_distribution(self, stats, y):
        unique, counts = np.unique(y, return_counts=True)
        dist = {str(int(k)): int(v) for k, v in zip(unique, counts)}

        stats["distribution"] = dist
        stats["total_sequences"] = len(y)

        logger.info(f"Target distribution: {dist}")

    def _compute_class_weights(self, y):
        unique = np.unique(y)

        if len(unique) < 2:
            return {0: config.WEIGHT_NEGATIVE_CLASS, 1: config.WEIGHT_POSITIVE_CLASS}

        if config.WEIGHT_CLASS == 'balanced':
            weights = class_weight.compute_class_weight(
                class_weight=config.WEIGHT_CLASS,
                classes=unique,
                y=y
            )
            cw = dict(zip(unique, weights))
            logger.info(f"Class weights: {cw}")
            return cw
        else:
            cw = {0: config.WEIGHT_NEGATIVE_CLASS, 1: config.WEIGHT_POSITIVE_CLASS}
            logger.info(f"Using fixed class weights: {cw}")
            return cw

    def _train_in_chunks(self, model, X, y, class_weights=None):
        total = len(X)
        chunk_size = config.TRAINING_CHUNKS_SIZE
        total_chunks = max(1, (total + chunk_size - 1) // chunk_size)

        # Shuffle once globally so each chunk is class-mixed and validation_split
        # does not accidentally operate on near single-class segments.
        if total > 1:
            perm = np.random.permutation(total)
            X = X[perm]
            y = y[perm]

        losses, accs = [], []

        for i in range(0, total, chunk_size):
            end = min(i + chunk_size, total)
            chunk_idx = i // chunk_size + 1
            chunk_start = time.time()

            logger.info(
                "Starting chunk %d/%d (%d samples: [%d, %d))",
                chunk_idx,
                total_chunks,
                end - i,
                i,
                end,
            )

            y_chunk = y[i:end]
            uniq, cnt = np.unique(y_chunk, return_counts=True)
            chunk_dist = {int(k): int(v) for k, v in zip(uniq, cnt)}
            logger.info("Chunk %d/%d class distribution: %s", chunk_idx, total_chunks, chunk_dist)

            if len(uniq) < 2:
                logger.warning(
                    "Chunk %d/%d has a single class (%s); precision/recall may be zero while accuracy is high.",
                    chunk_idx,
                    total_chunks,
                    int(uniq[0]),
                )

            early_stop = tf.keras.callbacks.EarlyStopping(
                monitor='val_loss',
                patience=int(config.TRAINING_EARLY_STOPPING_PATIENCE),
                restore_best_weights=True,
                verbose=0,
            )
            history = model.fit(
                X[i:end],
                y[i:end],
                epochs=config.EPOCHS,
                batch_size=config.BATCH_SIZE,
                class_weight=class_weights,
                shuffle=True,
                verbose=config.TRAINING_VERBOSE,
                validation_split=float(config.TRAINING_VALIDATION_SPLIT),
                callbacks=[_TrainingProgressLogger(chunk_idx, total_chunks), early_stop],
            )

            losses.append(history.history['loss'][-1])
            accs.append(history.history['accuracy'][-1])

            logger.info(
                "Finished chunk %d/%d in %.1fs",
                chunk_idx,
                total_chunks,
                time.time() - chunk_start,
            )

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
        train_start = time.time()

        with self.training_lock:
            try:
                logger.info("Train stage 1/6: selecting training subset from %d files", len(ts_files))
                training_subset = self._select_training_subset(ts_files, stats)

                logger.info("Train stage 2/6: loading and preprocessing data from %d files", len(training_subset))
                enc, scal = self._get_metadata()
                X_train, y_train = self._load_training_data(training_subset, enc, scal)

                if X_train is None:
                    logger.warning("Train aborted: no sequences prepared from selected files.")
                    return False

                logger.info("Train stage 3/6: augmenting %d sequences", len(y_train))
                X_train, y_train = self._augment_sequences(X_train, y_train, stats)
                aug_stats = stats.get("augmentation", {})
                logger.info(
                    "Augmentation summary: original=%s synthetic=%s total=%s patterns=%s",
                    aug_stats.get("original_samples", len(y_train)),
                    aug_stats.get("synthetic_samples", 0),
                    aug_stats.get("total_samples", len(y_train)),
                    aug_stats.get("patterns", {}),
                )

                logger.info("Train stage 4/6: computing class distribution/weights")
                self._update_stats_distribution(stats, y_train)
                class_weights = self._compute_class_weights(y_train)

                logger.info("Train stage 5/6: building/loading model and fitting")
                model = self.load_or_build_model(
                    input_shape=(X_train.shape[1], X_train.shape[2]),
                    output_dir=output_dir
                )

                stats.update(self._train_in_chunks(model, X_train, y_train, class_weights=class_weights))

                logger.info("Train stage 6/6: persisting artifacts")
                self._persist_artifacts(model, enc, scal, stats, output_dir)

                # Reload the LIVE model if we just did a live training (not shadow)
                if output_dir is None:
                    logger.info("Live training complete. Reloading model into memory...")
                    if not self._load_from_disk():
                        logger.error("Failed to reload model from disk after live training.")
                        return False
                else:
                    logger.info(f"Shadow training complete. Artifacts stored in {output_dir}. Skipping live reload.")

                logger.info("Training completed successfully in %.1fs", time.time() - train_start)

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
