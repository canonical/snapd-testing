import json
import os
import time

from flask import Flask, request, jsonify

from common import config
from common.utils import setup_logging
from common.cache import SystemStateCache
from services.jobs.context import register_context_endpoint

logger = setup_logging("predictor-server")
app = Flask(__name__)

# Initialize the cache
app.state_cache = SystemStateCache(history_size=config.CACHE_HISTORY_SIZE)
app.state_cache.initialize()

register_context_endpoint(app)


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
    if ones_ratio >= 0.99 and tail_one_streak >= 6 and transition_rate <= 0.05:
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
    if tail_one_streak == 1 and prev_zero_streak >= 10:
        trend_strength = _clamp01((prev_zero_streak - 10) / 6.0)
        target = 0.20 - 0.05 * trend_strength
        strength = 0.75 + 0.15 * trend_strength
        return (1.0 - strength) * adjusted + strength * target

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

    if ones_ratio >= 0.70 and tail_zero_streak == 1 and prev_one_streak >= 2:
        pass_strength = _clamp01((ones_ratio - 0.70) / 0.30)
        run_strength = _clamp01((prev_one_streak - 3) / 5.0)
        long_run_start = 10
        long_run_span = max(config.CACHE_HISTORY_SIZE - 1 - long_run_start, 1)
        long_run_strength = _clamp01((prev_one_streak - long_run_start) / long_run_span) ** 2
        noise_penalty = _clamp01((transition_rate - 0.30) / 0.40)
        floor = (
            0.55
            + 0.20 * pass_strength
            + 0.10 * run_strength
            + 0.13 * long_run_strength
            - 0.12 * noise_penalty
        )
        adjusted = max(adjusted, floor)

    if (
        tail_one_streak >= 1
        and tail_one_streak <= 4
        and 1 <= prev_zero_streak <= 3
        and ones_ratio >= 0.70
        and transition_rate <= 0.40
    ):
        recovery_strength = _clamp01((ones_ratio - 0.70) / 0.30)
        dip_penalty = _clamp01((prev_zero_streak - 1) / 2.0)
        target = 0.72 + 0.18 * recovery_strength - 0.15 * dip_penalty
        adjusted = max(adjusted, target)

    return adjusted


def _apply_flaky_rules(adjusted, flaky_score, balance_score, tail_one_streak, ones_ratio):
    if flaky_score >= 0.30:
        target_prob = 0.50 - (1.0 - balance_score) * 0.20
        strength = _clamp01((flaky_score - 0.30) / 0.70)
        adjusted = (1.0 - strength) * adjusted + strength * target_prob

        if tail_one_streak >= 3 and ones_ratio >= 0.45:
            adjusted = max(adjusted, 0.80)

    return adjusted


def _apply_recovery_rules(adjusted, ones_ratio, tail_one_streak, transition_rate):
    if tail_one_streak >= 3 and ones_ratio >= 0.40:
        history_support = _clamp01((ones_ratio - 0.40) / 0.30)
        noise_penalty = _clamp01((transition_rate - 0.55) / 0.35)
        floor = 0.60 + 0.15 * history_support - 0.15 * noise_penalty
        adjusted = max(adjusted, floor)

    return adjusted


def _apply_tail_recovery_floor(adjusted, tail_one_streak, prev_zero_streak, ones_ratio):
    sustained_reversal = tail_one_streak >= 3 and prev_zero_streak >= 6
    extended_mixed_recovery = tail_one_streak >= 6 and ones_ratio <= 0.60
    if sustained_reversal or extended_mixed_recovery:
        recovery_floor = min(0.97, 0.80 + 0.03 * (tail_one_streak - 3))
        adjusted = max(adjusted, recovery_floor)
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
    tail_zero_streak = _tail_streak(successes, 0)
    if tail_zero_streak >= 3:
        deterioration_cap = max(0.03, 0.20 - 0.03 * (tail_zero_streak - 3))
        adjusted = min(adjusted, deterioration_cap)

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
    adjusted = _apply_recovery_rules(adjusted, ones_ratio, tail_one_streak, transition_rate)
    adjusted = _apply_flaky_rules(adjusted, flaky_score, balance_score, tail_one_streak, ones_ratio)
    adjusted = _apply_mixed_extreme_guard(adjusted, ones_ratio, transition_rate, balance_score, successes)
    adjusted = _apply_tail_recovery_floor(adjusted, tail_one_streak, prev_zero_streak, ones_ratio)
    adjusted = _apply_tail_deterioration_cap(adjusted, successes, prev_one_streak, ones_ratio)

    return adjusted


def probabilistic_prediction(history_items):
    """Estimate probability from historical success ratio only, bypassing the model."""
    successes = _extract_successes(history_items)
    base_probability = (sum(successes) / len(successes)) if successes else 0.5
    return adjust_for_flaky_pattern(history_items, base_probability)


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


def predict_from_history_pattern(base_data, pattern_values):
    """Build probabilistic prediction input from a history pattern."""
    history_items = []
    for val in pattern_values:
        entry = base_data.copy()
        entry['success'] = val
        history_items.append(app.state_cache._normalize_entry(entry))

    return probabilistic_prediction(history_items), len(history_items)


def audit_history(history):
    """Record the history used for a probabilistic prediction."""
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
        "model": config.PREDICTION_MODEL,
        "history_length": len(history),
        "events": sequence_summary,
        "summary": " -> ".join([f"{e['name']}({e['result']})" for e in sequence_summary[-5:]]) # Last 5 for quick look
    }

    # Log to the audit file
    # We use a separate log or the main prediction log
    os.makedirs(config.LOGS_DIR, exist_ok=True)
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
    level = data.get('level')
    scenario = data.get('scenario') or config.DEFAULT_SCENARIO

    normalized_target = app.state_cache._normalize_entry(data)

    try:
        backend_filter = str(backend).strip() if backend is not None and str(backend).strip() else None
        history = app.state_cache.get_context(
            system=system,
            name=name,
            verb=verb,
            attempt=None,
            scenario=scenario,
            backend=backend_filter,
            max_items=config.CACHE_HISTORY_SIZE,
            level=level,
        )

        # If backend is not explicitly provided, reuse the most recent backend from
        # matched history so the backend feature is not always 'unknown'.
        if normalized_target.get('backend') in (None, '', 'unknown') and history:
            recent_backend = history[-1].get('backend')
            if recent_backend:
                normalized_target['backend'] = str(recent_backend)

        if data.get('audit', config.DEFAULT_AUDIT):
            audit_history(history)

        prob = probabilistic_prediction(history)

        if data.get('audit', config.DEFAULT_AUDIT):
            diag = _prediction_diagnostics(history)
            logger.info(
                "Prediction diagnostics: system=%s name=%s verb=%s scenario=%s context_len=%d usable_successes=%d "
                "tail_one=%d tail_zero=%d ones_ratio=%s transition_rate=%s raw_prob=%s adjusted_prob=%.4f model=%s",
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
                "n/a",
                prob,
                config.PREDICTION_MODEL,
            )

        return jsonify({
            "probability": prob,
            "context_len": len(history),
            "model": config.PREDICTION_MODEL
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
        app.state_cache.update(data)
        return jsonify({"status": "updated"}), 200
    return jsonify({"error": "No system provided"}), 400


@app.route('/internal/reload', methods=['POST'])
def reload_model():
    """Triggered by the cache service to refresh the snapshot from disk."""
    logger.info("Reload signal received. Refreshing cache...")

    reloaded_cache = SystemStateCache(history_size=app.state_cache.history_size)
    reloaded_cache.initialize()
    app.state_cache = reloaded_cache

    cache_stats = app.state_cache.stats()
    logger.info(
        "Replacement cache active: systems=%d tests=%d verb_buckets=%d "
        "entries=%d missing_provenance=%d snapshot=%s",
        cache_stats['systems'],
        cache_stats['tests'],
        cache_stats['verb_buckets'],
        cache_stats['entries'],
        cache_stats['missing_provenance'],
        app.state_cache.snapshot_path,
    )

    logger.info("Cache refreshed successfully.")
    return jsonify({"status": "success", "message": "Cache reloaded"}), 200


@app.route('/internal/list/<category>', methods=['GET'])
def list_metadata(category):
    mapping = {
        'names': 'name', 
        'verbs': 'verb', 
        'systems': 'system',
        'scenarios': 'scenario',
        'backends': 'backend',
        'levels': 'level',
    }
    
    if category not in mapping:
        return jsonify({"error": f"Invalid category. Options: {list(mapping.keys())}"}), 400

    key = mapping[category]
    values = set()
    for system, names in app.state_cache.cache.items():
        if key == 'system':
            values.add(system)
        for name, verbs in names.items():
            if key == 'name':
                values.add(name)
            for verb, history in verbs.items():
                if key == 'verb':
                    values.add(verb)
                if key in ('scenario', 'backend', 'level'):
                    values.update(str(item.get(key)) for item in history if item.get(key))

    vals = sorted(values)
    return jsonify({"category": category, "count": len(vals), "values": vals})

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
        "backend": request.args.get('backend'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', config.DEFAULT_ATTEMPT),
        "scenario": request.args.get('scenario', config.DEFAULT_SCENARIO)
    }

    # Accept any pattern length and keep the most recent history that fits.
    provided_len = len(pattern_list)
    max_history_len = config.CACHE_HISTORY_SIZE
    used_pattern = pattern_list[-max_history_len:] if len(pattern_list) > max_history_len else pattern_list
    truncated = provided_len > len(used_pattern)

    prob, context_len = predict_from_history_pattern(base_data, used_pattern)

    return jsonify({
        "probability": prob,
        "context_len": context_len,
        "model": config.PREDICTION_MODEL,
        "pattern_info": {
            "provided_length": provided_len,
            "used_length": len(used_pattern),
            "max_history_length": max_history_len,
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
        "backend": request.args.get('backend'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', config.DEFAULT_ATTEMPT),
        "scenario": request.args.get('scenario', config.DEFAULT_SCENARIO)
    }
    results = {}

    for label, info in scenarios.items():
        pattern = info["pattern"]
        prob, _ = predict_from_history_pattern(base_data, pattern)
        
        results[label] = {
            "prediction": f"{prob * 100:.2f}%",
            "expected_range": info["expected"],
        }

    return jsonify(results)


if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.PREDICTOR_PORT, threaded=True)
