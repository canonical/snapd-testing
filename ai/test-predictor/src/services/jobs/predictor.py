import json
import numpy as np
import os
import time

from flask import Flask, request, jsonify

from common import config
from common.utils import setup_logging
from common.model import ModelManager
from common.cache import SystemStateCache

logger = setup_logging("predictor-server")
app = Flask(__name__)

# Initialize the cache
app.state_cache = SystemStateCache(history_size=config.SEQUENCE_LENGTH - 1)
app.state_cache.prime_from_disk(config.PROCESSED_DIR)

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


# --- Helper: Encode a single data dictionary to a feature vector ---
def encode_to_vector(data, encoders):
    # Mapping request keys/defaults to encoder keys
    # Note: For historical data (from .ts), keys might be full names ('name' vs 'n')
    n = data.get('n') or data.get('name')
    v = data.get('v') or data.get('verb')
    l = data.get('l') or data.get('level')
    s = data.get('s') or data.get('system')
    scenario = data.get('scenario', config.DEFAULT_SCENARIO)
    attempt = float(data.get('attempt', config.DEFAULT_ATTEMPT))
    
    # success is 1 if it passed, 0 if it failed. 
    # For CURRENT prediction, we assume success=0.5 (neutral) or 1.0 (optimistic)
    # until the real result comes back via Ingestion.
    success = float(data.get('success', 1.0)) 
    duration = float(data.get('duration_ms', config.PREDICTION_DEFAULT_DURATION))

    # Actual Encoding
    n_enc = encoders['name'].transform([n])[0]
    v_enc = encoders['verb'].transform([v])[0]
    l_enc = encoders['level'].transform([l])[0]
    s_enc = encoders['system'].transform([s])[0]
    # We use scenario as is, backend we take first class if not in data
    b_val = data.get('backend', encoders['backend'].classes_[0])
    b_enc = encoders['backend'].transform([b_val])[0]
    sce_enc = encoders['scenario'].transform([scenario])[0]

    # Return the 8-feature vector
    return np.array([duration, attempt, v_enc, l_enc, b_enc, s_enc, n_enc, sce_enc], dtype='float32')


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

def audit_history(history, model_manager):
    """
    Formats the system history (last 49 tests) into a structured 
    audit record for debugging sequence-based predictions.
    """
    model, encoders, last_updated = model_manager.get_state()
    
    # Prepare the sequence descriptions
    # We want to see the readable names of what the LSTM 'remembered'
    sequence_summary = []
    
    for i, entry in enumerate(history):
        # Extract readable fields
        name = entry.get('n') or entry.get('name', 'unknown')
        verb = entry.get('v') or entry.get('verb', 'unknown')
        success = entry.get('success', 'unknown')
        
        sequence_summary.append({
            "step": i - len(history), # e.g., -49, -48...
            "name": name,
            "verb": verb,
            "result": "PASS" if str(success) == "1" else "FAIL"
        })

    # Build the audit record
    audit_record = {
        "audit_type": "sequence_context",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": {
            "last_trained": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_updated)),
            "file": os.path.basename(model_manager.model_path)
        },
        "history_length": len(history),
        "events": sequence_summary,
        "summary": " -> ".join([f"{e['name']}({e['result']})" for e in sequence_summary[-5:]]) # Last 5 for quick look
    }

    # Log to the audit file
    # We use a separate log or the main prediction log
    audit_log_path = os.path.join(config.LOGS_DIR, config.HISTORY_LOG)
    try:
        with open(audit_log_path, "a") as f:
            f.write(json.dumps(audit_record) + "\n")
    except Exception as e:
        print(f"Error writing history audit: {e}")

    return audit_record


@app.route('/internal/predict', methods=['POST'])
def predict():
    data = request.json
    system = data.get('s')
    
    model, encoders, _ = app.model_manager.get_state()
    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    # This ensures the 'Target' matches the format used in training
    normalized_target = app.state_cache._normalize_entry(data)

    # Validate
    keys_to_validate = ['n', 'v', 'l', 's', 'scenario']
    unknowns = validate_labels(normalized_target, keys_to_validate, encoders)
    if unknowns:
        return jsonify({"error": "Unknown labels", "details": unknowns}), 400

    try:
        # GET CONTEXT: Last tests for this system
        history = app.state_cache.get_context(system)

        if data.get('audit', config.DEFAULT_AUDIT):
            audit_history(history, app.model_manager)
        
        # CONSTRUCT SEQUENCE: (1, 50, 8)
        X_input = np.zeros((1, config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')
        
        # Combine history + current request
        full_sequence = history + [normalized_target]
        
        # Fill from the end (Pre-padding)
        for i, raw_item in enumerate(reversed(full_sequence)):
            if i >= config.SEQUENCE_LENGTH: 
                break
            vector = encode_to_vector(raw_item, encoders)
            X_input[0, -1 - i, :] = vector

        # PREDICT
        prediction = model.predict(X_input, verbose=config.PREDICTION_VERBOSE)
        prob = float(prediction[0][0])

        if data.get('audit', config.DEFAULT_AUDIT):
            audit_prediction(X_input, prob, normalized_target, app.model_manager)

        return jsonify({
            "probability": prob,
            "context_len": len(history)
        })

    except Exception as e:
        logger.error(f"Prediction error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/internal/update_context', methods=['POST'])
def update_context():
    """Called by Trainer Service when a real result is known."""
    data = request.json
    system = data.get('s') or data.get('system')
    if system:
        app.state_cache.update(system, data)
        return jsonify({"status": "updated"}), 200
    return jsonify({"error": "No system provided"}), 400


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
