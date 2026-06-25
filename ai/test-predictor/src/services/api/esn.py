"""
API routes for ESN predictions and ensemble (LSTM + ESN) combined predictions.

The ensemble endpoint queries both models independently and returns a weighted
combination, giving operators two sources of truth plus a blended view.
"""

import requests
from flask import Blueprint, request, jsonify
from common import config
from common.utils import setup_logging

logger = setup_logging("esn-api")
esn_bp = Blueprint('esn', __name__)

# Internal URLs
LSTM_PREDICTOR_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/predict"
ESN_PREDICTOR_URL = f"http://{config.SERVER_HOST}:{config.ESN_PREDICTOR_PORT}/internal/predict"
ESN_FLAKINESS_URL = f"http://{config.SERVER_HOST}:{config.ESN_PREDICTOR_PORT}/internal/flakiness"
ESN_STATUS_URL = f"http://{config.SERVER_HOST}:{config.ESN_PREDICTOR_PORT}/internal/status"


def _get_params():
    return {
        "name": request.args.get('name'),
        "verb": request.args.get('verb'),
        "backend": request.args.get('backend', None),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', None),
        "scenario": request.args.get('scenario', None),
    }


def _call_predictor(url, payload, timeout=5):
    """Call an internal predictor service and return (result_dict, status_code)."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code == 400:
            return resp.json(), 400
        if resp.status_code == 200:
            return resp.json(), 200
        return {"error": f"Service error ({resp.status_code})"}, resp.status_code
    except requests.exceptions.ConnectionError:
        return {"error": "Service unreachable"}, 502
    except Exception as e:
        logger.error("Predictor call to %s failed: %s", url, e)
        return {"error": "Service error"}, 502


@esn_bp.route('/esn/predict', methods=['GET'])
def esn_predict():
    """Get prediction from the ESN model only."""
    p = _get_params()
    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing required params: name, verb, system"}), 400

    result, status_code = _call_predictor(ESN_PREDICTOR_URL, p)
    if status_code != 200:
        return jsonify(result), status_code

    return jsonify({
        "success_probability": result.get("probability"),
        "context_len": result.get("context_len"),
        "model": "esn",
        "params": p,
    }), 200


@esn_bp.route('/esn/flakiness', methods=['GET'])
def esn_flakiness():
    """Get flakiness score from the ESN reservoir state analysis."""
    p = _get_params()
    if not all([p['name'], p['system']]):
        return jsonify({"error": "Missing required params: name, system"}), 400

    try:
        resp = requests.post(ESN_FLAKINESS_URL, json=p, timeout=5)
        if resp.status_code == 200:
            body = resp.json()
            return jsonify({
                "flakiness_score": body.get("flakiness_score"),
                "context_len": body.get("context_len"),
                "params": p,
            }), 200
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        logger.error("Flakiness call failed: %s", e)
        return jsonify({"error": "ESN flakiness service unreachable"}), 502


@esn_bp.route('/esn/status', methods=['GET'])
def esn_status():
    """Get ESN model status."""
    try:
        resp = requests.get(ESN_STATUS_URL, timeout=5)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": "ESN service unreachable"}), 502


@esn_bp.route('/ensemble/predict', methods=['GET'])
def ensemble_predict():
    """
    Query both LSTM and ESN models, return individual predictions and a
    weighted ensemble result.

    The ensemble weight is configurable via ESN_ENSEMBLE_WEIGHT.
    If one model is unavailable, falls back to the available model alone.
    """
    p = _get_params()
    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing required params: name, verb, system"}), 400

    lstm_result, lstm_status = _call_predictor(LSTM_PREDICTOR_URL, p)
    esn_result, esn_status = _call_predictor(ESN_PREDICTOR_URL, p)

    lstm_prob = None
    esn_prob = None
    ensemble_prob = None

    if lstm_status == 200:
        lstm_prob = float(lstm_result.get("probability", 0))
    if esn_status == 200:
        esn_prob = float(esn_result.get("probability", 0))

    # Compute ensemble
    if lstm_prob is not None and esn_prob is not None:
        w_esn = config.ESN_ENSEMBLE_WEIGHT
        w_lstm = 1.0 - w_esn
        ensemble_prob = w_lstm * lstm_prob + w_esn * esn_prob
    elif lstm_prob is not None:
        ensemble_prob = lstm_prob
    elif esn_prob is not None:
        ensemble_prob = esn_prob
    else:
        return jsonify({
            "error": "Both predictors unavailable",
            "lstm_error": lstm_result.get("error"),
            "esn_error": esn_result.get("error"),
        }), 503

    # Agreement metric: how closely the two models agree (1.0 = perfect agreement)
    agreement = None
    if lstm_prob is not None and esn_prob is not None:
        agreement = 1.0 - abs(lstm_prob - esn_prob)

    return jsonify({
        "ensemble_probability": ensemble_prob,
        "lstm_probability": lstm_prob,
        "esn_probability": esn_prob,
        "agreement": agreement,
        "esn_weight": config.ESN_ENSEMBLE_WEIGHT,
        "lstm_weight": 1.0 - config.ESN_ENSEMBLE_WEIGHT,
        "context_len": {
            "lstm": lstm_result.get("context_len") if lstm_status == 200 else None,
            "esn": esn_result.get("context_len") if esn_status == 200 else None,
        },
        "params": p,
    }), 200
