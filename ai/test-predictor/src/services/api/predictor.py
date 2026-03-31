import requests
from flask import Blueprint, request, jsonify
from common import config
from common.utils import setup_logging

logger = setup_logging("predictor-api")
predictor_bp = Blueprint('predictor', __name__)

# Internal URL for the standalone predictor
PREDICTOR_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/predict"
CATEGORY_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/list"
CACHE_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/context"
TEST_URL = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/test"

def get_params():
    return {
        "name": request.args.get('name'),
        "verb": request.args.get('verb'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt', None),
        "scenario": request.args.get('scenario', None),
        "audit": request.args.get('audit', config.DEFAULT_AUDIT)
    }

def call_internal_predictor(payload):
    """Helper to call the standalone predictor service."""
    try:
        resp = requests.post(PREDICTOR_URL, json=payload, timeout=5)
        # If the server returned a 400 (Validation Error), 
        # we want the user to see exactly WHY it failed.
        if resp.status_code == 400:
            return resp.json(), 400

        if resp.status_code == 200:
            return {"probability": resp.json().get('probability')}, 200            

        # Any other server error (500, 404, etc)
        return {"error": "Predictor server error"}, resp.status_code

    except Exception as e:
        logger.error(f"Predictor call failed: {e}")
        # Return a 502/503 status code for connectivity issues
        return {"error": "Predictor service unreachable"}, 502

@predictor_bp.route('/predict', methods=['GET'])
def predict_scenario():
    p = get_params()
    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing params"}), 400
    
    # Unpack the tuple: (data_dict, status_code)
    result, status_code = call_internal_predictor(p)

    # If it's a 400 (Validation error) or anything other than success
    if status_code != 200:
        return jsonify(result), status_code
        
    # If successful, return the clean probability
    return jsonify({
        "success_probability": result.get("probability"), 
        "params": p
    }), 200

@predictor_bp.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()
    
    # Get 'limit' from query params, default to None if not provided or invalid
    limit_raw = request.args.get('limit')
    try:
        limit = int(limit_raw) if limit_raw is not None else None
    except ValueError:
        limit = None

    if not all([p['verb'], p['system']]):
        return jsonify({"error": "Missing verb and system"}), 400

    try:
        list_response = requests.get(f"{CATEGORY_URL}/names", timeout=10)
        if list_response.status_code != 200:
            return jsonify({"error": "Could not retrieve names"}), 503
        names = list_response.json().get('values', [])
    except Exception as e:
        logger.error(f"Failed to connect to Predictor Server: {e}")
        return jsonify({"error": "Predictor service communication error"}), 502

    results = []
    for n in names:
        p = {
            "name": n, "verb": p['verb'], "system": p['system'], 
            "attempt": p['attempt'], "scenario": p['scenario']
        }
        result, status_code = call_internal_predictor(p)
        
        if status_code == 200 and result is not None:
            results.append({"name": n, "prob": float(result.get("probability"))})

    # Sort by probability (minor/lowest first)
    results.sort(key=lambda x: x['prob'])

    # Apply limit slice: if limit is None, it returns results[:] (the whole list)
    final_results = results[:limit] if limit is not None else results

    return jsonify({
        "attempt_analyzed": p['attempt'], 
        "count": len(final_results),
        "top_high_risk": final_results
    })

@predictor_bp.route('/worst-systems', methods=['GET'])
def worst_systems():
    p = get_params()

    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing name, verb, and system"}), 400

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
    # Predict for the given name and verb across all systems to find the riskiest ones
    for s in systems:
        payload = {"name": p['name'], "verb": p['verb'], "system": s, "attempt": p['attempt'], "scenario": p['scenario']}
        result, status_code = call_internal_predictor(payload)
        if status_code != 200:
            return jsonify(result), status_code
    
    results.sort(key=lambda x: x['prob'])
    return jsonify(results)

@predictor_bp.route('/list/<category>', methods=['GET'])
def proxy_list_metadata(category):
    try:
        response = requests.get(f"{CATEGORY_URL}/{category}", timeout=10)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        return jsonify({"error": f"Predictor server unreachable: {e}"}), 502

@predictor_bp.route('/predict-with-history', methods=['GET'])
def predict_with_history():
    """Returns the prediction PLUS the 49-step history used for the LSTM."""
    p = get_params()
    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing params"}), 400
    
    # Get the Prediction
    result, status_code = call_internal_predictor(p)
    if status_code != 200:
        return jsonify(result), status_code
        
    # Get the Context Cache from the internal server
    try:
        cache_resp = requests.get(CACHE_URL, params=p, timeout=5)
        history = cache_resp.json().get('history', []) if cache_resp.status_code == 200 else []
    except Exception as e:
        logger.error(f"Failed to fetch history: {e}")
        history = []

    return jsonify({
        "success_probability": result.get("probability"), 
        "history_length": len(history),
        "history": history,
        "params": p
    }), 200

@predictor_bp.route('/context', methods=['GET'])
def get_system_context():
    """Directly retrieves the current cache for a specific test configuration."""
    p = get_params()
    try:
        response = requests.get(CACHE_URL, params=p, timeout=5)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        return jsonify({"error": f"Internal predictor unreachable: {e}"})
    
@predictor_bp.route('/test', methods=['GET'])
def get_system_test():
    """Directly retrieves the current test configuration."""
    p = get_params()
    if not all([p['name'], p['verb'], p['system']]):
        return jsonify({"error": "Missing params"}), 400
    try:
        response = requests.get(TEST_URL, params=p, timeout=10)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        return jsonify({"error": f"Internal predictor unreachable: {e}"})