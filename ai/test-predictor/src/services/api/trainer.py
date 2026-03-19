import requests
from flask import Blueprint, jsonify
from common import config
from common.config import setup_logging

logger = setup_logging("tp-trainer-gateway")
trainer_bp = Blueprint('trainer', __name__)

# The internal URL of your standalone trainer service
TRAINER_INTERNAL_URL = f"http://{config.SERVER_HOST}:{config.TRAINER_PORT}/internal"

@trainer_bp.route('/train', methods=['POST'])
def manual_train():
    """Proxies the training request to the standalone Trainer Service."""
    try:
        # We use a very short timeout (1s) because we want the 
        # internal service to handle the 'background' part.
        logger.info("Forwarding manual training request to internal service...")
        
        # We don't wait for the whole training (which takes minutes)
        # The internal service should return 200/202 immediately or 429 if busy
        resp = requests.post(f"{TRAINER_INTERNAL_URL}/train", timeout=2)
        
        if resp.status_code == 200:
            return jsonify({"status": "success", "message": "Training triggered"}), 202
        elif resp.status_code == 429:
            return jsonify({"status": "error", "message": "Training already in progress"}), 429
        else:
            return jsonify({"status": "error", "message": "Internal trainer error"}), 500

    except requests.exceptions.Timeout:
        # If the trainer starts immediately, it might not respond in 2s
        # In this specific architecture, a timeout often means it started!
        return jsonify({"status": "success", "message": "Training initiated (ack)"}), 202
    except Exception as e:
        logger.error(f"Failed to reach internal trainer: {e}")
        return jsonify({"status": "error", "message": "Trainer service unreachable"}), 503

@trainer_bp.route('/status', methods=['GET'])
def get_status():
    """Fetches the current training state from the standalone service."""
    try:
        resp = requests.get(f"{TRAINER_INTERNAL_URL}/status", timeout=5)
        return jsonify(resp.json()), 200
    except Exception as e:
        logger.error(f"Failed to get status from internal trainer: {e}")
        return jsonify({"error": "Trainer service unreachable"}), 503
