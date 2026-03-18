#!/usr/bin/env python3
import gc
import os
import pickle
from flask import Flask, request, jsonify
from tensorflow.keras import backend as K
from tensorflow.keras.models import load_model

from common import config
from common.config import setup_logging
from common.predictor import predict_success

logger = setup_logging("tp-explorer-api")

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

app = Flask(__name__)

model_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)

MODEL = load_model(model_path)
with open(metadata_path, 'rb') as f:
    ENCODERS, SCALER = pickle.load(f)

NAMES = list(ENCODERS['name'].classes_)
VERBS = list(ENCODERS['verb'].classes_)
LEVELS = list(ENCODERS['level'].classes_)
SYSTEMS = list(ENCODERS['system'].classes_)

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

def validate_labels(params, keys_to_check):
    """Helper to check if provided params exist in ENCODERS"""
    unknowns = []
    mapping = {'n': 'name', 'v': 'verb', 'l': 'level', 's': 'system'}
    for k in keys_to_check:
        val = params.get(k)
        if val not in ENCODERS[mapping[k]].classes_:
            unknowns.append(f"{mapping[k]}: {val}")
    return unknowns

@app.route('/predict', methods=['GET'])
def predict_scenario():
    p = get_params()
    if not all([p['n'], p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing params: name, verb, level, and system are required"}), 400
    
    unknowns = validate_labels(p, ['n', 'v', 'l', 's'])
    if unknowns:
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404

    prob = predict_success(MODEL, ENCODERS, p['n'], p['v'], p['l'], p['s'], attempt=p['attempt'])
    if prob is None:
        return jsonify({"error": "Prediction failed"}), 500

    return jsonify({"success_probability": float(prob), "params": p})

@app.route('/rank-risk', methods=['GET'])
def rank_risk():
    p = get_params()
    # rank-risk requires verb, level, and system to iterate through all names
    if not all([p['v'], p['l'], p['s']]):
        return jsonify({"error": "Missing params: verb, level, and system are required"}), 400

    unknowns = validate_labels(p, ['v', 'l', 's'])
    if unknowns:
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404
    
    results = []
    for n in NAMES:
        prob = predict_success(MODEL, ENCODERS, n, p['v'], p['l'], p['s'], attempt=p['attempt'])
        if prob is not None:
            results.append({"name": n, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify({"attempt_analyzed": p['attempt'], "top_high_risk": results[:10]})

@app.route('/worst-systems', methods=['GET'])
def worst_systems():
    p = get_params()
    # worst-systems requires name, verb, and level to iterate through all systems
    if not all([p['n'], p['v'], p['l']]):
        return jsonify({"error": "Missing params: name, verb, and level are required"}), 400

    unknowns = validate_labels(p, ['n', 'v', 'l'])
    if unknowns:
        return jsonify({"error": "Unknown labels", "unknown_params": unknowns}), 404

    results = []
    for s in SYSTEMS:
        prob = predict_success(MODEL, ENCODERS, p['n'], p['v'], p['l'], s, attempt=p['attempt'])
        if prob is not None:
            results.append({"system": s, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])
    return jsonify(results)

@app.route('/list/<category>', methods=['GET'])
def list_metadata(category):
    """List valid values for: name, verb, level, or system"""
    mapping = {
        'names': NAMES,
        'verbs': VERBS,
        'levels': LEVELS,
        'systems': SYSTEMS
    }

    if category not in mapping:
        return jsonify({
            "error": f"Invalid category '{category}'",
            "valid_categories": list(mapping.keys())
        }), 400

    return jsonify({
        "category": category,
        "count": len(mapping[category]),
        "values": mapping[category]
    })

@app.route('/reload', methods=['POST'])
def reload_model():
    global MODEL, ENCODERS, NAMES, VERBS, LEVELS, SYSTEMS
    try:
        logger.info("Starting reload...")
        
        # 1. Clear old model from memory to prevent memory leaks/deadlocks
        K.clear_session()
        del MODEL
        gc.collect() 

        # 2. Reload metadata first (it's fast)
        with open(metadata_path, 'rb') as f:
            ENCODERS, SCALER = pickle.load(f)

        NAMES = list(ENCODERS['name'].classes_)
        VERBS = list(ENCODERS['verb'].classes_)
        LEVELS = list(ENCODERS['level'].classes_)
        SYSTEMS = list(ENCODERS['system'].classes_)

        # 3. Reload the Model (This is the heavy part)
        MODEL = load_model(model_path)

        metadata_summary = {
            "names_count": len(NAMES),
            "systems_count": len(SYSTEMS),
            "levels_count": len(LEVELS),
            "verbs_count": len(VERBS)
        }

        logger.info(f"Reload successful: {metadata_summary}")
        return jsonify({"status": "success", "metadata": metadata_summary})

    except Exception as e:
        logger.error(f"Reload failed: {str(e)}")
        return jsonify({"error": str(e)}), 500


