from flask import Flask, request, jsonify

from common import config
from common.dependency import DependencyManager
from common.utils import setup_logging

logger = setup_logging("dependency-engine")
app = Flask(__name__)

_manager = DependencyManager()

@app.route('/internal/dependencies/pass-given-fail', methods=['GET'])
def internal_pass_given_fail():
    test_name = request.args.get('test')
    system = request.args.get('system')
    scenario = request.args.get('scenario')
    use_cache = str(request.args.get('use_cache', 'true')).lower() in ('1', 'true', 'yes')
    include_self = str(request.args.get('include_self', 'false')).lower() in ('1', 'true', 'yes')

    if not test_name:
        return jsonify({"error": "missing required query param: test"}), 400

    try:
        result = _manager.get_pass_probabilities_given_fail(
            test_name=test_name,
            system=system,
            scenario=scenario,
            use_cache=use_cache,
            include_self=include_self,
        )
    except KeyError:
        return jsonify({"error": f"test not found in matrix: {test_name}"}), 404
    except Exception as e:
        logger.error(f"Pass-given-fail query failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    if not result:
        return jsonify({"error": "No data found for the given filters"}), 404

    return jsonify(result), 200


@app.route('/internal/dependencies/fail-given-fail', methods=['GET'])
def internal_fail_given_fail():
    test_name = request.args.get('test')
    system = request.args.get('system')
    scenario = request.args.get('scenario')
    use_cache = str(request.args.get('use_cache', 'true')).lower() in ('1', 'true', 'yes')
    include_self = str(request.args.get('include_self', 'false')).lower() in ('1', 'true', 'yes')

    if not test_name:
        return jsonify({"error": "missing required query param: test"}), 400

    try:
        result = _manager.get_fail_probabilities_given_fail(
            test_name=test_name,
            system=system,
            scenario=scenario,
            use_cache=use_cache,
            include_self=include_self,
        )
    except KeyError:
        return jsonify({"error": f"test not found in matrix: {test_name}"}), 404
    except Exception as e:
        logger.error(f"Fail-given-fail query failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    if not result:
        return jsonify({"error": "No data found for the given filters"}), 404

    return jsonify(result), 200


@app.route('/internal/dependencies/cache/build-all', methods=['POST'])
def internal_cache_build_all():
    try:
        response = _manager.trigger_cache_build_all()
        status_code = 202 if response.get("started") else 409
        return jsonify(response), status_code
    except Exception as e:
        logger.error(f"Cache build trigger failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/internal/dependencies/cache/build', methods=['POST'])
def internal_cache_build_system():
    system = request.args.get('system')
    if not system:
        return jsonify({"error": "missing required query param: system"}), 400

    try:
        response = _manager.trigger_cache_build_system(system)
        if response.get("started"):
            return jsonify(response), 202
        if "not found" in response.get("message", ""):
            return jsonify(response), 404
        return jsonify(response), 409
    except Exception as e:
        logger.error(f"System cache build trigger failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/internal/dependencies/cache/status', methods=['GET'])
def internal_cache_status():
    try:
        return jsonify(_manager.get_cache_build_status()), 200
    except Exception as e:
        logger.error(f"Cache status query failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.DEPENDENCY_PORT, threaded=True)
