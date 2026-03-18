import os
import threading
import glob

from flask import Blueprint, current_app, jsonify

from common import config
from common.config import setup_logging
from common.processor import process_and_train_batch

logger = setup_logging("tp-trainer-api")
trainer_bp = Blueprint('trainer', __name__)

def perform_training_cycle(app):
    with app.app_context():
        """Logic to find files, train, and update the global ModelManager."""
        manager = current_app.model_manager
        
        # Use the manager's lock to prevent concurrent training runs
        if manager.training_lock.locked():
            logger.warning("Training already in progress, skipping cycle.")
            return False

        with manager.training_lock:
            try:
                logger.info("Starting training cycle: Scanning for new result files...")
                
                # 1. Match the batch job behavior: find files and process
                pattern = os.path.join(config.RESULTS_DIR, "*.json")
                files = glob.glob(pattern)
                
                if not files:
                    logger.info("No new files found. Training cycle skipped.")
                    return False

                logger.info(f"Found {len(files)} files. Processing batch...")
                
                # 2. Run the heavy processing (This updates files on disk)
                # Assuming this function still handles the disk writes
                process_and_train_batch(files, config.TS_DIR, config.MODEL_DIR)
                
                # 3. Trigger the ModelManager to reload the new files into memory
                logger.info("Batch processing complete. Reloading model into memory...")
                success = manager.load_from_disk()
                
                if success:
                    logger.info(f"Training and reload successful. Systems now known: {len(manager.encoders['system'].classes_)}")
                    return True
                else:
                    logger.error("Training finished but ModelManager failed to reload files.")
                    return False
                    
            except Exception as e:
                logger.error(f"Training cycle failed: {e}", exc_info=True)
                return False

@trainer_bp.route('/train', methods=['POST'])
def manual_train():
    """Endpoint to trigger training manually in a background thread."""
    manager = current_app.model_manager
    
    if manager.training_lock.locked():
        logger.warning("Manual train requested but training is already active.")
        return jsonify({"status": "error", "message": "Training already in progress"}), 429

    # Get the real app object to pass to the thread
    app = current_app._get_current_object()

    # Run in a separate thread so the HTTP request doesn't timeout    
    thread = threading.Thread(target=perform_training_cycle, args=(app,))
    thread.start()
    
    logger.info("Manual training triggered via API.")
    return jsonify({"status": "success", "message": "Manual training triggered in background"}), 202

@trainer_bp.route('/status', methods=['GET'])
def get_status():
    """Check the current state of the model and training lock."""
    manager = current_app.model_manager
    model, encoders, last_updated = manager.get_state()
    
    status_data = {
        "training_active": manager.training_lock.locked(),
        "last_train_timestamp": last_updated,
        "systems_count": len(encoders['system'].classes_) if encoders else 0,
        "model_loaded": model is not None
    }
    
    logger.info(f"Status requested: {status_data}")
    return jsonify(status_data)
