# Directories & Filenames Settings

RESULTS_DIR = 'data/results'
TS_DIR = 'data/ts'
LOGS_DIR = 'logs'
MODEL_DIR = 'model'
SHADOW_MODELS_DIR = 'model/shadow_backups'

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
# File used to store a snapshot of the configuration at the time of training, which can be useful for debugging and traceability.
CONFIG_SNAPSHOT = "config_snapshot.pkl"
# This file stores the "Memory" of all training runs, including metrics and parameters, for auditing and future analysis.
TRAINING_STATS = "training_stats.jsonl"

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
# A slower learning rate helps the model "digest" the long-term dependencies in
# the 15-step sequence rather than just "memorizing" the most recent noise.
ADAM_LEARNING_RATE = 0.0001

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
GROUPED_BY_FEATURES = ['system', 'name']
ENCODED_FEATURES = ['verb', 'backend', 'system', 'name', 'scenario']

# Training Settings

# An Epoch is one full "lap" through your training data.
# The trainer looks at every single failed and successful test in your history 10 times.
# If you only do 1 epoch, the model is "distracted" and misses patterns. If you do 100,
# the model might "over-memorize" (Overfitting) and stop being able to predict new, unseen tests.
EPOCHS = 25
# This is how many test results the AI looks at simultaneously before updating its internal math.
# Large (e.g., 32 or 64): Learning is "smooth" and much faster, but requires more RAM.
BATCH_SIZE = 32
# Verbose (0): No output. 1: Progress bar. 2: One line per epoch.
TRAINING_VERBOSE = 0
# To prevent the trainer from getting overwhelmed, we can set a cap on how many files it processes in one go.
TRAINING_MAX_FILES = 150
# To prevent the trainer from getting overwhelmed, we can also set a cap on how many rows it processes in one go.
TRAINING_CHUNKS_SIZE = 30000
# The balanced class weight automatically adjusts based on the frequency of each class in the training data, while the positive and negative class weights allow for manual tuning. Adjusting these weights can help improve the model's ability to learn from imbalanced datasets, which is common in test results where successes may significantly outnumber failures (or vice versa).
WEIGHT_CLASS = 'balanced'
# Weights used during training to handle class imbalance. The model will "pay more attention" to the underrepresented class.
WEIGHT_POSITIVE_CLASS = 1.0
WEIGHT_NEGATIVE_CLASS = 4.0
# Focal Loss Parameters: These parameters help the model focus on harder-to-classify examples, which can be especially useful in imbalanced datasets.
# Gamma controls the focus on hard examples, while Alpha balances the importance of positive vs negative examples.
FOCAL_LOSS_GAMMA = 2.0
FOCAL_LOSS_ALPHA = 0.75

# Data Augmentation Settings

# Data Augmentation: These ratios control how much we "tweak" the data to create new training examples. By simulating failures, deteriorations, and recoveries, we can help the model learn more robust patterns. Adjusting these ratios can help the model better capture the variability in test results, especially if there are fluctuations in success rates.
AUGMENT_PROB = 0.5
AUGMENT_FAILURE_RATIO = 0.25
AUGMENT_DETERIORATION_RATIO = 0.20
AUGMENT_FLAKY_RATIO = 0.45
AUGMENT_RECOVERY_RATIO = 0.15
# Positive synthetic pattern to avoid collapsing into "history implies fail".
AUGMENT_STABLE_PASS_RATIO = 0.35
# Randomize identifier features in augmented samples using real, in-range IDs
# observed in training batches to reduce identity memorization.
AUGMENT_RANDOMIZE_IDENTIFIERS = True
AUGMENT_IDENTIFIER_FEATURES = ["name", "system"]
# Parameters used for injection algorithms. Adjusting these can help the model learn to recognize different patterns of failure and recovery.
AUGMENT_DETERIORATION_LENGTH = 3
AUGMENT_FAILURE_LENGTH = 3
AUGMENT_RECOVERY_LENGTH = 4
AUGMENT_FLAKY_PROB = 0.5
# Stochastic targets for synthetic patterns to avoid overconfident cliffs.
AUGMENT_FAILURE_SUCCESS_PROB = 0.05
AUGMENT_DETERIORATION_SUCCESS_PROB = 0.35
# Treat flaky behavior as high-risk: most synthetic flaky samples should fail.
AUGMENT_FLAKY_SUCCESS_PROB = 0.20
AUGMENT_RECOVERY_SUCCESS_PROB = 0.85
AUGMENT_STABLE_PASS_SUCCESS_PROB = 0.98

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
DEPENDENCY_PORT = 5004
