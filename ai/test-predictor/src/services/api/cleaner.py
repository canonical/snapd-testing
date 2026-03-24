import requests
from flask import Blueprint, jsonify
from common import config
from common.utils import setup_logging

logger = setup_logging("cleaner-api")
cleaner_bp = Blueprint('cleaner', __name__)

# Internal URL of your standalone cleaner service (default port 5001)
CLEANER_INTERNAL_URL = f"http://{config.SERVER_HOST}:{config.CLEANER_PORT}/internal"

@cleaner_bp.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Proxies the cleanup request to the standalone Cleaner Service."""
    try:
        logger.info("Forwarding manual cleanup request to internal service...")
        
        # Cleanup is usually fast, but we use a 5s timeout for safety
        resp = requests.post(f"{CLEANER_INTERNAL_URL}/cleanup", timeout=5)
        
        if resp.status_code == 200:
            return jsonify({
                "status": "success", 
                "message": "Cleanup completed successfully"
            }), 200
        else:
            return jsonify({
                "status": "error", 
                "message": "Internal cleaner error"
            }), 500

    except requests.exceptions.Timeout:
        return jsonify({
            "status": "warning", 
            "message": "Cleanup request timed out but may still be running"
        }), 202
    except Exception as e:
        logger.error(f"Failed to reach internal cleaner: {e}")
        return jsonify({
            "status": "error", 
            "message": "Cleaner service unreachable"
        }), 503
