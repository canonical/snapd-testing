import requests

from flask import Blueprint, jsonify, request

from common import config
from common.utils import setup_logging

logger = setup_logging("dependency-api")
dependency_bp = Blueprint('dependency', __name__)

DEPENDENCY_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies"
CACHE_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/cache"


@dependency_bp.route('/dependencies', methods=['GET'])
def get_dependencies():
    payload = {
        "system": request.args.get('system'),
        "scenario": request.args.get('scenario'),
        "prob_threshold": request.args.get('prob_threshold', 0.3),
        "lift_threshold": request.args.get('lift_threshold', 1.5),
        "run_granger": request.args.get('run_granger', 'false'),
        "granger_max_tests": request.args.get('granger_max_tests', 80),
        "use_cache": request.args.get('use_cache', 'true'),
    }

    try:
        response = requests.get(DEPENDENCY_URL, params=payload, timeout=300)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency service communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502


@dependency_bp.route('/cache', methods=['GET', 'POST', 'DELETE'])
def manage_cache():
    """Proxy cache management endpoints (GET=info, POST=save, DELETE=clear)."""
    try:
        if request.method == 'GET':
            response = requests.get(CACHE_URL, timeout=30)
        elif request.method == 'POST':
            response = requests.post(CACHE_URL, timeout=30)
        else:  # DELETE
            response = requests.delete(CACHE_URL, timeout=30)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Cache service communication error: {e}", exc_info=True)
        return jsonify({"error": "Cache service unreachable"}), 502
