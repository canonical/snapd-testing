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


def _extract_successes(history_items):
    successes = []
    for item in history_items:
        try:
            successes.append(int(float(item.get('success', 0))))
        except (TypeError, ValueError):
            continue
    return successes


def _history_part_bounds(history_size, part_sizes):
    bounds = []
    end = history_size
    for part_size in part_sizes:
        start = max(0, end - part_size)
        bounds.append((start, end))
        end = start
    bounds.append((0, end))
    return bounds


def _prs_with_matching_endpoint_results(history_items):
    endpoints = {}
    for item in history_items:
        pr = item.get('pr')
        if pr is None:
            continue
        try:
            success = int(float(item.get('success', 0)))
        except (TypeError, ValueError):
            continue
        if pr not in endpoints:
            endpoints[pr] = [success, success, 1]
        else:
            endpoints[pr][1] = success
            endpoints[pr][2] += 1

    return {
        pr
        for pr, (oldest, newest, count) in endpoints.items()
        if count > 1 and oldest == newest
    }


def _duplicate_pr_index(history_items, start, end, reorderable_prs):
    seen_prs = set()
    for index in range(end - 1, start - 1, -1):
        pr = history_items[index].get('pr')
        if pr not in reorderable_prs:
            continue
        if pr in seen_prs:
            return index
        seen_prs.add(pr)
    return None


def _demote_duplicate_prs(history_items, part_sizes):
    """Demote duplicate PRs only when their oldest and newest outcomes match."""
    reordered = list(history_items)
    reorderable_prs = _prs_with_matching_endpoint_results(history_items)
    for start, end in _history_part_bounds(len(reordered), part_sizes)[:-1]:
        if start == 0:
            break

        while True:
            duplicate_index = _duplicate_pr_index(
                reordered,
                start,
                end,
                reorderable_prs,
            )
            if duplicate_index is None:
                break

            current_prs = {
                item.get('pr')
                for item in reordered[start:end]
                if item.get('pr') is not None
            }
            replacement_index = next(
                (
                    index
                    for index in range(start - 1, -1, -1)
                    if reordered[index].get('pr') is None
                    or reordered[index].get('pr') not in current_prs
                ),
                None,
            )
            if replacement_index is None:
                break

            duplicate = reordered.pop(duplicate_index)
            reordered.insert(replacement_index, duplicate)

    return reordered


def _weighted_history_probability(history_items, part_sizes, part_weights):
    if len(part_weights) != len(part_sizes) + 1:
        raise ValueError("history part weights must include the unbounded oldest partition")
    if any(size <= 0 for size in part_sizes):
        raise ValueError("history part sizes must be positive")
    if any(weight < 0 for weight in part_weights) or not part_weights:
        raise ValueError("history part weights must be non-negative")

    weighted_successes = 0.0
    active_weight = 0.0
    for (start, end), part_weight in zip(
        _history_part_bounds(len(history_items), part_sizes),
        part_weights,
    ):
        successes = _extract_successes(history_items[start:end])
        if not successes:
            continue
        weighted_successes += part_weight * sum(successes) / len(successes)
        active_weight += part_weight

    return weighted_successes / active_weight if active_weight else 0.5


def probabilistic_prediction(history_items):
    """Estimate probability from configurable, PR-aware history partitions."""
    reordered_history = _demote_duplicate_prs(history_items, config.PREDICTION_PART_SIZES)
    return _weighted_history_probability(
        reordered_history,
        config.PREDICTION_PART_SIZES,
        config.PREDICTION_PART_WEIGHTS,
    )


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
            logger.info(
                "Prediction diagnostics: system=%s name=%s verb=%s scenario=%s context_len=%d "
                "usable_successes=%d probability=%.4f model=%s",
                system,
                name,
                verb,
                scenario,
                len(history),
                len(_extract_successes(history)),
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
    """Demonstrate partition weights with synthetic history patterns."""
    scenarios = {
        "stable_pass": {
            "pattern": [1] * 14,
            "expected": "100.00%"
        },
        "stable_fail": {
            "pattern": [0] * 14,
            "expected": "0.00%"
        },
        "new_test_no_history": {
            "pattern": [],
            "expected": "50.00%"
        },
        "newest_partition_passes": {
            "pattern": [0] * 11 + [1] * 3,
            "expected": "50.00%"
        },
        "second_partition_passes": {
            "pattern": [0] * 8 + [1] * 3 + [0] * 3,
            "expected": "25.00%"
        },
        "third_partition_passes": {
            "pattern": [0] * 4 + [1] * 4 + [0] * 6,
            "expected": "15.00%"
        },
        "oldest_partition_passes": {
            "pattern": [1] * 4 + [0] * 10,
            "expected": "10.00%"
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
