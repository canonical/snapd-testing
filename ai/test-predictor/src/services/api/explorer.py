#!/usr/bin/env python3
import os
import pickle
import threading
from flask import Blueprint, current_app, request, jsonify
from tensorflow.keras import backend as K
from tensorflow.keras.models import load_model

from common import config
from common.config import setup_logging
from common.predictor import predict_success

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
    model, encoders, _ = current_app.model_manager.get_state()
    
    if not all([p['n'], p['v'], p['l'], p['s']]):
        logger.warning(f"Missing params: name, verb, level, and system are required. Received: {p}")
        return jsonify({"error": "Missing params: name, verb, level, and system are required"}), 400
    
    unknowns = validate_labels(p, encoders, ['n', 'v', 'l', 's'])
    if unknowns:
        logger.error(f"Unknown labels provided: {unknowns}")
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404

    prob = predict_success(model, encoders, p['n'], p['v'], p['l'], p['s'], attempt=p['attempt'])
    
    if prob is None:
        logger.error(f"Prediction failed for {p}")
        return jsonify({"error": "Internal prediction failure"}), 500

    logger.info(f"Predicted success probability for {p}: {prob}")
    return jsonify({"success_probability": float(prob), "params": p})

@explorer_bp.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()
    model, encoders, _ = current_app.model_manager.get_state()
    
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
