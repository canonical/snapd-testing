
import glob
import requests
import os
import threading

from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from common import config
from common.utils import setup_logging
from common.cache import SystemStateCache
from common.processor import process_results

logger = setup_logging("trainer-server")
app = Flask(__name__)

rebuild_lock = threading.Lock()

def perform_cache_refresh():
    """Process new results and rebuild the probabilistic history cache."""
    if not rebuild_lock.acquire(blocking=False):
        logger.warning("Cache rebuild already in progress, skipping cycle.")
        return False

    try:
        # Process raw JSON results into .ts files
        json_pattern = os.path.join(config.RESULTS_DIR, "*.json")
        json_files = glob.glob(json_pattern)
        if json_files:
            logger.info(f"Processing {len(json_files)} new JSON results...")
            process_results(json_files, config.TS_DIR)

        if not glob.glob(os.path.join(config.TS_DIR, "*.ts")):
            logger.info("No transformed results found. Cache rebuild skipped.")
            return True

        cache = SystemStateCache()
        cache.prime_from_disk(config.TS_DIR)
        if not cache.save_snapshot():
            logger.error("Failed to save rebuilt cache snapshot.")
            return False

        threading.Thread(target=notify_predictor, daemon=True).start()
        return True
            
    except Exception as e:
        logger.error(f"Cache refresh failed: {e}", exc_info=True)
        return False
    finally:
        rebuild_lock.release()

def notify_predictor():
    logger.info("Notifying Predictor...")
    try:
        predictor_url = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/reload"
        resp = requests.post(predictor_url, json=None, timeout=(3, 10))
        if resp.status_code == 200:
            logger.info("Predictor successfully reloaded the cache.")
        else:
            logger.warning("Predictor acknowledged but failed to reload.")
    except Exception as e:
        logger.error(f"Could not reach Predictor to trigger reload: {e}")

@app.route('/internal/train', methods=['POST'])
def trigger_cache_refresh():
    success = perform_cache_refresh()
    if success:
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "busy_or_failed"}), 429

@app.route('/internal/status', methods=['GET'])
def get_internal_status():
    return jsonify({
        "refresh_active": rebuild_lock.locked(),
        "last_refresh_timestamp": os.path.getmtime(
            os.path.join(config.MODEL_DIR, config.CACHE_SNAPSHOT)
        ) if os.path.exists(os.path.join(config.MODEL_DIR, config.CACHE_SNAPSHOT)) else None
    })

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=perform_cache_refresh, trigger="interval", hours=config.CACHE_REFRESH_INTERVAL_HOURS)
scheduler.start()

if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.TRAINER_PORT, threaded=True)
