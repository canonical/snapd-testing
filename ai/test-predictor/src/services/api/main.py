import logging
import os

from flask import Flask

from src.services.api.ingestion import ingestion_bp
from src.services.api.predictor import predictor_bp
from src.services.api.trainer import trainer_bp
from src.services.api.stats import stats_bp
from src.services.api.cleaner import cleaner_bp
from src.services.api.dependency import dependency_bp
from src.services.api.esn import esn_bp

from common.utils import setup_logging

logger = setup_logging("main-api")

# Create one master app
app = Flask(__name__)

# Register all routes from both files
app.register_blueprint(ingestion_bp)
app.register_blueprint(trainer_bp)
app.register_blueprint(predictor_bp)
app.register_blueprint(stats_bp)
app.register_blueprint(cleaner_bp)
app.register_blueprint(dependency_bp)
app.register_blueprint(esn_bp)
