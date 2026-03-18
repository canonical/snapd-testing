import logging

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from src.services.api.ingestion import app as ingestion_app
from src.services.api.explorer import app as explorer_app
from src.services.api.trainer import app as trainer_app, perform_training_cycle

from common import config
from common.config import setup_logging
from common.model import ModelManager

logger = setup_logging("tp-main-api")

# Initialize at startup
manager = ModelManager(config.MODEL_PATH, config.METADATA_PATH)
manager.load_from_disk()

# Create one master app
app = Flask(__name__)
app.model_manager = manager

# Register all routes from both files
app.register_blueprint(ingestion_app)
app.register_blueprint(trainer_app)
app.register_blueprint(explorer_app)


def scheduled_training_job():
    """Background task to run every TRAIN_INTERVAL_MINUTES"""
    # We use a manual app context if we need to access current_app
    with app.app_context():
        logger.info("Scheduled Job: Checking for new data...")
        # Reuses the same logic as the /train endpoint
        perform_training_cycle()

# Initialize Scheduler
scheduler = BackgroundScheduler(daemon=True)
# Runs every TRAIN_INTERVAL_MINUTES
scheduler.add_job(scheduled_training_job, 'interval', minutes=config.TRAIN_INTERVAL_MINUTES)
scheduler.start()