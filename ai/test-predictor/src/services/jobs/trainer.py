import glob
import requests
import os

from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from common import config
from common.utils import setup_logging
from common.model import ModelManager
from common.processor import process_results

logger = setup_logging("trainer-server")
app = Flask(__name__)

# Initialize the manager once
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
app.model_manager = ModelManager(model_full_path, metadata_full_path)

def perform_training_cycle():

    """Logic to find files, train, and update the global ModelManager."""
    app.model_manager.load_or_build_model()
    
    # Use the manager's lock to prevent concurrent training runs
    if app.model_manager.training_lock.locked():
        logger.warning("Training already in progress, skipping cycle.")
        return False

    try:
        logger.info("Starting training cycle: Scanning for new result files...")
        
        # Find json files and process
        pattern = os.path.join(config.RESULTS_DIR, "*.json")
        files = glob.glob(pattern)
        
        if not files:
            logger.info("No new results json files found. Processing skipped.")
        else:
            logger.info(f"Found {len(files)} results json files. Processing...")
            process_results(files, config.TS_DIR)

        # Find ts files and train
        pattern = os.path.join(config.TS_DIR, "*.ts")
        files = glob.glob(pattern)

        if not files:
            logger.info("No new ts files found. Training skipped.")
            return True

        logger.info(f"Found {len(files)} ts files. Training...")
        success = app.model_manager.train(files, config.PROCESSED_DIR)        

        if success:
            if app.model_manager.encoders:
                count = len(app.model_manager.encoders['system'].classes_)
                logger.info(f"Training and reload successful. Systems now known: {count}")
    
            app.model_manager.unload_model()

            logger.info("Notifying Predictor...")
            try:
                predictor_url = f"http://{config.SERVER_HOST}:{config.PREDICTOR_PORT}/internal/reload"
                resp = requests.post(predictor_url, timeout=5)
                if resp.status_code == 200:
                    logger.info("Predictor successfully reloaded the new model.")
                else:
                    logger.warning("Predictor acknowledged but failed to reload.")
            except Exception as e:
                logger.error(f"Could not reach Predictor to trigger reload: {e}")

            logger.info("Notifying API...")
            try:
                api_url = f"http://{config.API_HOST}:{config.API_PORT}/reload"
                resp = requests.post(api_url, timeout=5)
                if resp.status_code == 200:
                    logger.info("API successfully reloaded the new model.")
                else:
                    logger.warning("API acknowledged but failed to reload.")
            except Exception as e:
                logger.error(f"Could not reach API to trigger reload: {e}")

            return True
        else:
            logger.error("Training finished but ModelManager failed to reload files.")
            return False
            
    except Exception as e:
        logger.error(f"Training cycle failed: {e}", exc_info=True)
        return False


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
# Adjust 'minutes=60' or use config.TRAIN_INTERVAL_MINUTES
scheduler.add_job(func=perform_training_cycle, trigger="interval", minutes=config.TRAIN_INTERVAL_MINUTES)
scheduler.start()

if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.TRAINER_PORT, threaded=True)
