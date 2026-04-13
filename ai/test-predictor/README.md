# LSTM Test Success Predictor

This project uses an LSTM (Long Short-Term Memory) Neural Network to predict system test success probabilities. It analyzes historical data—Test Name (including Variants), Verb, Level, System, and Attempt—to identify high-risk scenarios and flaky tests.

## Project Overview

This project uses an LSTM (Long Short-Term Memory) Neural Network to predict system test success probabilities.
It analyzes historical data—Test Name (including Variants), Verb, Level, System, and Attempt—to identify
high-risk scenarios and flaky tests.

```text
.
├── client/                 # CLI tools for end-users
│   └── predict/            # Python script to analyze results via SSH tunnels
│
├── data/                   # Data lifecycle storage
│   ├── results/            # Incoming raw JSON files (ingestion layer)
│   ├── ts/                 # Cleaned time-series data (CSV) for training
│   └── processed/          # Archived data after successful training
│
├── deploy/                 # Systemd templates & management scripts
│   ├── *.service.template  # Templates for API, Predictor, and Trainer
│   └── *_service.sh        # Setup / restart / uninstall automation
│
├── model/                  # Stored models (.keras) and metadata (metadata.pkl)
│
├── src/
│   ├── common/             # Shared logic (config, model manager, processing)
│   └── services/
│       ├── api/            # Flask gateway (main entry points)
│       └── jobs/           # Internal services (Predictor, Trainer)
│
└── README.md
```

## The Three-Tier Architecture

The system is split into three independent services. This ensures "heavy" AI tasks never hang the "light" web API.

1. Main API Gateway (deploy/api.service.template)
Source: src/services/api/main.py (Ports 5000)
Role: The public "Front Door." It handles Ingestion (receiving JSONs), Explorer (fetching risks), and Trainer status.
Design: Zero-AI logic. It proxies heavy requests to the internal jobs via HTTP.

2. Standalone Predictor (deploy/predictor.service.template)
Source: src/services/jobs/predictor.py (Internal Port 5001)
Role: Persistent inference engine.
Logic: Loads the model once into memory. It stays responsive for /predict, /worst-systems, and /worst-tests.
Feature: Supports /internal/reload to swap model weights without restarting.

3. Standalone Trainer (deploy/trainer.service.template)
Source: src/services/jobs/trainer.py (Internal Port 5002)
Role: Background learning and data processing.
Logic:
Processor: Converts JSON from data/results/ to data/ts/ (merging name:variant).
Trainer: Fits the model on-demand using SEQUENCE_LENGTH history.
Cleanup: Moves files to data/processed/ and unloads model from RAM to free resources.

## Environment Setup

It is highly recommended to use a virtual environment to manage dependencies and avoid system conflicts.

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

Modify these variables to tune the "Brain":

```bash
SEQUENCE_LENGTH: History window (e.g., 50 runs).
LSTM_UNITS / DENSE_UNITS: Internal neuron counts (64/32).
EPOCHS: Training intensity (10 laps).
BATCH_SIZE: Training "bite" size (32 rows).
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
python src/client/train retrain
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

