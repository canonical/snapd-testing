import logging
import sys

# Directories
RESULTS_DIR = 'data/results'
PROCESSED_DIR = 'data/processed'
TS_DIR = 'data/ts'
MODEL_DIR = 'model'

# Model Settings
MODEL_NAME = 'test_predictor_lstm.keras'
METADATA_NAME = 'metadata.pkl'
EPOCHS = 10
BATCH_SIZE = 8
RETENTION_DAYS = 30

# EXPLORER Settings
RELOAD_INTERVAL_MINUTES = 30


def setup_logging(name):
    logger = logging.getLogger(name)
    # Only configure if it hasn't been set up yet
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        # Standard format for systemd logs
        formatter = logging.Formatter('%(name)s [%(levelname)s] %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger