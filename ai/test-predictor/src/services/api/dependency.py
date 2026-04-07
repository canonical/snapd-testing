import requests

from flask import Blueprint, jsonify, request

from common import config
from common.utils import setup_logging

logger = setup_logging("dependency-api")
dependency_bp = Blueprint('dependency', __name__)

DEPENDENCY_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies"


@dependency_bp.route('/dependencies', methods=['GET'])
def get_dependencies():
    payload = {
        "system": request.args.get('system'),
        "scenario": request.args.get('scenario'),
        "prob_threshold": request.args.get('prob_threshold', 0.3),
        "lift_threshold": request.args.get('lift_threshold', 1.5),
        "run_granger": request.args.get('run_granger', 'false'),
    }

    try:
        response = requests.get(DEPENDENCY_URL, params=payload, timeout=300)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency service communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502
