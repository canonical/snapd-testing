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
    # Extract Strings/Values
    n = data.get('name', 'unknown')
    v = data.get('verb', 'unknown')
    s = data.get('system', 'unknown')
    sce = data.get('scenario', config.DEFAULT_SCENARIO)
    b_val = data.get('backend', 'unknown') # Default to unknown if missing
    
    # Scale numeric values (keep these as 0-1 range)
    attempt = float(data.get('attempt', config.DEFAULT_ATTEMPT))
    success = float(data.get('success', 1.0)) 
    
    # Helper to get raw integer ID
    def get_id(key, value):
        enc = encoders[key]
        try:
            # Transform returns the raw integer index
            return float(enc.transform([str(value)])[0])
        except (ValueError, KeyError):
            # If label is new/unknown, default to 0 (usually 'unknown')
            return 0.0

    # Build the vector in the EXACT order of config.FEATURE_COLUMNS
    # [scenario, attempt, verb, backend, system, name, success]
    return np.array([
        get_id('scenario', sce),
        attempt,
        get_id('verb', v),
        get_id('backend', b_val),
        get_id('system', s),
        get_id('name', n),
        success
    ], dtype='float32')


def build_model_input(sequence_items, encoders):
    """Builds a padded model input and masks current-step success like training."""
    X_input = np.zeros((1, config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')

    for i, raw_item in enumerate(reversed(sequence_items)):
        if i >= config.SEQUENCE_LENGTH:
            break

        item = dict(raw_item)
        # During training, the current timestep success is masked to avoid leakage.
        if i == 0:
            item['success'] = 0.0

        vector = encode_to_vector(item, encoders)
        X_input[0, -1 - i, :] = vector

    return X_input


def adjust_for_flaky_pattern(history_items, probability):
    """Post-process a raw model probability to correct for flaky or mixed history.

    The LSTM can become overconfident on sequences that lack a clear trend,
    producing near-certain 0% or 100% outputs for histories that are actually
    ambiguous. This function applies two calibration stages to bring those
    extremes back to a more defensible range.

    Stage 1 — Flaky score correction:
        A continuous flaky score is derived from two signals:
        - transition_rate: proportion of consecutive pairs that change value
          (0→1 or 1→0), normalised above a 0.45 baseline.
        - balance_score: proximity of the pass ratio to 50/50 (1.0 = perfectly
          balanced, 0.0 = all-pass or all-fail).
        When flaky_score >= 0.30 the probability is smoothly blended toward an
        uncertainty prior near 50%. The target is offset below 50% by the
        degree of imbalance (skewed-fail histories land around 30-40%).

    Stage 2 — Mixed-history extreme guard:
        Even without a strict flaky signature, the model can output near-certain
        values for mixed, transition-heavy histories that do not have a clear
        recent trend. If the adjusted probability is still extreme (≤ 2% or
        ≥ 98%), the history has both passes and failures (ones_ratio 20-80%),
        transitions are non-trivial (>= 25%), and the last three steps are NOT
        a uniform pass or fail run, the probability is further blended toward
        the empirical pass ratio of the history.

    Tail deterioration override:
        If the two most recent results are both failures, the output is hard-
        capped at 8% regardless of the earlier stages, reflecting a concrete
        recent signal of regression.

    The function is a no-op for histories shorter than 6 steps and for clearly
    stable or clearly collapsing sequences — those are intentionally left
    untouched so that real strong signals (e.g. 14× consecutive pass or 14×
    consecutive fail) are preserved.

    Args:
        history_items: List of history entry dicts, each containing at minimum
            a 'success' key with a value castable to int (0 or 1). The list
            should be ordered oldest-first.
        probability: Raw sigmoid output from the LSTM model, in [0.0, 1.0].

    Returns:
        Adjusted probability in [0.0, 1.0]. May be equal to the input if no
        correction criteria are met.
    """
    if not history_items:
        return probability

    successes = []
    for item in history_items:
        try:
            successes.append(int(float(item.get('success', 0))))
        except (TypeError, ValueError):
            continue

    if len(successes) < 6:
        return probability

    # Calculate the transition rate and balance of successes in the history.
    transitions = sum(1 for i in range(1, len(successes)) if successes[i] != successes[i - 1])
    transition_rate = transitions / float(len(successes) - 1)
    ones_ratio = sum(successes) / float(len(successes))

    # Build a continuous flaky score instead of hard thresholds.
    # - transition_score: frequent 0<->1 switches
    # - balance_score: close to a 50/50 pass/fail split
    transition_score = max(0.0, min(1.0, (transition_rate - 0.45) / 0.55))
    balance_score = max(0.0, 1.0 - abs(ones_ratio - 0.5) / 0.5)
    flaky_score = transition_score * balance_score

    adjusted = probability

    # Strong flaky signature: move toward uncertainty prior around ~50%.
    if flaky_score >= 0.30:
        target_prob = 0.50 - (1.0 - balance_score) * 0.20
        strength = max(0.0, min(1.0, (flaky_score - 0.30) / 0.70))
        adjusted = (1.0 - strength) * adjusted + strength * target_prob

    # Secondary guard for mixed histories that are not strictly flaky but where
    # the model can still jump to near-certain extremes.
    is_extreme = adjusted <= 0.02 or adjusted >= 0.98
    mixed_history = 0.20 <= ones_ratio <= 0.80 and transition_rate >= 0.25
    clear_tail = False
    if len(successes) >= 3:
        tail3 = successes[-3:]
        clear_tail = all(v == 1 for v in tail3) or all(v == 0 for v in tail3)

    if is_extreme and mixed_history and not clear_tail:
        # Blend toward empirical pass ratio in proportion to extremeness and
        # mixedness; this avoids brittle cliffs while preserving clear trends.
        extremeness = max(0.0, min(1.0, (abs(adjusted - 0.5) - 0.45) / 0.05))
        mixedness = min(1.0, transition_rate / 0.50) * balance_score
        strength = 0.60 * extremeness * mixedness
        adjusted = (1.0 - strength) * adjusted + strength * ones_ratio

    # If the immediate tail is deteriorating, bias further downward.
    if len(successes) >= 2 and successes[-1] == 0 and successes[-2] == 0:
        adjusted = min(adjusted, 0.08)

    return adjusted


def predict_from_history_pattern(base_data, pattern_values, model, encoders):
    """Build prediction input from history pattern and return adjusted probability."""
    history_items = []
    for val in pattern_values:
        entry = base_data.copy()
        entry['success'] = val
        history_items.append(app.state_cache._normalize_entry(entry))

    # Predict the NEXT run after the provided history.
    full_seq = history_items + [app.state_cache._normalize_entry(base_data)]
    X_input = build_model_input(full_seq, encoders)

    prob = float(model.predict(X_input, verbose=0)[0][0])
    prob = adjust_for_flaky_pattern(history_items, prob)
    return prob, len(history_items)


def audit_prediction(X_input, probability, params, model_manager):
    """
    Records the exact features, model metadata, and timestamp for a prediction.
    """
    model, _, last_updated = model_manager.get_state()
    
    # Extract the last timestep of the LSTM sequence (the most relevant data)
    # X_input shape is (1, sequence_length, num_features)
    current_features = X_input[0, -1, :].tolist() 
    
    # Map features back to names for readability
    feature_names = config.FEATURE_COLUMNS
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

    # Keep target normalized; build_model_input will mask the current-step success.
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
        
        # Combine history + current request
        full_sequence = history + [normalized_target]

        # Build model-ready input with training-consistent masking behavior.
        X_input = build_model_input(full_sequence, encoders)

        # PREDICT
        prediction = model.predict(X_input, verbose=config.PREDICTION_VERBOSE)
        prob = float(prediction[0][0])
        prob = adjust_for_flaky_pattern(history, prob)

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

    # Use your existing ModelManager logic to reload
    success = app.model_manager.reload_model()
    
    if success:
        logger.info("Model and cache refreshed successfully.")
        return jsonify({"status": "success", "message": "Model reloaded"}), 200
    else:
        logger.error("Failed to reload model from disk.")
        return jsonify({"status": "error", "message": "Reload failed"}), 500


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

@app.route('/internal/predict-pattern', methods=['GET'])
def get_internal_pattern():
    """Exposes the internal SystemStateCache to the external API."""
    pattern = request.args.get('pattern')
    
    # Pattern should be a comma-separated string of 0s and 1s, e.g., "1,0,1,1"
    if pattern:
        try:
            pattern_list = [int(x.strip()) for x in pattern.split(',')]
        except ValueError:
            return jsonify({"error": "Invalid pattern format. Use comma-separated 0s and 1s."}), 400
    else:
        return jsonify({"error": "Pattern query parameter is required."}), 400  

    if any(v not in (0, 1) for v in pattern_list):
        return jsonify({"error": "Pattern must contain only 0 or 1 values."}), 400
    
    base_data = {
        "name": request.args.get('name'),
        "verb": request.args.get('verb'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', config.DEFAULT_ATTEMPT),
        "scenario": request.args.get('scenario', config.DEFAULT_SCENARIO)
    }
    model, encoders, _ = app.model_manager.get_state()
    if model is None or encoders is None:
        return jsonify({"error": "Model or metadata not loaded"}), 503

    # The model predicts on [history + current_target]. Since the current target
    # consumes one timestep, only (SEQUENCE_LENGTH - 1) history items can be used.
    # Accept any pattern length and keep the most recent history that fits.
    provided_len = len(pattern_list)
    max_history_len = max(0, config.SEQUENCE_LENGTH - 1)
    used_pattern = pattern_list[-max_history_len:] if len(pattern_list) > max_history_len else pattern_list
    truncated = provided_len > len(used_pattern)

    prob, context_len = predict_from_history_pattern(base_data, used_pattern, model, encoders)
    
    return jsonify({
        "probability": prob,
        "context_len": context_len,
        "pattern_info": {
            "provided_length": provided_len,
            "used_length": len(used_pattern),
            "max_history_length": max_history_len,
            "sequence_length": config.SEQUENCE_LENGTH,
            "truncated": truncated
        }
    })


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
            # Flaky history that ends with recovery; flaky-aware scoring keeps this
            # in a medium-risk band instead of classifying as fully stable.
            "pattern": [1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1],
            "expected": "> 75%"
        },
        "flaky_alternating": {
            # Canonical flaky signal: frequent alternation with balanced outcomes.
            "pattern": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
            "expected": "45-55%"
        },
        "flaky_with_tail_fail": {
            # Mostly alternating, ending with deterioration should be even riskier.
            "pattern": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0],
            "expected": "< 10%"
        },
        "flaky_mixed_noise": {
            # Alternation with a small noisy pass streak, still flaky but less severe.
            "pattern": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1],
            "expected": "40-70%"
        },
        "recent_deterioration": {
            # Long pass streak with a very recent drop; this model predicts high risk.
            "pattern": [1] * 12 + [0, 0],
            "expected": "< 5%"
        },
        "zombie_test": {
            "pattern": [0] * 10 + [1] + [0] * 3,
            "expected": "< 10%"
        },
        "new_test_no_history": {
            # No prior history defaults to optimistic baseline on the current model.
            "pattern": [],
            "expected": "> 90%"
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

    model, encoders, _ = app.model_manager.get_state()
    if model is None or encoders is None:
        return jsonify({"error": "Model or metadata not loaded"}), 503

    for label, info in scenarios.items():
        pattern = info["pattern"]
        prob, _ = predict_from_history_pattern(base_data, pattern, model, encoders)
        
        results[label] = {
            "prediction": f"{prob * 100:.2f}%",
            "expected_range": info["expected"],
        }

    return jsonify(results)


if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
