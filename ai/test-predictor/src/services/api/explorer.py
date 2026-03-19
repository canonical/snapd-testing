#!/usr/bin/env python3
from flask import Blueprint, current_app, request, jsonify

from common import config
from common.config import setup_logging
from common.predictor import predict_success

import subprocess
from pathlib import Path

logger = setup_logging("tp-explorer-api")
explorer_bp = Blueprint('explorer', __name__)

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
    """Checks if provided params exist in the current encoder classes."""
    unknowns = []
    mapping = {'n': 'name', 'v': 'verb', 'l': 'level', 's': 'system'}
    for k in keys_to_check:
        val = params.get(k)
        if val not in encoders[mapping[k]].classes_:
            unknowns.append(f"{mapping[k]}: {val}")
    return unknowns

@explorer_bp.route('/predict', methods=['GET'])
def predict_scenario():
    p = get_params()
    
    # Dynamically find the project root (3 levels up from src/services/api/explorer.py)
    # Adjust the .parent count based on your actual file depth
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    
    script_path = project_root / "src" / "common" / "isolated_predictor.py"
    model_path = project_root / "model" / "test_predictor_lstm.keras"
    metadata_path = project_root / "model" / "metadata.pkl"
    python_bin = project_root / ".venv" / "bin" / "python3"

    cmd = [
        str(python_bin), str(script_path),
        str(model_path), str(metadata_path),
        str(p['n']), str(p['v']), str(p['l']), str(p['s']), str(p['attempt']), "0.5"
    ]

    try:
        # Isolated process avoids the Gunicorn/TensorFlow deadlock
        logger.info(f"Running command line: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        logger.info(f"Predictor process completed with output: '{result.stdout.strip()}' and error: '{result.stderr.strip()}'")

        if result.returncode != 0:
            logger.error(f"Predictor Error: {result.stderr}")
            return jsonify({"error": "Engine failed", "detail": result.stderr}), 500
        
        return jsonify({
            "success_probability": float(result.stdout.strip()),
            "params": p
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Prediction timed out"}), 504

@explorer_bp.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()
    model, encoders, _ = current_app.model_manager.get_state()
    
    # Ensure model and metadata are loaded
    if model is None or encoders is None:
        logger.warning(f"Worst-systems requested, but model or encoders are missing. Params: {p}")
        return jsonify({
            "error": "Model not loaded", 
            "message": "The system is currently initializing or training."
        }), 503

    if not all([p['v'], p['l'], p['s']]):
        logger.warning(f"Missing params for rank-risk: {p}")
        return jsonify({"error": "Missing verb, level, and system"}), 400

    unknowns = validate_labels(p, encoders, ['v', 'l', 's'])
    if unknowns:
        logger.error(f"Unknown labels for rank-risk: {unknowns}")
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404
    
    names = list(encoders['name'].classes_)
    results = []
    for n in names:
        prob = predict_success(model, encoders, n, p['v'], p['l'], p['s'], attempt=p['attempt'])
        if prob is not None:
            results.append({"name": n, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    
    logger.info(f"Ranked {len(results)} tests for attempt {p['attempt']}")
    return jsonify({"attempt_analyzed": p['attempt'], "top_high_risk": results[:10]})

@explorer_bp.route('/worst-systems', methods=['GET'])
def worst_systems():
    p = get_params()
    model, encoders, _ = current_app.model_manager.get_state()

    # Ensure model and metadata are loaded
    if model is None or encoders is None:
        logger.warning(f"Worst-systems requested, but model or encoders are missing. Params: {p}")
        return jsonify({
            "error": "Model not loaded", 
            "message": "The system is currently initializing or training."
        }), 503
    
    if not all([p['n'], p['v'], p['l']]):
        logger.warning(f"Missing params for worst-systems: {p}")
        return jsonify({"error": "Missing name, verb, and level"}), 400

    unknowns = validate_labels(p, encoders, ['n', 'v', 'l'])
    if unknowns:
        logger.error(f"Unknown labels for worst-systems: {unknowns}")
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404

    systems = list(encoders['system'].classes_)
    results = []
    for s in systems:
        prob = predict_success(model, encoders, p['n'], p['v'], p['l'], s, attempt=p['attempt'])
        if prob is not None:
            results.append({"system": s, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    
    logger.info(f"Ranked {len(results)} systems for test {p['n']}")
    return jsonify(results)

@explorer_bp.route('/list/<category>', methods=['GET'])
def list_metadata(category):
    _, encoders, _ = current_app.model_manager.get_state()
    
    # Ensure encoders exist before accessing them
    if encoders is None:
        logger.warning(f"Metadata requested for '{category}', but no encoders are loaded.")
        return jsonify({
            "error": "Model metadata not available.",
            "status": "The model may still be training or files are missing on disk."
        }), 503

    mapping = {
        'names': 'name',
        'verbs': 'verb',
        'levels': 'level',
        'systems': 'system'
    }

    if category not in mapping:
        logger.warning(f"Invalid list category requested: {category}")
        return jsonify({"error": "Invalid category", "valid": list(mapping.keys())}), 400

    vals = list(encoders[mapping[category]].classes_)
    logger.info(f"Listed {len(vals)} items for category: {category}")
    return jsonify({"category": category, "count": len(vals), "values": vals})
