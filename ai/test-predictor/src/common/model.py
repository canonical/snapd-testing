import gc, logging, pickle, os, time, threading, shutil, json
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

logger = logging.getLogger("tp-model-manager")

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

    def _build_or_load_model(self, input_shape):
        if os.path.exists(self.model_path):
            logger.info("Loading existing model for incremental training...")
            model = load_model(self.model_path, compile=False)
        else:
            logger.info(f"Building new model with input shape {input_shape}")
            model = Sequential([
                Input(shape=input_shape),
                LSTM(64),
                Dropout(0.2),
                Dense(32, activation='relu'),
                Dense(1, activation='sigmoid')
            ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
        return model

    def exists(self):
        return os.path.exists(self.model_path) and os.path.exists(self.metadata_path)

    def load_from_disk(self):
        with self._lock:
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

    def train(self, ts_batch_paths, processed_dir):
        """
        Expects a list of paths to .ts (CSV) files. 
        Loads -> Preprocesses -> Trains -> Saves -> Reloads In-Memory.
        """
        if not ts_batch_paths or self.training_lock.locked():
            return False

        with self.training_lock:
            try:
                # Load the TS files into DataFrames
                logger.info(f"Training model with {len(ts_batch_paths)} new TS files...")
                df_list = [pd.read_csv(p) for p in ts_batch_paths]
                combined = pd.concat(df_list, ignore_index=True)
                
                # Get existing Metadata & Preprocess
                # This ensures we use the same encoders/scaler stored on disk
                enc, scal = self._get_metadata()
                proc_df = self._preprocess_dataframe(combined, enc, scal)
                
                # Save metadata immediately after updating encoders with potential new labels
                self._save_metadata(enc, scal)
                
                # Prepare sequences and Train
                X, y = self._prepare_sequences(proc_df)
                
                # _build_or_load handles building fresh or incremental loading
                model = self._build_or_load_model((X.shape[1], X.shape[2]))
                
                logger.info(f"Starting fit on {len(X)} sequences...")
                model.fit(X, y, epochs=5, batch_size=8, verbose=0)
                model.save(self.model_path)

                # Cleanup: Archive the TS files
                for t_p in ts_batch_paths:
                    if os.path.exists(t_p):
                        shutil.move(t_p, os.path.join(processed_dir, os.path.basename(t_p)))

                logger.info("Training complete on disk. Triggering in-memory reload...")
                
                # Atomic Refresh: Sync the API's global MODEL/ENCODERS
                self.load_from_disk()
                return True

            except Exception as e:
                logger.error(f"Batch training failed: {e}", exc_info=True)
                return False

    def get_state(self):
        with self._lock:
            return self.model, self.encoders, self.last_updated
