# Test Predictor

This project predicts system test success probabilities from configurable, recency-weighted history partitions without a trained model. Repeated results from the same PR are moved toward older partitions only when that PR's oldest and newest outcomes match.
It also includes dependency analysis features to evaluate how failing tests correlate with other tests across systems and scenarios.

## Project Overview

The predictor groups historical results by test context and calculates a weighted success ratio from the retained history.

```text
.
├── README.md
├── data/                   # Data lifecycle storage
│   ├── results/            # Incoming raw JSON files (ingestion layer)
│   ├── archive/            # Aborted source rows retained as CSV
│   └── ts/                 # Cleaned time-series CSV data for cache rebuilds
├── deploy/                 # Systemd templates & management scripts
│   ├── *.service.template  # API, predictor, trainer, cleaner, and dependency templates
│   ├── setup_project.sh    # One-shot environment + services bootstrap
│   ├── setup_services.sh   # Service setup and systemd wiring
│   ├── restart_services.sh
│   ├── stop_services.sh
│   └── uninstall_services.sh
├── logs/                   # Runtime log files
├── model/                  # Predictor and dependency cache snapshots
└── src/
	├── client/             # End-user CLI tools (deps, explore, ingest, predict, stats, train)
	├── common/             # Shared logic (config, cache, processing, utilities)
	└── services/
		├── api/            # Flask gateway routes and handlers
		└── jobs/           # Background predictor/trainer/cleaner services
```

## Prediction Algorithm

History is stored chronologically, from the oldest result to the newest. For
prediction only, the predictor copies that history and divides it from newest
to oldest into these default partitions:

| Partition | Results | Weight | Weight per result when full |
| --- | --- | --- | --- |
| A | newest 3 | 50% | $50\% / 3$ |
| B | next 3 | 30% | $30\% / 3$ |
| C | next 4 | 15% | $15\% / 4$ |
| D | all remaining older results | 5% | $5\% / x$ |

Before calculating the probability, the predictor compares the oldest and
newest retained results for each repeated PR. The PR is eligible for reordering
only when those two outcomes match: both PASS or both FAIL. When the outcomes
differ, all results from that PR keep their original positions because the
change in outcome carries useful chronological information.

Eligible results from the same PR are spread across partitions when possible.
The newest occurrence keeps its higher priority. An older duplicate is moved
into an older partition and replaced by a result whose PR is not already
present in the current partition. Results without a PR are independent and are
not treated as duplicates.

For example, if the newest partition contains `PR10 PR10 PR11`, the older
`PR10` is moved into partition B only if both `PR10` results are PASS or both
are FAIL. If one is PASS and the other is FAIL, neither is moved. This prevents
equivalent runs of one change from consuming the same partition's weight while
preserving meaningful result transitions. The configured `PR_RUNS_LIMIT` may
retain multiple results from one PR; with three retained occurrences, the
oldest and newest outcomes determine whether all three are eligible for
distribution across A, B, and C.

For each non-empty partition, the predictor calculates its success ratio and
multiplies it by the partition weight:

$$
P = \frac{\sum_i w_i \left(\frac{\text{successful results}_i}{\text{results}_i}\right)}
		 {\sum_i w_i}
$$

The denominator contains only non-empty partitions, so shorter histories are
normalized over the weights that are present. With no history, the prediction
is neutral at 50%.

PR reordering is temporary and affects prediction only. `/internal/context`
and `/predict-with-history` return results in their original chronological
order.

## Service Architecture

The system is split into independent services so cache processing and maintenance jobs never block the public API.

1. Main API Gateway (`deploy/api.service.template`)
Source: `src/services/api/main.py` (Port `5000`)
Role: Public entry point for ingestion, prediction/explorer, stats, trainer control, cleaner control, and dependency endpoints.
Design: Thin HTTP gateway that forwards compute-heavy requests to internal job services.

2. Standalone Predictor (`deploy/predictor.service.template`)
Source: `src/services/jobs/predictor.py` (Port `5001`)
Role: Persistent inference engine.
Logic: Loads the history cache, serves probabilistic predictions and risk queries, and supports internal cache reloads.

3. Standalone Trainer (`deploy/trainer.service.template`)
Source: `src/services/jobs/trainer.py` (Port `5002`)
Role: Background training orchestrator.
Logic: Every two hours, processes incoming JSON into `.ts`, rebuilds the history cache snapshot, and notifies the predictor to reload it. The `/train` endpoint triggers the same operation manually.

4. Standalone Cleaner (`deploy/cleaner.service.template`)
Source: `src/services/jobs/cleaner.py` (Port `5003`)
Role: Scheduled data hygiene.
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

# Install data processing dependencies
pip install pandas numpy

# Install Web and Task management
pip install flask gunicorn requests apscheduler

# Install for grander causality
pip install statsmodels networkx

```

## Predictor Configuration (src/common/config.py)

Key runtime settings in `src/common/config.py`:

```bash
CACHE_HISTORY_SIZE=25
PR_RUNS_LIMIT=2
PREDICTION_PART_SIZES=(3, 3, 4) # newest partitions; the oldest contains the remainder
PREDICTION_PART_WEIGHTS=(0.50, 0.30, 0.15, 0.05)
CACHE_REFRESH_INTERVAL_HOURS=2
```

Cleanup intervals, retention settings, and ports are also defined in the same file.

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

Each `history` entry returned by `predict-with-history` includes the ingestion
`job_id` and `run_id` for the corresponding result.

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
curl "http://test-predictor.canonical.com:5000/stats/all-targets?scenario=generic"
```

`PASS` counts successful `executing` rows. `FAIL` counts unsuccessful
`preparing`, `executing`, and `restoring` rows; `--verb` can limit failures to
one phase. Each test execution is counted at most once, and a failure takes
precedence over an `executing` pass. Failures are deduplicated by test name,
scenario, attempt, backend, system, `runid`, and `job_id`. `ABORTED` comes from
compact CSV archives containing only aborted rows. Pending files in
`data/results` are not included. `TOTAL` is the sum of these three categories,
and `RATE` uses that total. Archived files follow the configured data retention
period.

Training materializes daily pass, fail, and aborted counters by test, scenario,
attempt, backend, and system in `model/stats_snapshot.sqlite3`. Stats requests
compose those indexed counters; they scan retained processed and archived files
only when the snapshot is unavailable. The predictor's per-PR history limit
does not apply to statistics.

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
python src/client/stats --from 2026-10-01 --to 2026-10-14
```

When `--attempt` is omitted, statistics include results from every attempt.
`--from` and `--to` are optional inclusive dates in `YYYY-MM-DD` format, applied
to the result start date.

### 5. `train` - Process results and refresh the cache

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

## Deploy As A Charm

This project now includes a machine charm that deploys the API service with gunicorn.

### 1. Build the charm

```bash
cd ./ai/test-predictor
charmcraft pack
```

### 2. Deploy with Juju

```bash
juju deploy ./spread-tests-predictor_amd64.charm \
	--application-name test-predictor \
	--base ubuntu@24.04
```

To update an existing deployment after packing a new charm:

```bash
juju refresh test-predictor --path=./spread-tests-predictor_amd64.charm
```

Note: The application is not externally reachable until it is exposed.

```bash
juju expose test-predictor
```

If you need to pass proxy values manually at deploy time:

```bash
juju deploy ./spread-tests-predictor_amd64.charm \
	--application-name test-predictor \
	--base ubuntu@24.04 \
	--config http_proxy=http://egress.ps7.internal:3128 \
	--config https_proxy=http://egress.ps7.internal:3128 \
	--config no_proxy=127.0.0.1,127.0.0.53,localhost
```

### 3. Configure runtime options (optional)

```bash
juju config test-predictor port=5000 workers=1 threads=4 timeout=300
```

You can also set or change proxy settings after deployment:

```bash
juju config test-predictor \
	http_proxy=http://egress.ps7.internal:3128 \
	https_proxy=http://egress.ps7.internal:3128 \
	no_proxy=127.0.0.1,127.0.0.53,localhost
```

### 4. Check status

```bash
juju status test-predictor
juju debug-log --include test-predictor --replay
```

Notes:
- The charm creates a Python virtual environment on the unit and installs dependencies from `.charm/requirements-app.txt`.
- Proxy config (`http_proxy`, `https_proxy`, `no_proxy`) is exported to all services and synchronized to `/etc/environment` (both uppercase and lowercase variants).
- It manages a `test-predictor-api` systemd service.

