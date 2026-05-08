
import gc
import glob
import shutil
import requests
import os
import threading

from datetime import datetime
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from common import config
from common.cleaner import cleanup_and_restore
from common.utils import setup_logging
from common.model import ModelManager
from common.cache import SystemStateCache
from common.processor import process_results, get_files_for_attempt

logger = setup_logging("trainer-server")
app = Flask(__name__)

# Initialize the manager once
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
app.model_manager = ModelManager(model_full_path, metadata_full_path)

def perform_training_cycle():
    """Main orchestration: Prepares data, trains in shadow, and swaps to live."""
    
    # Prevent concurrent runs
    if app.model_manager.training_lock.locked():
        logger.warning("Training already in progress, skipping cycle.")
        return False

    try:
        # Process raw JSON results into .ts files
        json_pattern = os.path.join(config.RESULTS_DIR, "*.json")
        json_files = glob.glob(json_pattern)
        if json_files:
            logger.info(f"Processing {len(json_files)} new JSON results...")
            process_results(json_files, config.TS_DIR)

        # Check for .ts training data
        ts_pattern = os.path.join(config.TS_DIR, "*.ts")
        ts_files = glob.glob(ts_pattern)
        if not ts_files:
            logger.info("No new .ts files found. Training skipped.")
            return True

        # Prefilter by attempt directly from filename, e.g.
        # results_..._scenario_generic_attempt_3.ts
        target_attempt = int(config.TRAINING_ATTEMPT_FILTER)
        filtered_ts_files = get_files_for_attempt(ts_files, target_attempt, extension="ts")

        logger.info(
            "Attempt prefilter: kept %d/%d files for attempt=%d",
            len(filtered_ts_files),
            len(ts_files),
            target_attempt,
        )

        if not filtered_ts_files:
            logger.info("No .ts files matched attempt=%d. Training skipped.", target_attempt)
            return True

        # Create Shadow Directory for this run
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        shadow_dir = os.path.join(config.SHADOW_MODELS_DIR, f"{timestamp}")
        os.makedirs(shadow_dir, exist_ok=True)

        logger.info(f"Starting Shadow Training in: {shadow_dir}")
        
        # Train model directly into the shadow directory
        # This creates model.h5 and metadata.pkl inside shadow_dir
        success = app.model_manager.train(filtered_ts_files, output_dir=shadow_dir)

        if not success:
            logger.error("Training failed in shadow directory.")
            return False
        
        # Re-prime the cache in the shadow environment
        # We use a fresh instance to scan the newly processed files
        logger.info("Generating shadow cache snapshot...")
        shadow_cache = SystemStateCache()
        shadow_cache.prime_from_disk(config.TS_DIR)
        # Save the shadow cache snapshot to the shadow directory
        shadow_cache.save_snapshot(output_dir=shadow_dir)

        # THE ATOMIC SWAP: Promote shadow assets to root model dir
        logger.info("Promoting shadow assets to LIVE...")
        promote_shadow_to_live(shadow_dir)

        logger.info("Refreshing in-memory model manager...")
        if app.model_manager._load_from_disk():
            logger.info("Successfully reloaded new model into memory.")
        else:
            logger.error("Failed to reload model after promotion!")

        # Notify Predictor asynchronously so this cycle does not block waiting
        # for a potentially slow/restarting predictor instance.
        threading.Thread(target=notify_predictor, daemon=True).start()

        # Local Cleanup
        gc.collect()
        return True
            
    except Exception as e:
        logger.error(f"Training cycle failed: {e}", exc_info=True)
        return False

def notify_predictor():
    logger.info("Notifying Predictor...")
    try:
        predictor_url = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/reload"
        resp = requests.post(predictor_url, json=None, timeout=(3, 10))
        if resp.status_code == 200:
            logger.info("Predictor successfully reloaded the new model.")
        else:
            logger.warning("Predictor acknowledged but failed to reload.")
    except Exception as e:
        logger.error(f"Could not reach Predictor to trigger reload: {e}")

def promote_shadow_to_live(shadow_dir):
    """Moves the finalized assets from shadow folder to the main model folder."""
    files = [config.MODEL_NAME, config.METADATA_NAME, config.CACHE_SNAPSHOT]
    for f in files:
        src = os.path.join(shadow_dir, f)
        dst = os.path.join(config.MODEL_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            logger.info(f"Promoted: {f}")

@app.route('/internal/train', methods=['POST'])
def trigger_train():
    success = perform_training_cycle()
    if success:
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "busy_or_failed"}), 429

@app.route('/internal/status', methods=['GET'])
def get_internal_status():
    manager = app.model_manager
    _, encoders, last_updated = manager.get_state()
    return jsonify({
        "training_active": manager.training_lock.locked(),
        "last_train_timestamp": last_updated
    })

scheduler = BackgroundScheduler(daemon=True)
# Adjust 'hours=6' or use config.TRAIN_INTERVAL_HOURS
scheduler.add_job(func=perform_training_cycle, trigger="interval", hours=config.TRAIN_INTERVAL_HOURS)
scheduler.start()

if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.TRAINER_PORT, threaded=True)
