import os
import tensorflow as tf
import numpy as np
import pickle
from flask import Flask, request, jsonify

from common import config
from common.config import setup_logging
from common.model import ModelManager

logger = setup_logging("tp-predictor")
app = Flask(__name__)

# Initialize the manager once
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
app.model_manager = ModelManager(model_full_path, metadata_full_path)
app.model_manager.load_or_build_model()

@app.route('/internal/predict', methods=['POST'])
def predict():
    data = request.json
    logger.info(f"Received prediction request: {data}")

    model, encoders, _ = app.model_manager.get_state()
    try:
        # Transformation logic (using the loaded 'encoders')
        n_enc = encoders['name'].transform([data['n']])[0]
        v_enc = encoders['verb'].transform([data['v']])[0]
        l_enc = encoders['level'].transform([data['l']])[0]
        s_enc = encoders['system'].transform([data['s']])[0]
        b_enc = encoders['backend'].transform([encoders['backend'].classes_[0]])[0]

        features = np.array([0.5, float(data['attempt']), v_enc, l_enc, b_enc, s_enc, n_enc], dtype='float32')
        X_input = features.reshape(1, 1, 7)
        
        prediction = model.predict(X_input, verbose=0)
        logger.info(f"Prediction result for {data}: {prediction[0][0]}")

        return jsonify({"probability": float(prediction[0][0])})
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
