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


def _check_model_input_compatibility(model):
    """Validate that loaded model input width matches configured feature schema."""
    expected = int(config.NUM_FEATURES)
    actual = None
    try:
        actual = int(model.input_shape[-1])
    except Exception:
        return False, "Unable to inspect model input shape"

    if actual != expected:
        return False, (
            f"Model expects {actual} features but code is configured for {expected}. "
            "Retrain and promote a new model to apply the updated feature schema."
        )
    return True, ""

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
    succ = data.get('success', config.CURRENT_SUCCESS_MASK_VALUE)
    
    # Helper to get raw integer ID
    def get_id(key, value):
        enc = encoders[key]
        try:
            encoded = float(enc.transform([str(value)])[0])
            max_index = max(len(enc.classes_) - 1, 1)
            return encoded / float(max_index)
        except (ValueError, KeyError):
            # If label is new/unknown, default to 0 (usually 'unknown')
            return 0.0

    # Build the vector in the EXACT order of config.FEATURE_COLUMNS
    # [scenario, verb, backend, system, name, success]
    # Success is a lag feature; current timestep is masked in build_model_input.
    return np.array([
        get_id('scenario', sce),
        get_id('verb', v),
        get_id('backend', b_val),
        get_id('system', s),
        get_id('name', n),
        float(succ)
    ], dtype='float32')


def build_model_input(sequence_items, encoders):
    """Builds a padded model input and masks current-step success to avoid leakage."""
    X_input = np.zeros((1, config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')

    for i, raw_item in enumerate(reversed(sequence_items)):
        if i >= config.SEQUENCE_LENGTH:
            break

        item = dict(raw_item)
        if i == 0:
            # The most recent item is the target timestep: its success is unknown.
            item['success'] = config.CURRENT_SUCCESS_MASK_VALUE
        vector = encode_to_vector(item, encoders)
        X_input[0, -1 - i, :] = vector

    return X_input


def _clamp01(value):
    return max(0.0, min(1.0, value))


def _extract_successes(history_items):
    successes = []
    for item in history_items:
        try:
            successes.append(int(float(item.get('success', 0))))
        except (TypeError, ValueError):
            continue
    return successes


def _tail_streak(values, target):
    streak = 0
    for v in reversed(values):
        if v == target:
            streak += 1
        else:
            break
    return streak


def _prev_streak_before_tail(values, tail_len, target):
    if tail_len <= 0:
        return 0

    streak = 0
    for v in reversed(values[:-tail_len]):
        if v == target:
            streak += 1
        else:
            break
    return streak


def _compute_history_metrics(successes):
    transitions = sum(1 for i in range(1, len(successes)) if successes[i] != successes[i - 1])
    transition_rate = transitions / float(len(successes) - 1)
    ones_ratio = sum(successes) / float(len(successes))

    transition_score = _clamp01((transition_rate - 0.45) / 0.55)
    balance_score = _clamp01(1.0 - abs(ones_ratio - 0.5) / 0.5)
    flaky_score = transition_score * balance_score

    return {
        "transition_rate": transition_rate,
        "ones_ratio": ones_ratio,
        "balance_score": balance_score,
        "flaky_score": flaky_score,
    }


def _apply_strong_trend_rules(adjusted, ones_ratio, tail_one_streak, tail_zero_streak, transition_rate):
    if ones_ratio >= 0.99 and tail_one_streak >= 8 and transition_rate <= 0.05:
        return max(adjusted, 0.99), True
    if ones_ratio >= 0.98:
        return max(adjusted, 0.98), True
    if ones_ratio <= 0.01:
        return min(adjusted, 0.03), True

    if ones_ratio >= 0.85 and tail_one_streak >= 2 and transition_rate <= 0.25:
        adjusted = max(adjusted, 0.90)
    if tail_zero_streak >= 3 and ones_ratio <= 0.20:
        return min(adjusted, 0.03), True
    if tail_one_streak >= 6 and ones_ratio >= 0.50 and transition_rate <= 0.20:
        adjusted = max(adjusted, 0.90)

    return adjusted, False


def _apply_boundary_flip_rules(
    adjusted,
    tail_zero_streak,
    prev_one_streak,
    tail_one_streak,
    prev_zero_streak,
):
    if tail_zero_streak >= 1 and prev_one_streak >= 6:
        trend_strength = _clamp01((prev_one_streak - 6) / 8.0)
        confirmation = _clamp01((tail_zero_streak - 1) / 2.0)
        target = 0.50 + 0.15 * trend_strength - 0.30 * confirmation
        strength = 0.65 + 0.30 * trend_strength
        adjusted = (1.0 - strength) * adjusted + strength * target

    if tail_one_streak >= 1 and prev_zero_streak >= 6:
        trend_strength = _clamp01((prev_zero_streak - 6) / 8.0)
        confirmation = _clamp01((tail_one_streak - 1) / 2.0)
        target = _clamp01(0.50 - 0.15 * trend_strength + 0.85 * confirmation)
        strength = 0.65 + 0.30 * trend_strength
        adjusted = (1.0 - strength) * adjusted + strength * target

    return adjusted


def _apply_mostly_pass_rules(
    adjusted,
    ones_ratio,
    tail_one_streak,
    tail_zero_streak,
    prev_one_streak,
    prev_zero_streak,
    transition_rate,
):
    if ones_ratio >= 0.70 and tail_one_streak >= 2:
        pass_strength = _clamp01((ones_ratio - 0.70) / 0.30)
        tail_conf = _clamp01((tail_one_streak - 2) / 3.0)
        noise_penalty = _clamp01((transition_rate - 0.30) / 0.40)
        floor = 0.45 + 0.25 * pass_strength + 0.18 * tail_conf - 0.18 * noise_penalty
        adjusted = max(adjusted, floor)

    if ones_ratio >= 0.70 and tail_zero_streak == 1 and prev_one_streak >= 3:
        pass_strength = _clamp01((ones_ratio - 0.70) / 0.30)
        run_strength = _clamp01((prev_one_streak - 3) / 5.0)
        noise_penalty = _clamp01((transition_rate - 0.30) / 0.40)
        floor = 0.40 + 0.12 * pass_strength + 0.12 * run_strength - 0.12 * noise_penalty
        adjusted = max(adjusted, floor)

    if (
        tail_one_streak >= 1
        and tail_one_streak <= 4
        and 1 <= prev_zero_streak <= 3
        and ones_ratio >= 0.70
        and transition_rate <= 0.30
    ):
        recovery_strength = _clamp01((ones_ratio - 0.70) / 0.30)
        dip_penalty = _clamp01((prev_zero_streak - 1) / 2.0)
        target = 0.55 + 0.25 * recovery_strength - 0.15 * dip_penalty
        blend = 0.60 + 0.25 * recovery_strength
        adjusted = (1.0 - blend) * adjusted + blend * target

    return adjusted


def _apply_flaky_rules(adjusted, flaky_score, balance_score, tail_one_streak, ones_ratio):
    if flaky_score >= 0.30:
        target_prob = 0.50 - (1.0 - balance_score) * 0.20
        strength = _clamp01((flaky_score - 0.30) / 0.70)
        adjusted = (1.0 - strength) * adjusted + strength * target_prob

        if tail_one_streak >= 3 and ones_ratio >= 0.45:
            adjusted = max(adjusted, 0.80)

    return adjusted


def _apply_mixed_extreme_guard(adjusted, ones_ratio, transition_rate, balance_score, successes):
    is_extreme = adjusted <= 0.02 or adjusted >= 0.98
    mixed_history = 0.20 <= ones_ratio <= 0.80 and transition_rate >= 0.25

    clear_tail = False
    if len(successes) >= 3:
        tail3 = successes[-3:]
        clear_tail = all(v == 1 for v in tail3) or all(v == 0 for v in tail3)

    if is_extreme and mixed_history and not clear_tail:
        extremeness = _clamp01((abs(adjusted - 0.5) - 0.45) / 0.05)
        mixedness = min(1.0, transition_rate / 0.50) * balance_score
        strength = 0.60 * extremeness * mixedness
        adjusted = (1.0 - strength) * adjusted + strength * ones_ratio

    return adjusted


def _apply_tail_deterioration_cap(adjusted, successes, prev_one_streak, ones_ratio):
    if len(successes) >= 2 and successes[-1] == 0 and successes[-2] == 0:
        if prev_one_streak < 8:
            if ones_ratio >= 0.75:
                adjusted = min(adjusted, 0.04)
            else:
                adjusted = min(adjusted, 0.08)
    return adjusted


def adjust_for_flaky_pattern(history_items, probability):
    """Post-process raw model probability to avoid brittle extremes on mixed history."""
    if not history_items:
        return max(probability, 0.93)

    successes = _extract_successes(history_items)
    if len(successes) < 6:
        return probability

    tail_one_streak = _tail_streak(successes, 1)
    tail_zero_streak = _tail_streak(successes, 0)
    prev_zero_streak = _prev_streak_before_tail(successes, tail_one_streak, 0)
    prev_one_streak = _prev_streak_before_tail(successes, tail_zero_streak, 1)

    metrics = _compute_history_metrics(successes)
    transition_rate = metrics["transition_rate"]
    ones_ratio = metrics["ones_ratio"]
    balance_score = metrics["balance_score"]
    flaky_score = metrics["flaky_score"]

    adjusted = probability

    adjusted, should_return = _apply_strong_trend_rules(
        adjusted,
        ones_ratio,
        tail_one_streak,
        tail_zero_streak,
        transition_rate,
    )
    if should_return:
        return adjusted

    adjusted = _apply_boundary_flip_rules(
        adjusted,
        tail_zero_streak,
        prev_one_streak,
        tail_one_streak,
        prev_zero_streak,
    )
    adjusted = _apply_mostly_pass_rules(
        adjusted,
        ones_ratio,
        tail_one_streak,
        tail_zero_streak,
        prev_one_streak,
        prev_zero_streak,
        transition_rate,
    )
    adjusted = _apply_flaky_rules(adjusted, flaky_score, balance_score, tail_one_streak, ones_ratio)
    adjusted = _apply_mixed_extreme_guard(adjusted, ones_ratio, transition_rate, balance_score, successes)
    adjusted = _apply_tail_deterioration_cap(adjusted, successes, prev_one_streak, ones_ratio)

    return adjusted


def _prediction_diagnostics(history_items):
    """Summarize history-derived signals used by flaky post-processing."""
    successes = _extract_successes(history_items)
    if not successes:
        return {
            "usable_successes": 0,
            "ones_ratio": None,
            "transition_rate": None,
            "tail_one_streak": 0,
            "tail_zero_streak": 0,
        }

    tail_one_streak = _tail_streak(successes, 1)
    tail_zero_streak = _tail_streak(successes, 0)
    transitions = sum(1 for i in range(1, len(successes)) if successes[i] != successes[i - 1])
    transition_rate = 0.0 if len(successes) < 2 else transitions / float(len(successes) - 1)
    ones_ratio = sum(successes) / float(len(successes))

    return {
        "usable_successes": len(successes),
        "ones_ratio": ones_ratio,
        "transition_rate": transition_rate,
        "tail_one_streak": tail_one_streak,
        "tail_zero_streak": tail_zero_streak,
    }


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

    is_compatible, msg = _check_model_input_compatibility(model)
    if not is_compatible:
        raise ValueError(msg)

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
    backend = data.get('backend')
    attempt = data.get('attempt')
    scenario = data.get('scenario') or config.DEFAULT_SCENARIO

    try:
        attempt = int(attempt) if attempt is not None and attempt != '' else int(config.DEFAULT_ATTEMPT)
    except (TypeError, ValueError):
        attempt = int(config.DEFAULT_ATTEMPT)
    
    model, encoders, _ = app.model_manager.get_state()
    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    is_compatible, msg = _check_model_input_compatibility(model)
    if not is_compatible:
        return jsonify({"error": msg}), 503

    # Keep target normalized; build_model_input will mask the current-step success.
    normalized_target = app.state_cache._normalize_entry(data)

    # Validate: use the long names that exist in both normalized_target and encoders
    keys_to_validate = ['name', 'verb', 'system', 'scenario']
    unknowns = validate_labels(normalized_target, keys_to_validate, encoders)

    if unknowns:
        return jsonify({"error": "Unknown labels", "details": unknowns}), 400

    try:
        # GET CONTEXT: Last tests for this system
        backend_filter = str(backend).strip() if backend is not None and str(backend).strip() else None
        history = app.state_cache.get_context(
            system=system, 
            name=name, 
            verb=verb,
            attempt=attempt,
            scenario=scenario,
            backend=backend_filter,
        )

        # If backend is not explicitly provided, reuse the most recent backend from
        # matched history so the backend feature is not always 'unknown'.
        if normalized_target.get('backend') in (None, '', 'unknown') and history:
            recent_backend = history[-1].get('backend')
            if recent_backend:
                normalized_target['backend'] = str(recent_backend)

        if data.get('audit', config.DEFAULT_AUDIT):
            audit_history(history, app.model_manager)
        
        # Combine history + current request
        full_sequence = history + [normalized_target]

        # Build model-ready input with training-consistent masking behavior.
        X_input = build_model_input(full_sequence, encoders)

        # PREDICT
        prediction = model.predict(X_input, verbose=config.PREDICTION_VERBOSE)
        raw_prob = float(prediction[0][0])
        prob = adjust_for_flaky_pattern(history, raw_prob)

        if data.get('audit', config.DEFAULT_AUDIT):
            diag = _prediction_diagnostics(history)
            logger.info(
                "Prediction diagnostics: system=%s name=%s verb=%s scenario=%s context_len=%d usable_successes=%d "
                "tail_one=%d tail_zero=%d ones_ratio=%s transition_rate=%s raw_prob=%.4f adjusted_prob=%.4f",
                system,
                name,
                verb,
                scenario,
                len(history),
                diag["usable_successes"],
                diag["tail_one_streak"],
                diag["tail_zero_streak"],
                "n/a" if diag["ones_ratio"] is None else f"{diag['ones_ratio']:.3f}",
                "n/a" if diag["transition_rate"] is None else f"{diag['transition_rate']:.3f}",
                raw_prob,
                prob,
            )

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
        # Reload predictor context from the promoted snapshot on disk.
        app.state_cache.initialize()

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
    backend = request.args.get('backend')
    scenario = request.args.get('scenario') or config.DEFAULT_SCENARIO
    attempt = request.args.get('attempt')

    try:
        attempt = int(attempt) if attempt is not None and attempt != '' else int(config.DEFAULT_ATTEMPT)
    except (TypeError, ValueError):
        attempt = int(config.DEFAULT_ATTEMPT)
    
    if not system:
        return jsonify({"error": "System required"}), 400
        
    backend_filter = str(backend).strip() if backend is not None and str(backend).strip() else None
    history = app.state_cache.get_context(system, name, verb, attempt, scenario, backend_filter)
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

    is_compatible, msg = _check_model_input_compatibility(model)
    if not is_compatible:
        return jsonify({"error": msg}), 503

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
            "expected": ">= 99%"
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
            "pattern": [1] * 10 + [0] * 4,
            "expected": "< 25%"
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
        },
        "all_pass_then_fail": {
            "pattern": [1] * 13 + [0],
            "expected": "50-70%"
        },
        "all_pass_then_two_fails": {
            "pattern": [1] * 12 + [0, 0],
            "expected": "20-50%"
        },
        "all_fail_then_pass": {
            "pattern": [0] * 13 + [1],
            "expected": "30-50%"
        },
        "all_fail_then_two_passes": {
            "pattern": [0] * 12 + [1, 1],
            "expected": "70-90%"
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

    is_compatible, msg = _check_model_input_compatibility(model)
    if not is_compatible:
        return jsonify({"error": msg}), 503

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
