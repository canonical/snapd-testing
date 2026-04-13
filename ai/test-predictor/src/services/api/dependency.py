import requests

from flask import Blueprint, jsonify, request

from common import config
from common.utils import setup_logging

logger = setup_logging("dependency-api")
dependency_bp = Blueprint('dependency', __name__)

DEPENDENCY_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies"
DEPENDENCY_PASS_GIVEN_FAIL_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies/pass-given-fail"
DEPENDENCY_FAIL_GIVEN_FAIL_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies/fail-given-fail"
CACHE_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/cache"
DEPENDENCY_CACHE_BUILD_ALL_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies/cache/build-all"
DEPENDENCY_CACHE_BUILD_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies/cache/build"
DEPENDENCY_CACHE_STATUS_URL = f"http://{config.SERVER_HOST}:{config.DEPENDENCY_PORT}/internal/dependencies/cache/status"


@dependency_bp.route('/dependencies/pass-given-fail', methods=['GET'])
def get_pass_given_fail():
    payload = {
        "test": request.args.get('test'),
        "system": request.args.get('system'),
        "scenario": request.args.get('scenario'),
        "use_cache": request.args.get('use_cache', 'true'),
        "include_self": request.args.get('include_self', 'false'),
    }

    try:
        response = requests.get(DEPENDENCY_PASS_GIVEN_FAIL_URL, params=payload, timeout=300)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency pass-given-fail communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502


@dependency_bp.route('/dependencies/fail-given-fail', methods=['GET'])
def get_fail_given_fail():
    payload = {
        "test": request.args.get('test'),
        "system": request.args.get('system'),
        "scenario": request.args.get('scenario'),
        "use_cache": request.args.get('use_cache', 'true'),
        "include_self": request.args.get('include_self', 'false'),
    }

    try:
        response = requests.get(DEPENDENCY_FAIL_GIVEN_FAIL_URL, params=payload, timeout=300)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency fail-given-fail communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502


@dependency_bp.route('/dependencies/cache/build-all', methods=['POST'])
def build_dependency_cache_all():
    try:
        response = requests.post(DEPENDENCY_CACHE_BUILD_ALL_URL, timeout=30)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency cache build trigger communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502


@dependency_bp.route('/dependencies/cache/build', methods=['POST'])
def build_dependency_cache_system():
    payload = {
        "system": request.args.get('system'),
    }
    try:
        response = requests.post(DEPENDENCY_CACHE_BUILD_URL, params=payload, timeout=30)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency system cache build trigger communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502


@dependency_bp.route('/dependencies/cache/status', methods=['GET'])
def dependency_cache_status():
    try:
        response = requests.get(DEPENDENCY_CACHE_STATUS_URL, timeout=30)
        return (response.content, response.status_code, response.headers.items())
    except Exception as e:
        logger.error(f"Dependency cache status communication error: {e}", exc_info=True)
        return jsonify({"error": "Dependency service unreachable"}), 502
