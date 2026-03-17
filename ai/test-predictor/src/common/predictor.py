import numpy as np
import os

from common.config import setup_logging

logger = setup_logging("tp-predictor")

# Silence TF
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

def predict_success(model, encoders, name, verb, level, system, attempt=1, duration=0.5):
    logger.info(f"Predicting success for: name={name}, verb={verb}, level={level}, system={system}, attempt={attempt}")

    try:
        # 1. Encode strings to numbers
        n_enc = encoders['name'].transform([name])[0]
        v_enc = encoders['verb'].transform([verb])[0]
        l_enc = encoders['level'].transform([level])[0]
        s_enc = encoders['system'].transform([system])[0]
        
        # Default backend
        b_val = encoders['backend'].classes_[0]
        b_enc = encoders['backend'].transform([b_val])[0]
        
        # 2. Features: [duration, name, verb, level, backend, system, attempt]
        # Ensure this order matches your Training script exactly
        features = np.array([duration, n_enc, v_enc, l_enc, b_enc, s_enc, attempt], dtype='float32')
        
        # 3. Reshape for LSTM [1, 1, 7] (assuming 7 features now)
        X_input = features.reshape(1, 1, len(features))
        
        prediction = model.predict(X_input, verbose=0)
        return float(prediction[0][0])
    except Exception:
        logger.error(f"Prediction failed for input: name={name}, verb={verb}, level={level}, system={system}, attempt={attempt}")
        return None
