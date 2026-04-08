from flask import Flask, request, jsonify

from common import config
from common.dependency import DependencyManager
from common.utils import setup_logging

logger = setup_logging("dependency-engine")
app = Flask(__name__)

_manager = DependencyManager()

def serialize(result):
    """Convert internal result dict to JSON-safe plain types."""
    ranking = [{"test": t, "score": round(s, 4)} for t, s in result.get("ranking", [])]
    toxicity = [
        {"test": t, "toxicity": round(float(v), 4)}
        for t, v in result.get("toxicity", {}).items()
    ]
    edges = [
        {"cause": u, "effect": v, "prob": round(d["weight"], 4), "lift": round(d["lift"], 4)}
        for u, v, d in result["graph"].edges(data=True)
    ] if result.get("graph") else []
    pass_edges = [
        {"cause": u, "effect": v, "prob": round(d["weight"], 4), "lift": round(d["lift"], 4)}
        for u, v, d in result["graph_pass"].edges(data=True)
    ] if result.get("graph_pass") else []

    payload = {
        "matrix_shape": list(result["matrix"].shape) if result.get("matrix") is not None else [],
        "ranking":    ranking,
        "toxicity":   toxicity,
        "edges":      edges,
        "pass_edges": pass_edges,
    }

    granger_df = result.get("granger")
    if granger_df is not None:
        payload["granger"] = granger_df.to_dict(orient="records")
    granger_meta = result.get("granger_meta")
    if granger_meta is not None:
        payload["granger_meta"] = granger_meta

    return payload

@app.route('/internal/dependencies', methods=['GET'])
def internal_dependencies():
    system      = request.args.get('system')
    scenario    = request.args.get('scenario')
    run_granger = str(request.args.get('run_granger', 'false')).lower() in ('1', 'true', 'yes')
    use_cache   = str(request.args.get('use_cache', 'true')).lower() in ('1', 'true', 'yes')

    try:
        prob_threshold    = float(request.args.get('prob_threshold', 0.3))
        lift_threshold    = float(request.args.get('lift_threshold', 1.5))
        granger_max_tests = int(request.args.get('granger_max_tests', 80))
        if granger_max_tests <= 0:
            return jsonify({"error": "granger_max_tests must be > 0"}), 400
    except ValueError:
        return jsonify({"error": "prob_threshold/lift_threshold must be numbers and granger_max_tests must be integer"}), 400

    try:
        result = _manager.analyze(
            system=system,
            scenario=scenario,
            prob_threshold=prob_threshold,
            lift_threshold=lift_threshold,
            run_granger=run_granger,
            granger_max_tests=granger_max_tests,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.error(f"Dependency analysis failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    if not result:
        return jsonify({"error": "No data found for the given filters"}), 404

    return jsonify(serialize(result)), 200


@app.route('/internal/cache', methods=['GET'])
def cache_info():
    return jsonify(_manager.cache.stats()), 200


@app.route('/internal/cache', methods=['DELETE'])
def cache_clear():
    _manager.cache.clear()
    return jsonify({"message": "Cache cleared"}), 200


@app.route('/internal/cache', methods=['POST'])
def cache_save():
    _manager.cache.save()
    return jsonify({"message": "Cache saved to disk"}), 200


if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.DEPENDENCY_PORT, threaded=True)
