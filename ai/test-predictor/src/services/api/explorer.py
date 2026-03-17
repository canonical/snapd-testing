#!/usr/bin/env python3
import os
import pickle
from flask import Flask, request, jsonify
from tensorflow.keras.models import load_model

from common import config
from common.config import setup_logging
from common.predictor import predict_success

logger = setup_logging("tp-explorer-api")

# Silence TF noise
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

app = Flask(__name__)

# --- Global Model Loading (Reusing your logic) ---
model_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)

MODEL = load_model(model_path)
with open(metadata_path, 'rb') as f:
    ENCODERS, SCALER = pickle.load(f)

NAMES = list(ENCODERS['name'].classes_)
VERBS = list(ENCODERS['verb'].classes_)
LEVELS = list(ENCODERS['level'].classes_)
SYSTEMS = list(ENCODERS['system'].classes_)

# --- Helper for standard params ---
def get_params():
    return {
        "n": request.args.get('name'),
        "v": request.args.get('verb'),
        "l": request.args.get('level'),
        "s": request.args.get('system'),
        "attempt": int(request.args.get('attempt', 1))
    }

@app.route('/predict', methods=['GET'])
def predict_scenario():
    """Choice 1: Predict Specific Scenario"""
    p = get_params()
    # Basic validation
    if not all([p['n'], p['v'], p['l'], p['s']]):
        logger.warning("Missing params: name, verb, level, and system are required")
        return jsonify({"error": "Missing params: name, verb, level, and system are required"}), 400
    
    prob = predict_success(MODEL, ENCODERS, p['n'], p['v'], p['l'], p['s'], attempt=p['attempt'])

    logger.info(f"Predicted success probability for {p}: {prob}")
    return jsonify({"success_probability": float(prob), "params": p})

@app.route('/rank-risk', methods=['GET'])
def rank_risk():
    """Choice 2 & 4: Rank Tests (High Risk / Worst by Attempt)"""
    p = get_params()
    # Choice 4 specifically uses 'attempt', Choice 2 uses default (1)
    target_attempt = p['attempt'] 
    
    results = []
    for n in NAMES:
        prob = predict_success(MODEL, ENCODERS, n, p['v'], p['l'], p['s'], attempt=target_attempt)
        if prob is not None:
            results.append({"name": n, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])

    logger.info(f"Ranked {len(results)} tests for attempt {target_attempt}")
    return jsonify({
        "attempt_analyzed": target_attempt,
        "top_high_risk": results[:10]
    })

@app.route('/worst-systems', methods=['GET'])
def worst_systems():
    """Choice 3: Worst System for Test"""
    p = get_params()
    results = []
    for s in SYSTEMS:
        prob = predict_success(MODEL, ENCODERS, p['n'], p['v'], p['l'], s, attempt=p['attempt'])
        if prob is not None:
            results.append({"system": s, "prob": float(prob)})
    
    results.sort(key=lambda x: x['prob'])

    logger.info(f"Ranked {len(results)} systems for test {p['n']}")
    return jsonify(results)
