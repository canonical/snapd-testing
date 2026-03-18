import os
import pickle
import threading
import gc
import time
import logging
from tensorflow.keras.models import load_model
from tensorflow.keras import backend as K

logger = logging.getLogger("tp-model-manager")

class ModelManager:
    def __init__(self, model_path, metadata_path):
        self.model_path = model_path
        self.metadata_path = metadata_path
        self.model = None
        self.encoders = None
        self.last_updated = 0
        
        # _lock: Protects the in-memory swap (Predict vs Reload)
        self._lock = threading.Lock()
        # training_lock: Ensures only one batch/training job runs at a time
        self.training_lock = threading.Lock()

    def load_from_disk(self):
        """Thread-safe reload from the .h5 and .pkl files on disk"""
        with self._lock:
            try:
                if not os.path.exists(self.model_path) or not os.path.exists(self.metadata_path):
                    logger.warning("Model or Metadata files missing on disk.")
                    return False

                # Load to local vars first to verify integrity
                with open(self.metadata_path, 'rb') as f:
                    new_encoders, _ = pickle.load(f)
                new_model = load_model(self.model_path, compile=False)

                # Clear old session before swapping to save RAM
                if self.model is not None:
                    K.clear_session()
                    del self.model
                    gc.collect()

                self.model = new_model
                self.encoders = new_encoders
                self.last_updated = time.time()
                
                logger.info(f"ModelManager: Successfully loaded model with {len(new_encoders['system'].classes_)} systems.")
                return True
            except Exception as e:
                logger.error(f"ModelManager: Failed to load from disk: {e}")
                return False

    def get_state(self):
        """Safe access for the /predict and /list routes"""
        with self._lock:
            return self.model, self.encoders, self.last_updated
