import logging
import os

from flask import Flask

from src.services.api.ingestion import ingestion_bp
from src.services.api.explorer import explorer_bp
from src.services.api.trainer import trainer_bp

from common import config
from common.config import setup_logging
from common.model import ModelManager

logger = setup_logging("tp-main-api")

# Initialize the model at startup
model_full_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
metadata_full_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
manager = ModelManager(model_full_path, metadata_full_path)
manager.load_or_build_model()

# Create one master app
app = Flask(__name__)
app.model_manager = manager

# Register all routes from both files
app.register_blueprint(ingestion_bp)
app.register_blueprint(trainer_bp)
app.register_blueprint(explorer_bp)
