import os
import time
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from common import config
from common.utils import setup_logging
from common.cleaner import cleanup_and_restore, cleanup_ts_files

logger = setup_logging("cleaner-server")
app = Flask(__name__)

@app.route('/internal/cleanup', methods=['POST'])
def trigger_cleanup():
    """Manual trigger for the cleanup job."""
    success = cleanup_and_restore()
    if success:
        return jsonify({"status": "success", "message": "Cleanup completed"}), 200
    return jsonify({"status": "error", "message": "Cleanup failed"}), 500


# Initialize scheduler
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(
    func=cleanup_and_restore, 
    trigger="interval", 
    hours=config.CLEANER_INTERVAL_HOURS
)
scheduler.start()

if __name__ == "__main__":
    app.run(host=config.SERVER_HOST, port=config.CLEANER_PORT, threaded=True)
