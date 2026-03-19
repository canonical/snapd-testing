import requests
from flask import Blueprint, current_app, request, jsonify
from common import config
from common.config import setup_logging

logger = setup_logging("tp-explorer-api")
explorer_bp = Blueprint('explorer', __name__)

# Internal URL for the standalone predictor
PREDICTOR_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/predict"

def get_params():
    try:
        attempt = int(request.args.get('attempt', 1))
    except ValueError:
        attempt = 1
    return {
        "n": request.args.get('name'),
        "v": request.args.get('verb'),
        "l": request.args.get('level'),
        "s": request.args.get('system'),
        "attempt": attempt
    }

def validate_labels(params, encoders, keys_to_check):
    mapping = {'n': 'name', 'v': 'verb', 'l': 'level', 's': 'system'}
    unknowns = []
    for k in keys_to_check:
        val = params.get(k)
        if val not in encoders[mapping[k]].classes_:
            unknowns.append(f"{mapping[k]}: {val}")
    return unknowns

def call_internal_predictor(payload):
    """Helper to call the standalone predictor service."""
    try:
        resp = requests.post(PREDICTOR_URL, json=payload, timeout=5)
        if resp.status_code == 200:
            return resp.json().get('probability')
        return None
    except Exception as e:
        logger.error(f"Predictor call failed: {e}")
        return None

@explorer_bp.route('/predict', methods=['GET'])
def predict_scenario():
    p = get_params()
    _, encoders, _ = current_app.model_manager.get_state()
    
    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    if not all([p['n'], p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing params"}), 400
    
    unknowns = validate_labels(p, encoders, ['n', 'v', 'l', 's'])
    if unknowns:
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404

    prob = call_internal_predictor(p)
    if prob is None:
        return jsonify({"error": "Predictor service error"}), 503
        
    return jsonify({"success_probability": prob, "params": p})

@explorer_bp.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()
    _, encoders, _ = current_app.model_manager.get_state()
    
    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503

    if not all([p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing verb, level, and system"}), 400

    names = list(encoders['name'].classes_)
    results = []
    for n in names:
        payload = {"n": n, "v": p['v'], "l": p['l'], "s": p['s'], "attempt": p['attempt']}
        prob = call_internal_predictor(payload)
        if prob is not None:
            results.append({"name": n, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify({"attempt_analyzed": p['attempt'], "top_high_risk": results[:10]})

@explorer_bp.route('/worst-systems', methods=['GET'])
def worst_systems():
    p = get_params()
    _, encoders, _ = current_app.model_manager.get_state()

    if encoders is None:
        return jsonify({"error": "Metadata not loaded"}), 503
    
    if not all([p['n'], p['v'], p['l']]):
        return jsonify({"error": "Missing name, verb, and level"}), 400

    systems = list(encoders['system'].classes_)
    results = []
    for s in systems:
        payload = {"n": p['n'], "v": p['v'], "l": p['l'], "s": s, "attempt": p['attempt']}
        prob = call_internal_predictor(payload)
        if prob is not None:
            results.append({"system": s, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify(results)

@explorer_bp.route('/list/<category>', methods=['GET'])
def list_metadata(category):
    _, encoders, _ = current_app.model_manager.get_state()
    if encoders is None:
        return jsonify({"error": "Metadata not available"}), 503

    mapping = {'names': 'name', 'verbs': 'verb', 'levels': 'level', 'systems': 'system'}
    if category not in mapping:
        return jsonify({"error": "Invalid category"}), 400

    vals = list(encoders[mapping[category]].classes_)
    return jsonify({"category": category, "count": len(vals), "values": vals})
