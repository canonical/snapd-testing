import glob
import os
from flask import Flask, jsonify

from common import config
from common.config import setup_logging
from common.model import ModelManager
from common.processor import process_results

# Anti-hang settings for standalone TF
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

logger = setup_logging("tp-trainer-server")
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
            return False

        logger.info(f"Found {len(files)} ts files. Training...")
        success = app.model_manager.train(files, config.PROCESSED_DIR)        

        if success:
            if app.model_manager.encoders:
                count = len(manager.encoders['system'].classes_)
                logger.info(f"Training and reload successful. Systems now known: {count}")
    
            app.model_manager.unload_model()
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

if __name__ == "__main__":
    # Run without Gunicorn
    app.run(host=config.SERVER_HOST, port=config.TRAINER_PORT, threaded=True)
