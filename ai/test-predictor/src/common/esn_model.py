"""
Echo State Network (ESN) for spread test outcome prediction.

Acts as a second source of truth alongside the LSTM model. The reservoir
is a fixed random recurrent network; only the output (readout) weights
are trained via ridge regression, making training orders of magnitude
faster than backpropagation through time.
"""

import json
import os
import pickle
import threading
import time

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import LabelEncoder

from common import config
from common.utils import setup_logging

logger = setup_logging("esn-model")


class EchoStateNetwork:
    """Leaky-integrator Echo State Network with ridge-regression readout."""

    def __init__(
        self,
        input_size=config.NUM_FEATURES,
        reservoir_size=config.ESN_RESERVOIR_SIZE,
        spectral_radius=config.ESN_SPECTRAL_RADIUS,
        leaking_rate=config.ESN_LEAKING_RATE,
        input_scaling=config.ESN_INPUT_SCALING,
        ridge_alpha=config.ESN_RIDGE_ALPHA,
        seed=config.ESN_SEED,
    ):
        self.input_size = input_size
        self.reservoir_size = reservoir_size
        self.spectral_radius = spectral_radius
        self.leaking_rate = leaking_rate
        self.input_scaling = input_scaling
        self.ridge_alpha = ridge_alpha
        self.seed = seed

        self._rng = np.random.default_rng(seed)
        self.W_in = None  # Input-to-reservoir weights
        self.W_res = None  # Reservoir recurrent weights
        self.readout = None  # Trained Ridge regressor

        self._init_reservoir()

    def _init_reservoir(self):
        """Initialize fixed random reservoir weights."""
        # Input weights: sparse, scaled
        self.W_in = self._rng.uniform(
            -self.input_scaling, self.input_scaling,
            size=(self.reservoir_size, self.input_size)
        )

        # Reservoir weights: sparse random matrix scaled to spectral_radius
        density = min(10.0 / self.reservoir_size, 1.0)
        W = self._rng.standard_normal((self.reservoir_size, self.reservoir_size))
        mask = self._rng.random((self.reservoir_size, self.reservoir_size)) < density
        W *= mask

        # Scale to desired spectral radius
        eigenvalues = np.linalg.eigvals(W)
        max_eigenvalue = np.max(np.abs(eigenvalues))
        if max_eigenvalue > 0:
            W = W * (self.spectral_radius / max_eigenvalue)

        self.W_res = W
        logger.info(
            "Reservoir initialized: size=%d, spectral_radius=%.3f, leaking_rate=%.3f",
            self.reservoir_size, self.spectral_radius, self.leaking_rate,
        )

    def _run_reservoir(self, X_sequence):
        """
        Drive the reservoir with an input sequence.

        Args:
            X_sequence: np.ndarray of shape (seq_len, input_size)

        Returns:
            Final reservoir state vector of shape (reservoir_size,)
        """
        state = np.zeros(self.reservoir_size)
        for t in range(X_sequence.shape[0]):
            pre_activation = (
                self.W_in @ X_sequence[t] + self.W_res @ state
            )
            state = (
                (1 - self.leaking_rate) * state
                + self.leaking_rate * np.tanh(pre_activation)
            )
        return state

    def _collect_states(self, X_sequences):
        """
        Collect final reservoir states for a batch of sequences.

        Args:
            X_sequences: np.ndarray of shape (n_samples, seq_len, input_size)

        Returns:
            np.ndarray of shape (n_samples, reservoir_size)
        """
        n_samples = X_sequences.shape[0]
        states = np.zeros((n_samples, self.reservoir_size))
        for i in range(n_samples):
            states[i] = self._run_reservoir(X_sequences[i])
        return states

    def train(self, X_sequences, y_targets, validation_split=None):
        """
        Train the readout layer via ridge regression.

        Args:
            X_sequences: np.ndarray of shape (n_samples, seq_len, input_size)
            y_targets: np.ndarray of shape (n_samples,) with values in {0, 1}
            validation_split: float, fraction of data to hold out for validation (e.g., 0.15)

        Returns:
            dict with training metrics
        """
        if validation_split and validation_split > 0:
            n = len(X_sequences)
            split_idx = int(n * (1 - validation_split))
            # Shuffle before splitting
            perm = self._rng.permutation(n)
            X_sequences = X_sequences[perm]
            y_targets = y_targets[perm]

            X_train, X_val = X_sequences[:split_idx], X_sequences[split_idx:]
            y_train, y_val = y_targets[:split_idx], y_targets[split_idx:]
        else:
            X_train, y_train = X_sequences, y_targets
            X_val, y_val = None, None

        logger.info("Collecting reservoir states for %d training sequences...", len(X_train))
        states_train = self._collect_states(X_train)

        logger.info("Fitting ridge regression readout (alpha=%.4f)...", self.ridge_alpha)
        self.readout = Ridge(alpha=self.ridge_alpha)
        self.readout.fit(states_train, y_train)

        # Compute training accuracy
        train_preds = self.readout.predict(states_train)
        train_binary = (train_preds >= 0.5).astype(int)
        train_accuracy = np.mean(train_binary == y_train.astype(int))

        stats = {
            "train_accuracy": float(train_accuracy),
            "n_train_samples": len(y_train),
        }

        # Compute validation accuracy if split was used
        if X_val is not None and len(X_val) > 0:
            logger.info("Collecting reservoir states for %d validation sequences...", len(X_val))
            states_val = self._collect_states(X_val)
            val_preds = self.readout.predict(states_val)
            val_binary = (val_preds >= 0.5).astype(int)
            val_accuracy = np.mean(val_binary == y_val.astype(int))
            stats["val_accuracy"] = float(val_accuracy)
            stats["n_val_samples"] = len(y_val)
            logger.info(
                "ESN training complete: train_acc=%.4f val_acc=%.4f on %d/%d samples",
                train_accuracy, val_accuracy, len(y_train), len(y_val),
            )
        else:
            logger.info("ESN training complete: train_acc=%.4f on %d samples", train_accuracy, len(y_train))

        return stats

    def predict(self, X_sequence):
        """
        Predict success probability for a single sequence.

        Args:
            X_sequence: np.ndarray of shape (seq_len, input_size) or (1, seq_len, input_size)

        Returns:
            float: predicted probability clamped to [0, 1]
        """
        if self.readout is None:
            raise RuntimeError("ESN readout not trained yet")

        if X_sequence.ndim == 3:
            X_sequence = X_sequence[0]

        state = self._run_reservoir(X_sequence)
        raw = float(self.readout.predict(state.reshape(1, -1))[0])
        return max(0.0, min(1.0, raw))

    def predict_batch(self, X_sequences):
        """
        Predict success probabilities for a batch of sequences.

        Args:
            X_sequences: np.ndarray of shape (n_samples, seq_len, input_size)

        Returns:
            np.ndarray of shape (n_samples,) clamped to [0, 1]
        """
        if self.readout is None:
            raise RuntimeError("ESN readout not trained yet")

        states = self._collect_states(X_sequences)
        raw = self.readout.predict(states)
        return np.clip(raw, 0.0, 1.0)

    def get_flakiness_score(self, X_sequences):
        """
        Compute flakiness score from reservoir state variance over a window.

        Higher variance in reservoir activations across recent runs indicates
        unstable/flaky behavior.

        Args:
            X_sequences: np.ndarray of shape (n_runs, seq_len, input_size)
                         Recent run sequences for a single test×system pair.

        Returns:
            float: flakiness score in [0, 1]
        """
        if X_sequences.shape[0] < 2:
            return 0.0

        states = self._collect_states(X_sequences)
        # Variance of each reservoir neuron across runs, then mean
        variance = np.mean(np.var(states, axis=0))
        # Normalize: tanh squashes to [0, 1) range
        return float(np.tanh(variance * config.ESN_FLAKINESS_SENSITIVITY))


class ESNManager:
    """Manages the ESN lifecycle: training, saving, loading, prediction."""

    def __init__(self, model_path=None, metadata_path=None):
        if model_path is None:
            model_path = os.path.join(config.MODEL_DIR, config.ESN_MODEL_NAME)
        if metadata_path is None:
            metadata_path = os.path.join(config.MODEL_DIR, config.ESN_METADATA_NAME)

        self.model_path = model_path
        self.metadata_path = metadata_path
        self.esn = None
        self.encoders = None
        self.last_updated = 0
        self.feature_index = {name: i for i, name in enumerate(config.FEATURE_COLUMNS)}
        self._lock = threading.RLock()
        self.training_lock = threading.Lock()

    def exists(self):
        return os.path.exists(self.model_path) and os.path.exists(self.metadata_path)

    def save(self, output_dir=None):
        """Save ESN (reservoir weights + readout) and metadata to disk."""
        model_path = self.model_path
        metadata_path = self.metadata_path

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            model_path = os.path.join(output_dir, config.ESN_MODEL_NAME)
            metadata_path = os.path.join(output_dir, config.ESN_METADATA_NAME)

        with self._lock:
            with open(model_path, 'wb') as f:
                pickle.dump({
                    'W_in': self.esn.W_in,
                    'W_res': self.esn.W_res,
                    'readout': self.esn.readout,
                    'reservoir_size': self.esn.reservoir_size,
                    'input_size': self.esn.input_size,
                    'spectral_radius': self.esn.spectral_radius,
                    'leaking_rate': self.esn.leaking_rate,
                    'input_scaling': self.esn.input_scaling,
                }, f)

            with open(metadata_path, 'wb') as f:
                pickle.dump(self.encoders, f)

        self.last_updated = time.time()
        logger.info("ESN saved to %s", model_path)

    def load(self):
        """Load ESN from disk."""
        with self._lock:
            try:
                if not self.exists():
                    return False

                with open(self.model_path, 'rb') as f:
                    data = pickle.load(f)

                self.esn = EchoStateNetwork(
                    input_size=data['input_size'],
                    reservoir_size=data['reservoir_size'],
                    spectral_radius=data['spectral_radius'],
                    leaking_rate=data['leaking_rate'],
                    input_scaling=data['input_scaling'],
                )
                # Restore trained weights
                self.esn.W_in = data['W_in']
                self.esn.W_res = data['W_res']
                self.esn.readout = data['readout']

                with open(self.metadata_path, 'rb') as f:
                    self.encoders = pickle.load(f)

                self.last_updated = time.time()
                logger.info("ESN loaded from disk successfully.")
                return True

            except Exception as e:
                logger.error("ESN load failed: %s", e)
                return False

    def get_state(self):
        """Returns (esn, encoders, last_updated) tuple."""
        with self._lock:
            return self.esn, self.encoders, self.last_updated

    def train(self, ts_files, encoders=None, output_dir=None):
        """
        Train the ESN from .ts time-series files (same format as LSTM).

        Uses the same preprocessing as the LSTM ModelManager to ensure
        feature encoding consistency.

        Args:
            ts_files: list of .ts file paths
            encoders: optional pre-fitted LabelEncoders (shared with LSTM)
            output_dir: optional directory to save the trained model

        Returns:
            bool indicating success
        """
        import pandas as pd
        from sklearn.preprocessing import LabelEncoder, MinMaxScaler
        from keras.utils import pad_sequences

        if not self.training_lock.acquire(blocking=False):
            logger.warning("ESN training already in progress.")
            return False

        try:
            logger.info("ESN training started with %d files.", len(ts_files))

            # Load and concatenate all .ts files
            frames = []
            for fpath in ts_files:
                try:
                    df = pd.read_csv(fpath)
                    frames.append(df)
                except Exception as e:
                    logger.warning("Skipping file %s: %s", fpath, e)
                    continue

            if not frames:
                logger.error("No valid .ts files loaded.")
                return False

            df = pd.concat(frames, ignore_index=True)
            logger.info("Loaded %d total rows from %d files.", len(df), len(frames))

            # Use shared encoders or build new ones
            if encoders is None:
                encoders = {}

            # Preprocess (matches LSTM pipeline)
            if 'success' in df.columns:
                df['success'] = pd.to_numeric(df['success'], errors='coerce').fillna(0)
                df['success'] = (df['success'] >= 1).astype('float32')

            for col in config.ENCODED_FEATURES:
                if col in df.columns:
                    col_data = df[col].astype(str).fillna("unknown")
                    if col not in encoders:
                        encoders[col] = LabelEncoder()
                        encoders[col].fit(col_data.unique())
                    else:
                        existing = encoders[col].classes_
                        new_labels = sorted([l for l in col_data.unique() if l not in existing])
                        if new_labels:
                            encoders[col].classes_ = np.concatenate([existing, new_labels])

                    encoded = encoders[col].transform(col_data).astype('float32')
                    max_index = max(len(encoders[col].classes_) - 1, 1)
                    df[col] = encoded / float(max_index)

            # Prepare sequences (same grouping as LSTM)
            feature_cols = config.FEATURE_COLUMNS
            if 'start' in df.columns:
                df['start'] = pd.to_datetime(df['start'])
                df = df.sort_values(by='start')

            sequences, targets = [], []
            for _, group in df.groupby(config.GROUPED_BY_FEATURES):
                if not all(col in group.columns for col in feature_cols):
                    continue

                group = group.sort_values(by='start') if 'start' in group.columns else group
                group_features = group[feature_cols].values
                group_targets = group['success'].values

                for i in range(len(group_features)):
                    start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
                    window = group_features[start_idx:i + 1].copy()

                    # Mask current-step success (same as LSTM)
                    success_idx = self.feature_index.get('success')
                    if success_idx is not None:
                        window[-1, success_idx] = config.CURRENT_SUCCESS_MASK_VALUE

                    sequences.append(window)
                    targets.append(group_targets[i])

            if not sequences:
                logger.error("No sequences produced from data.")
                return False

            X = pad_sequences(sequences, maxlen=config.SEQUENCE_LENGTH, padding='pre', dtype='float32')
            y = np.array(targets, dtype='float32')

            logger.info("Prepared %d sequences for ESN training.", len(X))

            # Initialize and train ESN
            self.esn = EchoStateNetwork(input_size=config.NUM_FEATURES)
            stats = self.esn.train(X, y, validation_split=config.TRAINING_VALIDATION_SPLIT)
            self.encoders = encoders

            # Save
            save_dir = output_dir if output_dir else None
            self.save(output_dir=save_dir)

            # Also save to root model dir if output_dir was a shadow
            if output_dir:
                self.save()

            logger.info("ESN training complete: %s", stats)
            return True

        except Exception as e:
            logger.error("ESN training failed: %s", e, exc_info=True)
            return False
        finally:
            self.training_lock.release()
