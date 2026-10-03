import threading

from flask import jsonify, request

from common import config
from common.cache import SystemStateCache
from common.utils import setup_logging


logger = setup_logging("predictor-cache")


def register_context_endpoint(app):
    rebuild_lock = threading.Lock()

    @app.route('/internal/context', methods=['GET'])
    def get_internal_context():
        """Expose the internal SystemStateCache to the external API."""
        system = request.args.get('system')
        name = request.args.get('name')
        verb = request.args.get('verb')
        backend = request.args.get('backend')
        level = request.args.get('level')
        scenario = request.args.get('scenario') or config.DEFAULT_SCENARIO

        if not system:
            return jsonify({"error": "System required"}), 400

        backend_filter = str(backend).strip() if backend is not None and str(backend).strip() else None
        history = app.state_cache.get_context(
            system, name, verb, None, scenario, backend_filter,
            max_items=config.CACHE_HISTORY_SIZE, level=level,
        )
        return jsonify({
            "system": system,
            "name": name,
            "level": level,
            "history": history,
            "model": config.PREDICTION_MODEL,
        }), 200

    @app.route('/internal/rebuild-cache', methods=['POST'])
    def rebuild_internal_cache():
        """Rebuild the cache from transformed files without retraining."""
        if not rebuild_lock.acquire(blocking=False):
            return jsonify({"error": "Cache rebuild already in progress"}), 409

        try:
            logger.info("Cache-only rebuild started from %s", config.TS_DIR)
            replacement = SystemStateCache(history_size=app.state_cache.history_size)
            replacement.prime_from_disk(config.TS_DIR)
            stats = replacement.stats()
            if stats['entries'] == 0:
                logger.error("Cache-only rebuild found no entries; keeping active cache")
                return jsonify({"error": "No cache entries found"}), 500

            if not replacement.save_snapshot():
                return jsonify({"error": "Failed to save cache snapshot"}), 500
            app.state_cache = replacement
            logger.info(
                "Cache-only replacement active: systems=%d tests=%d verb_buckets=%d "
                "entries=%d missing_provenance=%d provenance_samples=%s snapshot=%s",
                stats['systems'],
                stats['tests'],
                stats['verb_buckets'],
                stats['entries'],
                stats['missing_provenance'],
                stats['provenance_samples'],
                replacement.snapshot_path,
            )
            return jsonify({"status": "success", "cache": stats}), 200
        except Exception as error:
            logger.error("Cache-only rebuild failed: %s", error, exc_info=True)
            return jsonify({"error": str(error)}), 500
        finally:
            rebuild_lock.release()