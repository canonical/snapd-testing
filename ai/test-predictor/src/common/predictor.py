import numpy as np
import os

from common.config import setup_logging

logger = setup_logging("tp-predictor")

# Force absolute isolation
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

def predict_success(model, encoders, name, verb, level, system, attempt=1, duration=0.5):
    logger.info(f"Predicting success for: name={name}, verb={verb}, level={level}, system={system}, attempt={attempt}")

    try:
        # Encode strings (using the shared encoders from ModelManager)
        n_enc = encoders['name'].transform([name])[0]
        v_enc = encoders['verb'].transform([verb])[0]
        l_enc = encoders['level'].transform([level])[0]
        s_enc = encoders['system'].transform([system])[0]
        
        # Default backend (matches your training logic)
        b_val = encoders['backend'].classes_[0]
        b_enc = encoders['backend'].transform([b_val])[0]
        
        # MATCH THE TRAINING ORDER:
        # ['duration_ms', 'attempt', 'verb', 'level', 'backend', 'system', 'name']
        features = np.array([
            float(duration), # duration_ms
            float(attempt),  # attempt
            float(v_enc),    # verb
            float(l_enc),    # level
            float(b_enc),    # backend
            float(s_enc),    # system
            float(n_enc)     # name
        ], dtype='float32')
        
        # Reshape for LSTM [samples, timesteps, features] -> [1, 1, 7]
        X_input = features.reshape(1, 1, 7)
        
        # Use verbose=0 to avoid the Gunicorn log-buffer hang
        prediction = model.predict(X_input, verbose=0)
        
        result = float(prediction[0][0])
        logger.info(f"Prediction successful: {result}")
        return result

    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        return None

