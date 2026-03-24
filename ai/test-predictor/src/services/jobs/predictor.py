import json
import numpy as np
import os
import time

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


def validate_labels(params, keys_to_check, encoders):
    # Mapping request keys to internal encoder keys
    mapping = {
        'n': 'name', 
        'v': 'verb', 
        'l': 'level', 
        's': 'system', 
        'scenario': 'scenario'
    }
    unknowns = []
    for k in keys_to_check:
        # Get value, defaulting to config if it's the scenario key
        val = params.get(k)
        if k == 'scenario' and not val:
            val = config.DEFAULT_SCENARIO
            
        if val not in encoders[mapping[k]].classes_:
            unknowns.append(f"{mapping[k]}: {val}")
    return unknowns


def audit_prediction(X_input, probability, params, model_manager):
    """
    Records the exact features, model metadata, and timestamp for a prediction.
    """
    model, _, last_updated = model_manager.get_state()
    
    # Extract the last timestep of the LSTM sequence (the most relevant data)
    # X_input shape is (1, sequence_length, num_features)
    current_features = X_input[0, -1, :].tolist() 
    
    # Map features back to names for readability
    feature_names = ['duration_ms', 'attempt', 'verb', 'level', 'backend', 'system', 'name', 'scenario']
    feature_map = dict(zip(feature_names, current_features))

    # Build the audit record
    audit_record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_info": {
            "last_trained": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_updated)),
            "model_file": os.path.basename(model_manager.model_path)
        },
        "request_params": params,       # Original strings from the API request
        "model_input_raw": feature_map, # The actual encoded/scaled numbers fed to LSTM
        "prediction": {
            "success_probability": float(probability),
            "verdict": "pass" if probability > 0.5 else "fail"
        }
    }

    # Save to a rolling log file
    audit_log_path = os.path.join(config.LOGS_DIR, config.PREDICTION_LOG)
    with open(audit_log_path, "a") as f:
        f.write(json.dumps(audit_record) + "\n")

    return audit_record

@app.route('/internal/predict', methods=['POST'])
def predict():
    data = request.json
    logger.info(f"Received prediction request: {data}")

    model, encoders, _ = app.model_manager.get_state()

    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    # Validate incoming labels before transforming
    keys_to_validate = ['n', 'v', 'l', 's', 'scenario']
    unknowns = validate_labels(data, keys_to_validate, encoders)
    if unknowns:
        logger.warning(f"Validation failed: {unknowns}")
        return jsonify({"error": "Unknown labels", "details": unknowns}), 400

    try:
        # Transformation logic (using the loaded 'encoders')
        n_enc = encoders['name'].transform([data['n']])[0]
        v_enc = encoders['verb'].transform([data['v']])[0]
        l_enc = encoders['level'].transform([data['l']])[0]
        s_enc = encoders['system'].transform([data['s']])[0]
        b_enc = encoders['backend'].transform([encoders['backend'].classes_[0]])[0]
        sce_enc = encoders['scenario'].transform([data.get('scenario', config.DEFAULT_SCENARIO)])[0]
        attempt = float(data.get('attempt', config.DEFAULT_ATTEMPT))

        # Create the flat feature vector (size 8)
        features = np.array([config.PREDICTION_DEFAULT_DURATION, attempt, v_enc, l_enc, b_enc, s_enc, n_enc, sce_enc], dtype='float32')

        # Initialize a buffer of (1, 50, 8) with zeros
        # This creates the 50 timesteps the model expects
        X_input = np.zeros((1, config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')

        # Place the 8 features into the VERY LAST timestep (index SEQUENCE_LENGTH - 1)
        X_input[0, -1, :] = features

        prediction = model.predict(X_input, verbose=config.PREDICTION_VERBOSE)
        logger.info(f"Prediction result for {data}: {prediction[0][0]}")

        if data.get('audit', config.DEFAULT_AUDIT):
            audit_prediction(X_input, prediction[0][0], data, app.model_manager)

        return jsonify({"probability": float(prediction[0][0])})
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/internal/reload', methods=['POST'])
def reload_model():
    """Triggered by the Trainer to refresh the model from disk."""
    logger.info("Reload signal received from Trainer. Refreshing model...")
    
    # Use your existing ModelManager logic to reload
    success = app.model_manager.reload_model()
    
    if success:
        logger.info("Model refreshed successfully.")
        return jsonify({"status": "success", "message": "Model reloaded"}), 200
    else:
        logger.error("Failed to reload model from disk.")
        return jsonify({"status": "error", "message": "Reload failed"}), 500


# In your predictor_server.py (the one with app.model_manager)

@app.route('/internal/list/<category>', methods=['GET'])
def list_metadata(category):
    # Access the manager directly from the app instance
    model, encoders, _ = app.model_manager.get_state()
    
    if encoders is None:
        return jsonify({"error": "Metadata not loaded on server"}), 503

    mapping = {
        'names': 'name', 
        'verbs': 'verb', 
        'levels': 'level', 
        'systems': 'system',
        'scenarios': 'scenario' 
    }
    
    if category not in mapping:
        return jsonify({"error": f"Invalid category. Options: {list(mapping.keys())}"}), 400

    try:
        # Get classes from the specific LabelEncoder
        vals = list(encoders[mapping[category]].classes_)
        return jsonify({
            "category": category, 
            "count": len(vals), 
            "values": vals
        })
    except KeyError:
        return jsonify({"error": f"Encoder for {category} not found"}), 500


if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
