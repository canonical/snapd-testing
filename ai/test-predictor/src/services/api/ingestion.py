#!/usr/bin/env python3

import os
import json

from flask import Flask, request, jsonify

from common import config
from common.config import setup_logging

logger = setup_logging("tp-ingestion-api")

app = Flask(__name__)

# Ensure the directory exists on startup
if not os.path.exists(config.RESULTS_DIR):
    os.makedirs(config.RESULTS_DIR)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'json'

@app.route('/ingest', methods=['POST'])
def ingest_data():
    # Validate mandatory IDs
    job_id = request.form.get('job_id')
    run_id = request.form.get('run_id')
    attempt = request.form.get('attempt', '0')
    logger.info(f"Received ingestion request: job_id={job_id}, run_id={run_id}, attempt={attempt}")

    if not job_id or not run_id:
        logger.warning("Missing mandatory parameters: job_id and run_id are required")
        return jsonify({"error": "Missing mandatory parameters: job_id and run_id are required"}), 400

    if not job_id.isdigit() or not run_id.isdigit():
        logger.warning(f"invalid job_id ({job_id}) or run_id ({run_id}): must be positive numbers")
        return jsonify({"error": "job_id and run_id must be positive integers"}), 400

    # Check for the file
    if 'file' not in request.files:
        logger.warning("No file part in the request")
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        logger.warning("No selected file")
        return jsonify({"error": "No selected file"}), 400

    # Validate JSON extension and content
    if not allowed_file(file.filename):
        logger.warning("Only .json files are allowed")
        return jsonify({"error": "Only .json files are allowed"}), 400

    try:
        file_content = file.read()
        json.loads(file_content)
        file.seek(0) 
    except (ValueError, json.JSONDecodeError):
        logger.warning("Invalid JSON content")
        return jsonify({"error": "Invalid JSON content"}), 400

    # Construct path and check for existing file    
    filename = f"results_job_{job_id}_run_{run_id}_attempt_{attempt}.json"
    filepath = os.path.join(config.RESULTS_DIR, filename)

    if os.path.exists(filepath):
        logger.warning(f"File already exists: {filename}")
        return jsonify({"error": f"File already exists: {filename}"}), 409

    # Save the file
    try:
        file.save(filepath)
        logger.info(f"File saved successfully: {filename}")
        return jsonify({
            "status": "success", 
            "job_id": job_id, 
            "run_id": run_id,
            "filename": filename
        }), 201
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
