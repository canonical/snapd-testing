"""
ESN Predictor Service — mirrors the LSTM predictor as an independent source of truth.

Runs on ESN_PREDICTOR_PORT and exposes /internal/predict and /internal/flakiness
endpoints. Uses the same cache and encoding logic as the LSTM predictor for
consistent feature representation.
"""

import json
import os
import time

import numpy as np
from flask import Flask, request, jsonify

from common import config
from common.utils import setup_logging
from common.esn_model import ESNManager
from common.cache import SystemStateCache

logger = setup_logging("esn-predictor-server")
app = Flask(__name__)

# Initialize the cache (shared format with LSTM predictor)
app.state_cache = SystemStateCache(history_size=config.SEQUENCE_LENGTH - 1)
app.state_cache.initialize()

# Initialize ESN manager
app.esn_manager = ESNManager()
if app.esn_manager.exists():
    app.esn_manager.load()
else:
    logger.warning("No trained ESN found on disk. Predictions unavailable until training.")


def encode_to_vector(data, encoders):
    """Encode a single data point to feature vector (same logic as LSTM predictor)."""
    n = data.get('name', 'unknown')
    v = data.get('verb', 'unknown')
    s = data.get('system', 'unknown')
    sce = data.get('scenario', config.DEFAULT_SCENARIO)
    b_val = data.get('backend', 'unknown')
    succ = data.get('success', config.CURRENT_SUCCESS_MASK_VALUE)

    def get_id(key, value):
        enc = encoders.get(key)
        if enc is None:
            return 0.0
        try:
            encoded = float(enc.transform([str(value)])[0])
            max_index = max(len(enc.classes_) - 1, 1)
            return encoded / float(max_index)
        except (ValueError, KeyError):
            return 0.0

    return np.array([
        get_id('scenario', sce),
        get_id('verb', v),
        get_id('backend', b_val),
        get_id('system', s),
        get_id('name', n),
        float(succ),
    ], dtype='float32')


def build_esn_input(sequence_items, encoders):
    """Build a padded input sequence for the ESN (same masking as LSTM)."""
    X_input = np.zeros((config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')

    for i, raw_item in enumerate(reversed(sequence_items)):
        if i >= config.SEQUENCE_LENGTH:
            break

        item = dict(raw_item)
        if i == 0:
            # Mask current-step success to prevent leakage
            item['success'] = config.CURRENT_SUCCESS_MASK_VALUE

        vector = encode_to_vector(item, encoders)
        X_input[-1 - i, :] = vector

    return X_input


def validate_labels(params, keys_to_check, encoders):
    """Validate that parameter values exist in the fitted encoders."""
    unknowns = []
    for k in keys_to_check:
        val = params.get(k)
        if k in encoders:
            if val not in encoders[k].classes_:
                unknowns.append(f"{k}: {val}")
    return unknowns


@app.route('/internal/predict', methods=['POST'])
def predict():
    """Predict success probability using the ESN."""
    data = request.json
    system = data.get('system')
    name = data.get('name')
    verb = data.get('verb')
    backend = data.get('backend')

    esn, encoders, _ = app.esn_manager.get_state()
    if esn is None or encoders is None:
        return jsonify({"error": "ESN not loaded"}), 503

    # Normalize the target entry
    normalized_target = app.state_cache._normalize_entry(data)

    # Validate labels
    keys_to_validate = ['name', 'verb', 'system', 'scenario']
    unknowns = validate_labels(normalized_target, keys_to_validate, encoders)
    if unknowns:
        return jsonify({"error": "Unknown labels", "details": unknowns}), 400

    try:
        # Get historical context
        backend_filter = str(backend).strip() if backend and str(backend).strip() else None
        history = app.state_cache.get_context(
            system=system,
            name=name,
            verb=verb,
            attempt=None,
            scenario=None,
            backend=backend_filter,
        )

        # Infer backend from history if not provided
        if normalized_target.get('backend') in (None, '', 'unknown') and history:
            recent_backend = history[-1].get('backend')
            if recent_backend:
                normalized_target['backend'] = str(recent_backend)

        # Build sequence input
        full_sequence = history + [normalized_target]
        X_input = build_esn_input(full_sequence, encoders)

        # Predict
        prob = esn.predict(X_input)

        return jsonify({
            "probability": float(prob),
            "context_len": len(history),
            "model": "esn",
        })

    except Exception as e:
        logger.error("ESN prediction error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/internal/flakiness', methods=['POST'])
def flakiness():
    """
    Compute flakiness score for a test based on reservoir state variance
    across its recent execution history.
    """
    data = request.json
    system = data.get('system')
    name = data.get('name')
    verb = data.get('verb', 'executing')
    backend = data.get('backend')

    esn, encoders, _ = app.esn_manager.get_state()
    if esn is None or encoders is None:
        return jsonify({"error": "ESN not loaded"}), 503

    try:
        backend_filter = str(backend).strip() if backend and str(backend).strip() else None
        history = app.state_cache.get_context(
            system=system,
            name=name,
            verb=verb,
            attempt=None,
            scenario=None,
            backend=backend_filter,
        )

        if len(history) < 3:
            return jsonify({
                "flakiness_score": 0.0,
                "context_len": len(history),
                "message": "Insufficient history for flakiness analysis",
            })

        # Build a sequence for each historical run (sliding window)
        sequences = []
        for i in range(len(history)):
            start_idx = max(0, i - config.SEQUENCE_LENGTH + 1)
            window = history[start_idx:i + 1]
            X = build_esn_input(window, encoders)
            sequences.append(X)

        X_all = np.array(sequences)
        score = esn.get_flakiness_score(X_all)

        return jsonify({
            "flakiness_score": float(score),
            "context_len": len(history),
            "model": "esn",
        })

    except Exception as e:
        logger.error("Flakiness computation error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/internal/reload', methods=['POST'])
def reload_model():
    """Reload the ESN from disk (triggered after training)."""
    logger.info("ESN reload signal received.")
    success = app.esn_manager.load()
    if success:
        app.state_cache.initialize()
        return jsonify({"status": "success", "message": "ESN reloaded"}), 200
    return jsonify({"status": "error", "message": "ESN reload failed"}), 500


@app.route('/internal/status', methods=['GET'])
def status():
    """Return ESN model status."""
    esn, _, last_updated = app.esn_manager.get_state()
    return jsonify({
        "model": "esn",
        "loaded": esn is not None and esn.readout is not None,
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_updated)) if last_updated else None,
        "reservoir_size": config.ESN_RESERVOIR_SIZE,
        "spectral_radius": config.ESN_SPECTRAL_RADIUS,
    })


if __name__ == '__main__':
    app.run(host=config.SERVER_HOST, port=config.ESN_PREDICTOR_PORT)
