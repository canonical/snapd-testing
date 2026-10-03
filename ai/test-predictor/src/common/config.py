import os

# Directories & Filenames Settings

STATE_DIR = os.environ.get('TEST_PREDICTOR_STATE_DIR', '').strip()


def state_path(relative_path):
    return os.path.join(STATE_DIR, relative_path) if STATE_DIR else relative_path


RESULTS_DIR = state_path('data/results')
TS_DIR = state_path('data/ts')
LOGS_DIR = state_path('logs')
MODEL_DIR = state_path('model')

# This file stores the "Memory" of all historical contexts for auditing and future analysis.
HISTORY_LOG = 'history_audit.jsonl'
# Cache snapshots used by the predictor and dependency analysis.
CACHE_SNAPSHOT = "cache_snapshot.pkl"
DEPENDENCY_CACHE_SNAPSHOT = "dependency_cache_snapshot.pkl"

# Data Settings

# How many historical results the probabilistic predictor retains per test.
CACHE_HISTORY_SIZE = 25
# Columns required by transformed result files.
MANDATORY_TS_COLUMNS = [ 
    'runid',
    'job_id',
    'run_id',
    'instance',
    'start',
    'duration_ms',
    'scenario',
    'attempt',
    'verb',
    'level',
    'backend',
    'system',
    'name',
    'success'
]
# Number of newest entries considered per PR; 0 disables the limit.
PR_RUNS_LIMIT = 2

# Prediction Settings

PREDICTION_MODEL_PROBABILISTIC = "probabilistic"
PREDICTION_MODEL = PREDICTION_MODEL_PROBABILISTIC

# API Settings

CACHE_REFRESH_INTERVAL_HOURS = 2
DEPENDENCY_CACHE_TTL_HOURS = 2
DEFAULT_SCENARIO = "generic"
DEFAULT_ATTEMPT = 1
DEFAULT_AUDIT = False

# Cleaner Settings

CLEANER_INTERVAL_HOURS = 24
FILE_RETENTION_DAYS = 14

# Server Settings

API_HOST = '127.0.0.1'
SERVER_HOST = '127.0.0.1'
# Ports can be overridden via env vars so an isolated instance (e.g. an
# end-to-end smoke test) can run alongside a live deployment without clashing.
API_PORT = int(os.environ.get('TEST_PREDICTOR_API_PORT', '5000'))
PREDICTOR_PORT = int(os.environ.get('TEST_PREDICTOR_PREDICTOR_PORT', '5001'))
TRAINER_PORT = int(os.environ.get('TEST_PREDICTOR_TRAINER_PORT', '5002'))
CLEANER_PORT = int(os.environ.get('TEST_PREDICTOR_CLEANER_PORT', '5003'))
DEPENDENCY_PORT = int(os.environ.get('TEST_PREDICTOR_DEPENDENCY_PORT', '5004'))
