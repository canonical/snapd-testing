# Test Predictor

This project uses an LSTM (Long Short-Term Memory) Neural Network to predict system test success probabilities. It analyzes historical data—Test Name (including Variants), Verb, Level, System, and Attempt—to identify high-risk scenarios and flaky tests.
It also includes dependency analysis features to evaluate how failing tests correlate with other tests across systems and scenarios.

## Project Overview

This project uses an LSTM (Long Short-Term Memory) Neural Network to predict system test success probabilities.
It analyzes historical data—Test Name (including Variants), Verb, Level, System, and Attempt—to identify
high-risk scenarios and flaky tests.

```text
.
├── README.md
├── data/                   # Data lifecycle storage
│   ├── results/            # Incoming raw JSON files (ingestion layer)
│   └── ts/                 # Cleaned time-series CSV data for training
├── deploy/                 # Systemd templates & management scripts
│   ├── *.service.template  # API, predictor, trainer, cleaner, and dependency templates
│   ├── setup_project.sh    # One-shot environment + services bootstrap
│   ├── setup_services.sh   # Service setup and systemd wiring
│   ├── restart_services.sh
│   ├── stop_services.sh
│   └── uninstall_services.sh
├── logs/                   # Runtime log files
├── model/                  # Stored models (.keras) and metadata
└── src/
	├── client/             # End-user CLI tools (deps, explore, ingest, predict, stats, train)
	├── common/             # Shared logic (config, model manager, processing, utilities)
	└── services/
		├── api/            # Flask gateway routes and handlers
		└── jobs/           # Background predictor/trainer/cleaner services
```

## Service Architecture

The system is split into independent services so heavy model work and maintenance jobs never block the public API.

1. Main API Gateway (`deploy/api.service.template`)
Source: `src/services/api/main.py` (Port `5000`)
Role: Public entry point for ingestion, prediction/explorer, stats, trainer control, cleaner control, and dependency endpoints.
Design: Thin HTTP gateway that forwards compute-heavy requests to internal job services.

2. Standalone Predictor (`deploy/predictor.service.template`)
Source: `src/services/jobs/predictor.py` (Port `5001`)
Role: Persistent inference engine.
Logic: Loads model + metadata once, serves predictions and risk queries, and supports `/internal/reload` after model promotion.

3. Standalone Trainer (`deploy/trainer.service.template`)
Source: `src/services/jobs/trainer.py` (Port `5002`)
Role: Background training orchestrator.
Logic: Processes incoming JSON into `.ts`, trains in shadow mode, promotes artifacts atomically, and notifies predictor reload.

4. Standalone Cleaner (`deploy/cleaner.service.template`)
Source: `src/services/jobs/cleaner.py` (Port `5003`)
Role: Scheduled data/model hygiene.
Logic: Runs periodic cleanup/restore routines and exposes manual `/internal/cleanup` trigger.

5. Standalone Dependency Engine (`deploy/dependency.service.template`)
Source: `src/services/jobs/dependency.py` (Port `5004`)
Role: Dependency analytics service.
Logic: Serves pass/fail conditional dependency queries and manages dependency cache build/status endpoints.

## Environment Setup

It is highly recommended to use a virtual environment to manage dependencies and avoid system conflicts.

One-shot setup (recommended):

```bash
./deploy/setup_project.sh <username>
```

Example:

```bash
./deploy/setup_project.sh ubuntu
```

```bash
# Install the virtual environment package
sudo apt install python3.10-venv

# Create and activate the environment
python3 -m venv .venv
source .venv/bin/activate

# Install Core AI and Data stacks
pip install tensorflow pandas numpy scikit-learn

# Install Web and Task management
pip install flask gunicorn requests apscheduler

# Install for grander causality
pip install statsmodels networkx

```

## Model Configuration (src/common/config.py)

Tune these key variables in `src/common/config.py`:

```bash
SEQUENCE_LENGTH=15
LSTM_UNITS=64
SECOND_LSTM_UNITS=32
DENSE_UNITS=32
DROPOUT_RATE=0.2
ADAM_LEARNING_RATE=0.0001

EPOCHS=25
BATCH_SIZE=32
WEIGHT_CLASS='balanced'
WEIGHT_POSITIVE_CLASS=1.0
WEIGHT_NEGATIVE_CLASS=4.0
FOCAL_LOSS_GAMMA=2.0
FOCAL_LOSS_ALPHA=0.75

AUGMENT_PROB=0.5
AUGMENT_FAILURE_RATIO=0.25
AUGMENT_DETERIORATION_RATIO=0.20
AUGMENT_FLAKY_RATIO=0.45
AUGMENT_RECOVERY_RATIO=0.15
AUGMENT_STABLE_PASS_RATIO=0.35
```

Runtime service settings (train/cleanup intervals and ports) are also defined in the same file.

## Usage (REST API)

Public API base URL:
- `http://test-predictor.canonical.com:5000`

Note: if `test-predictor.canonical.com` does not resolve on your machine, add it to `/etc/hosts`:

```bash
echo "127.0.0.1 test-predictor.canonical.com" | sudo tee -a /etc/hosts
```

After that, verify resolution:

```bash
getent hosts test-predictor.canonical.com
```

### 1. Ingestion

Upload result JSON files to the ingestion layer.

```bash
curl -X POST "http://test-predictor.canonical.com:5000/ingest" \
	-F "file=@/path/to/results.json" \
	-F "job_id=12345" \
	-F "run_id=67890" \
	-F "scenario=generic" \
	-F "attempt=1"
```

### 2. Predictor / Explorer

```bash
curl "http://test-predictor.canonical.com:5000/predict?name=tests/smoke/foo&verb=install&system=ubuntu-core-24-64&scenario=generic&attempt=1"
curl "http://test-predictor.canonical.com:5000/predict-with-history?name=tests/smoke/foo&verb=install&system=ubuntu-core-24-64"
curl "http://test-predictor.canonical.com:5000/rank-risk?verb=install&system=ubuntu-core-24-64&limit=20"
curl "http://test-predictor.canonical.com:5000/worst-systems?name=tests/smoke/foo&verb=install&system=ubuntu-core-24-64"
curl "http://test-predictor.canonical.com:5000/list/names"
curl "http://test-predictor.canonical.com:5000/list/systems"
curl "http://test-predictor.canonical.com:5000/predict-pattern?name=tests/smoke/foo&verb=install&system=ubuntu-core-24-64&pattern=0,1,0,1,0,1"
```

### 3. Trainer

```bash
curl -X POST "http://test-predictor.canonical.com:5000/train"
curl "http://test-predictor.canonical.com:5000/status"
```

### 4. Stats

At least one filter is required (`name`, `system`, `attempt`, `scenario`, or `verb`).

```bash
curl "http://test-predictor.canonical.com:5000/stats?system=ubuntu-core-24-64&scenario=generic"
curl "http://test-predictor.canonical.com:5000/stats/all-systems?verb=install&scenario=generic"
```

### 5. Cleaner

```bash
curl -X POST "http://test-predictor.canonical.com:5000/cleanup"
```

### 6. Dependencies

```bash
curl "http://test-predictor.canonical.com:5000/dependencies/pass-given-fail?test=tests/smoke/foo&system=ubuntu-core-24-64&scenario=generic"
curl "http://test-predictor.canonical.com:5000/dependencies/fail-given-fail?test=tests/smoke/foo"
curl -X POST "http://test-predictor.canonical.com:5000/dependencies/cache/build?system=ubuntu-core-24-64"
curl -X POST "http://test-predictor.canonical.com:5000/dependencies/cache/build-all"
curl "http://test-predictor.canonical.com:5000/dependencies/cache/status"
```

## Usage (Client)

All CLI clients live in `src/client/`.

```bash
source .venv/bin/activate
```

By default, clients target:
- URL: `http://test-predictor.canonical.com`
- Port: `5000`

You can override these with `--url` and `--port` on each command.

### 1. `predict` - Predict success for failed items in a JSON file

```bash
python src/client/predict /path/to/results.json
python src/client/predict /path/to/results.json --attempt 2
```

### 2. `ingest` - Upload results JSON into the ingestion API

```bash
python src/client/ingest /path/to/results.json 12345 67890
python src/client/ingest /path/to/results.json 12345 67890 --scenario generic --attempt 1
```

Arguments:
- `file`: path to JSON results
- `job_id`: numeric job id
- `run_id`: numeric run id

### 3. `explore` - Query predictor/explorer endpoints

```bash
python src/client/explore --help
python src/client/explore risk --help
python src/client/explore compare --help
```

Available subcommands:
- `predict`
- `risk`
- `compare`
- `list`
- `context`
- `pattern`
- `test`

### 4. `stats` - Audit historical test results

```bash
python src/client/stats
python src/client/stats --system ubuntu-core-24-64 --scenario generic
python src/client/stats --name "tests/smoke/foo" --verb install
```

### 5. `train` - Control trainer service

```bash
python src/client/train start
python src/client/train status
```

### 6. `deps` - Dependency analysis client

Core dependency queries:

```bash
python src/client/deps pass-given-fail --test "tests/smoke/foo" --system ubuntu-core-24-64 --scenario generic
python src/client/deps pass-given-fail --test "tests/smoke/foo"
python src/client/deps fail-given-fail --test "tests/smoke/foo"
```

Sort options:
- `pass-given-fail`: `--sort {pass-prob,pass-count,pair-score}`
- `fail-given-fail`: `--sort {fail-prob,fail-count,pair-score}`

Cache operations:

```bash
python src/client/deps cache-build --system ubuntu-core-24-64
python src/client/deps cache-build-all
python src/client/deps cache-status
```

Use `--help` on any client or subcommand for full options:

```bash
python src/client/deps --help
python src/client/train --help
python src/client/explore --help
```

