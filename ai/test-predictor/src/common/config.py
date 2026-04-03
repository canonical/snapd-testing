# Directories
RESULTS_DIR = 'data/results'
TS_DIR = 'data/ts'
LOGS_DIR = 'logs'
MODEL_DIR = 'model'
SHADOW_MODELS_DIR = 'model/shadow_backups'

# Model Settings
# Units (64 or 128): The number of memory cells in the LSTM layer.
LSTM_UNITS = 64
# Units (32): The number of memory cells in the second LSTM layer, if used.
SECOND_LSTM_UNITS = 32
# Dropout (0.2): Percentage of neurons to ignore during training to prevent overfitting.
DROPOUT_RATE = 0.2
# Neurons (32): The size of the decision-making Dense layer.
DENSE_UNITS = 32
# 'relu' is the industry standard for hidden layers.
HIDDEN_ACTIVATION = 'relu'
# 'sigmoid' is standard for 0.0 to 1.0 probability.
OUTPUT_ACTIVATION = 'sigmoid' 
# For binary classification (Success/Failure), this MUST be 1.
OUTPUT_UNITS = 1
# Adam Learning Rate: A smaller learning rate can lead to more stable training.
ADAM_LEARNING_RATE = 0.0005

# Data Settings

# Sequence Length (1): How many historical runs to look at. 
# 1 = Current run only. 10 = Look at the last 10 results.
SEQUENCE_LENGTH = 15
# This list must match the EXACT order used during model.fit() as in the .ts files
FEATURE_COLUMNS = [
    'scenario', 
    'attempt', 
    'verb', 
    'backend', 
    'system', 
    'name',
    'success'
]
# The "width" of the data. It tells the AI exactly how many different pieces of information it gets for every single run.
# Attempt number, Verb (encoded), Backend (encoded), System (encoded), Name (encoded) and Scenario (encoded).
NUM_FEATURES = len(FEATURE_COLUMNS)
# This is the minimum set of columns that must be present in the .ts files for the model to train and predict correctly.
MANDATORY_TS_COLUMNS = [ 
    'runid',
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

# Training Settings

# An Epoch is one full "lap" through your training data.
# The trainer looks at every single failed and successful test in your history 10 times.
# If you only do 1 epoch, the model is "distracted" and misses patterns. If you do 100,
# the model might "over-memorize" (Overfitting) and stop being able to predict new, unseen tests.
EPOCHS = 40
# This is how many test results the AI looks at simultaneously before updating its internal math.
# Large (e.g., 32 or 64): Learning is "smooth" and much faster, but requires more RAM.
BATCH_SIZE = 32
# Verbose (0): No output. 1: Progress bar. 2: One line per epoch.
TRAINING_VERBOSE = 0
# To prevent the trainer from getting overwhelmed, we can set a cap on how many files it processes in one go.
TRAINING_MAX_FILES = 200
# To prevent the trainer from getting overwhelmed, we can also set a cap on how many rows it processes in one go.
TRAINING_CHUNKS_SIZE = 30000
# Weights used during training to handle class imbalance. The model will "pay more attention" to the underrepresented class.
WEIGHT_POSITIVE_CLASS = 1.0
WEIGHT_NEGATIVE_CLASS = 1.0
# Focal Loss Parameters: These parameters help the model focus on harder-to-classify examples, which can be especially useful in imbalanced datasets. Gamma controls the focus on hard examples, while Alpha balances the importance of positive vs negative examples.
FOCAL_LOSS_GAMMA = 2.0
FOCAL_LOSS_ALPHA = 0.25
# Data Augmentation: These ratios control how much we "tweak" the data to create new training examples. By simulating failures, deteriorations, and recoveries, we can help the model learn more robust patterns. Adjusting these ratios can help the model better capture the variability in test results, especially if there are fluctuations in success rates.
AUGMENT_PROB = 0.1
AUGMENT_FAILURE_RATIO = 0.2
AUGMENT_DETERIORATION_RATIO = 0.3
AUGMENT_RECOVERY_RATIO = 0.5
# This is the minimum success rate in the recent history for a run to be considered successful. If the success rate in the last 5 runs is above 60%, we label it as a success (1), otherwise as a failure (0). Adjusting this threshold can help the model better capture patterns in the data, especially if there are fluctuations in test results.
LABEL_THRESHOLD = 0.6
LABEL_WINDOW = 5

# Prediction Settings
# Verbose (0): No output. 1: Progress bar. 2: One line per epoch.
PREDICTION_VERBOSE = 1

# API Settings
TRAIN_INTERVAL_HOURS = 6
DEFAULT_SCENARIO = "generic"
DEFAULT_ATTEMPT = 1
DEFAULT_AUDIT = False

# Cleaner Settings
CLEANER_INTERVAL_HOURS = 24
FILE_RETENTION_DAYS = 7
BACKUPS_RETENTION_DAYS = 7

# Server Settings
API_HOST = '127.0.0.1'
SERVER_HOST = '127.0.0.1'
API_PORT = 5000
PREDICTOR_PORT = 5001
TRAINER_PORT = 5002
CLEANER_PORT = 5003


# Filenames Settings
#This file stores the "Brain" (the weights/math). It tells the AI how to predict.
MODEL_NAME = 'test_predictor_lstm.keras'
# This file stores the "Translation Dictionary" (Encoders)
METADATA_NAME = 'metadata.pkl'
# This file stores the "Memory" of all predictions for auditing and future analysis.
PREDICTION_LOG = 'prediction_audit.jsonl'
# This file stores the "Memory" of all historical contexts for auditing and future analysis.
HISTORY_LOG = 'history_audit.jsonl'
# These files store snapshots of the cache and config for traceability and debugging.
CACHE_SNAPSHOT = "cache_snapshot.pkl"
CONFIG_SNAPSHOT = "config_snapshot.pkl"
TRAINING_STATS = "training_stats.jsonl"
