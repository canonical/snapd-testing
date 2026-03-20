import os
import tensorflow as tf
import numpy as np
from flask import Flask, request, jsonify

from common import config
from common.utils import setup_logging
from common.model import ModelManager

logger = setup_logging("predictor-server")
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
        sce_enc = encoders['scenario'].transform([data.get('scenario', config.DEFAULT_SCENARIO)])[0]
        attempt = float(data.get('attempt', config.DEFAULT_ATTEMPT))

        features = np.array([0.5, attempt, v_enc, l_enc, b_enc, s_enc, n_enc, sce_enc], dtype='float32')

        # Create a buffer of SEQUENCE_LENGTH timesteps (all zeros)
        X_input = features.reshape(1, config.SEQUENCE_LENGTH, config.NUM_FEATURES)
        # Place the current features at the very last timestep (index SEQUENCE_LENGTH - 1)
        X_input[0, -1, :] = features

        prediction = model.predict(X_input, verbose=config.PREDICTION_VERBOSE)
        logger.info(f"Prediction result for {data}: {prediction[0][0]}")

        return jsonify({"probability": float(prediction[0][0])})
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/internal/reload', methods=['POST'])
def reload_model():
    """Triggered by the Trainer to refresh the model from disk."""
    logger.info("Reload signal received from Trainer. Refreshing model...")
    
    # Use your existing ModelManager logic to reload
    success = app.model_manager.load_or_build_model()
    
    if success:
        logger.info("Model refreshed successfully.")
        return jsonify({"status": "success", "message": "Model reloaded"}), 200
    else:
        logger.error("Failed to reload model from disk.")
        return jsonify({"status": "error", "message": "Reload failed"}), 500


if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
