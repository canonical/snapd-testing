import logging
import sys


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
