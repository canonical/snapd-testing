import requests
from flask import Blueprint, current_app, request, jsonify
from common import config
from common.utils import setup_logging

logger = setup_logging("predictor-api")
predictor_bp = Blueprint('predictor', __name__)

# Internal URL for the standalone predictor
PREDICTOR_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/predict"
CATEGORY_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/list"

def get_params():
    try:
        attempt = int(request.args.get('attempt', config.DEFAULT_ATTEMPT))
        scenario = request.args.get('scenario', config.DEFAULT_SCENARIO)
    except ValueError:
        attempt = config.DEFAULT_ATTEMPT
        scenario = config.DEFAULT_SCENARIO
    return {
        "n": request.args.get('name'),
        "v": request.args.get('verb'),
        "l": request.args.get('level'),
        "s": request.args.get('system'),
        "attempt": attempt,
        "scenario": scenario
    }

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

@predictor_bp.route('/predict', methods=['GET'])
def predict_scenario():
    p = get_params()
    if not all([p['n'], p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing params"}), 400
    
    prob = call_internal_predictor(p)
    if prob is None:
        return jsonify({"error": "Predictor service error"}), 503
        
    return jsonify({"success_probability": prob, "params": p})

@predictor_bp.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()

    if not all([p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing verb, level, and system"}), 400

    # Fetch the names list from the Predictor Server
    try:
        # We call the 'internal/list/names' route we just created on the server        
        list_response = requests.get(f"{CATEGORY_URL}/names", timeout=10)
        
        if list_response.status_code != 200:
            return jsonify({"error": "Could not retrieve names from predictor server"}), 503
            
        # Extract the 'values' list from the server response
        names = list_response.json().get('values', [])
    except Exception as e:
        logger.error(f"Failed to connect to Predictor Server for metadata: {e}")
        return jsonify({"error": "Predictor service communication error"}), 502

    results = []
    # Predict for the given verb, level and system across all names to find the riskiest ones
    for n in names:
        payload = {"n": n, "v": p['v'], "l": p['l'], "s": p['s'], "attempt": p['attempt'], "scenario": p['scenario']}
        prob = call_internal_predictor(payload)
        if prob is not None:
            results.append({"name": n, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify({"attempt_analyzed": p['attempt'], "top_high_risk": results[:10]})

@predictor_bp.route('/worst-systems', methods=['GET'])
def worst_systems():
    p = get_params()

    if not all([p['n'], p['v'], p['l']]):
        return jsonify({"error": "Missing name, verb, and level"}), 400

    # Fetch the systems list from the Predictor Server
    try:
        # We call the 'internal/list/systems' route we just created on the server        
        list_response = requests.get(f"{CATEGORY_URL}/systems", timeout=10)
        
        if list_response.status_code != 200:
            return jsonify({"error": "Could not retrieve systems from predictor server"}), 503
            
        # Extract the 'values' list from the server response
        systems = list_response.json().get('values', [])
    except Exception as e:
        logger.error(f"Failed to connect to Predictor Server for metadata: {e}")
        return jsonify({"error": "Predictor service communication error"}), 502

    results = []
    # Predict for the given name, verb and level across all systems to find the riskiest ones
    for s in systems:
        payload = {"n": p['n'], "v": p['v'], "l": p['l'], "s": s, "attempt": p['attempt'], "scenario": p['scenario']}
        prob = call_internal_predictor(payload)
        if prob is not None:
            results.append({"system": s, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify(results)

# In your predictor_bp (the API side)
import requests

@predictor_bp.route('/list/<category>', methods=['GET'])
def proxy_list_metadata(category):
    try:
        response = requests.get(f"{CATEGORY_URL}/{category}", timeout=10)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        return jsonify({"error": f"Predictor server unreachable: {e}"}), 502

