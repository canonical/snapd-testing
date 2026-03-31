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
app.state_cache.initialize()

# Initialize the manager once
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
app.model_manager = ModelManager(model_full_path, metadata_full_path)
app.model_manager.load_or_build_model()

def validate_labels(params, keys_to_check, encoders):
    """
    Validates that the values in 'params' exist in the 
    corresponding LabelEncoder classes.
    """
    unknowns = []
    for k in keys_to_check:
        val = params.get(k)
        # Check if the encoder exists for this key (e.g., 'name', 'system')
        if k in encoders:
            if val not in encoders[k].classes_:
                unknowns.append(f"{k}: {val}")
        else:
            logger.warning(f"No encoder found for key: {k}")
    return unknowns

def encode_to_vector(data, encoders):
    # Extract and Normalize Strings/Values
    n = data.get('name')
    v = data.get('verb')
    s = data.get('system')
    scenario = data.get('scenario', config.DEFAULT_SCENARIO)
    
    # attempt is usually small (1, 2, 3), but you can divide by 10 for safety
    attempt = float(data.get('attempt', config.DEFAULT_ATTEMPT))
    
    # We now include the success bit (1.0 or 0.0) in the features
    # If it's a future prediction, we default to 1.0
    success = float(data.get('success', 1.0)) 

    # Encode and SCALE to 0.0 - 1.0 range
    def scale_val(key, value):
        enc = encoders[key]
        idx = enc.transform([value])[0]
        num_classes = len(enc.classes_)
        return float(idx) / (num_classes - 1) if num_classes > 1 else 0.0

    n_enc = scale_val('name', n)
    v_enc = scale_val('verb', v)
    s_enc = scale_val('system', s)
    
    b_val = data.get('backend', encoders['backend'].classes_[0])
    b_enc = scale_val('backend', b_val)
    
    sce_enc = scale_val('scenario', scenario)

    # Return the feature vector (Added Success)
    # Ensure config.NUM_FEATURES is updated to 9 in your config.py
    return np.array([
        attempt, 
        v_enc, 
        b_enc, 
        s_enc, 
        n_enc, 
        sce_enc, 
        success
    ], dtype='float32')

def audit_prediction(X_input, probability, params, model_manager):
    """
    Records the exact features, model metadata, and timestamp for a prediction.
    """
    model, _, last_updated = model_manager.get_state()
    
    # Extract the last timestep of the LSTM sequence (the most relevant data)
    # X_input shape is (1, sequence_length, num_features)
    current_features = X_input[0, -1, :].tolist() 
    
    # Map features back to names for readability
    feature_names = ['attempt', 'verb', 'backend', 'system', 'name', 'scenario', 'success']
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
    system = data.get('system')
    name = data.get('name')
    verb = data.get('verb')
    attempt = data.get('attempt', config.DEFAULT_ATTEMPT)
    scenario = data.get('scenario', config.DEFAULT_SCENARIO)
    
    model, encoders, _ = app.model_manager.get_state()
    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    # This ensures the 'Target' matches the format used in training
    normalized_target = app.state_cache._normalize_entry(data)

    # Validate: use the long names that exist in both normalized_target and encoders
    keys_to_validate = ['name', 'verb', 'system', 'scenario']
    unknowns = validate_labels(normalized_target, keys_to_validate, encoders)

    if unknowns:
        return jsonify({"error": "Unknown labels", "details": unknowns}), 400

    try:
        # GET CONTEXT: Last tests for this system
        history = app.state_cache.get_context(
            system=system, 
            name=name, 
            verb=verb,
            attempt=None, 
            scenario=scenario
        )

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
    system = data.get('system')
    if system:
        app.state_cache.update(system, data)
        return jsonify({"status": "updated"}), 200
    return jsonify({"error": "No system provided"}), 400


@app.route('/internal/reload', methods=['POST'])
def reload_model():
    """Triggered by the Trainer to refresh the model from disk."""
    logger.info("Reload signal received from Trainer. Refreshing model...")
    
    data = request.json
    backup_dir = data.get('backup_dir') 

    if not backup_dir or not os.path.isdir(backup_dir):
        logger.error(f"Invalid or missing backup directory: {backup_dir}")
        return jsonify({"status": "error", "message": "Invalid backup_dir"}), 400

    # Use your existing ModelManager logic to reload
    success = app.model_manager.reload_model()
    
    if success:
        # This ensures Step -1 matches the data just trained
        cache_restored = app.state_cache.restore_backup(backup_dir)

        if not cache_restored:
            logger.warning("Model reloaded, but cache restoration failed.")

        logger.info(f"Model and cache refreshed successfully from {backup_dir}")
        return jsonify({"status": "success", "message": "Model reloaded"}), 200
    else:
        logger.error("Failed to reload model from disk.")
        return jsonify({"status": "error", "message": "Reload failed"}), 500


# In your predictor_server.py (the one with app.model_manager)

@app.route('/internal/list/<category>', methods=['GET'])
def list_metadata(category):
    # Access the manager directly from the app instance
    _, encoders, _ = app.model_manager.get_state()
    
    if encoders is None:
        return jsonify({"error": "Metadata not loaded on server"}), 503

    mapping = {
        'names': 'name', 
        'verbs': 'verb', 
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

@app.route('/internal/context', methods=['GET'])
def get_internal_context():
    """Exposes the internal SystemStateCache to the external API."""
    # Extract keys from query params
    system = request.args.get('system')
    name = request.args.get('name')
    verb = request.args.get('verb')
    scenario = request.args.get('scenario', config.DEFAULT_SCENARIO)
    attempt = request.args.get('attempt', config.DEFAULT_ATTEMPT)
    
    if not system:
        return jsonify({"error": "System required"}), 400
        
    history = app.state_cache.get_context(system, name, verb, None, scenario)
    return jsonify({
        "system": system,
        "name": name,
        "history": history
    }), 200


@app.route('/internal/test', methods=['GET'])
def test_scenarios():
    """Tests the model against synthetic patterns and includes expected ranges."""
    scenarios = {
        "death_spiral": {
            "pattern": [1, 1] + [0] * 12,
            "expected": "< 5%"
        },
        "stable_pass": {
            "pattern": [1] * 14,
            "expected": "> 95%"
        },
        "flaky_recovery": {
            "pattern": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1],
            "expected": "60-70%"
        },
        "recent_deterioration": {
            "pattern": [1] * 12 + [0, 0],
            "expected": "30-50%"
        },
        "zombie_test": {
            "pattern": [0] * 10 + [1] + [0] * 3,
            "expected": "< 10%"
        },
        "new_test_no_history": {
            "pattern": [], 
            "expected": "80-90%"
        },
        "improving_trend": {
            "pattern": [0] * 6 + [1] * 8,
            "expected": "> 85%"
        }
    }
    
    base_data = {
        "name": request.args.get('name'),
        "verb": request.args.get('verb'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', config.DEFAULT_ATTEMPT),
        "scenario": request.args.get('scenario', config.DEFAULT_SCENARIO)
    }
    results = {}

    encoders, _ = app.model_manager._get_metadata()

    for label, info in scenarios.items():
        pattern = info["pattern"]
        fake_history = []
        for val in pattern:
            entry = base_data.copy()
            entry['success'] = val
            fake_history.append(app.state_cache._normalize_entry(entry))
            
        target = app.state_cache._normalize_entry(base_data)
        target['success'] = 0.0 
        
        full_seq = fake_history + [target]
        X_input = np.zeros((1, config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')
        
        for i, item in enumerate(reversed(full_seq)):
            if i >= config.SEQUENCE_LENGTH: break
            vector = encode_to_vector(item, encoders)
            X_input[0, -1 - i, :] = vector

        prob = float(app.model_manager.model.predict(X_input, verbose=0)[0][0])
        
        results[label] = {
            "prediction": f"{prob * 100:.2f}%",
            "expected_range": info["expected"],
        }

    return jsonify(results)


if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
