# Testing Guide

## Prerequisites

- Python virtual environment activated
- Project root:

```bash
source .venv/bin/activate
cd ./ai/test-predictor
```

## Test Suite Overview

Main prediction tests are in:

- `src/tests/test_probabilistic_prediction.py`

Current coverage includes:

- Exact weighted-partition calculations
- PR-aware history reordering
- Partial-history weight normalization
- Invalid partition configuration
- Probability bounds over generated pass/fail patterns

## Run All Unit Tests

```bash
cd ./ai/test-predictor
PYTHONPATH=src python -m unittest -v src.tests.test_probabilistic_prediction
```

## Run a Single Test Method

Example: exact partition weighting

```bash
cd ./ai/test-predictor
PYTHONPATH=src python -m unittest -v src.tests.test_probabilistic_prediction.TestProbabilisticPrediction.test_prediction_matches_direct_partition_sum
```

## Quick Syntax Validation

```bash
cd ./ai/test-predictor
python -m py_compile \
  src/services/jobs/predictor.py \
  src/tests/test_probabilistic_prediction.py
```

## Notes

- Unit tests call the partition and prediction functions directly and do not require starting the API server.
- `PYTHONPATH=src` is required so imports like `services.jobs.predictor` resolve correctly.
- Exact assertions verify the configured partition arithmetic.

## End-to-End Smoke Test (run before deploy)

`src/tests/e2e_smoke.py` boots the internal predictor service and the external
API service as real HTTP servers on isolated ports, seeds a small synthetic
history, and exercises the full probabilistic request chain, including legacy
model rejection and full cache-depth context. It does not touch the live
deployment: it uses a temp `STATE_DIR` and dedicated ports (5100/5101 by
default).

```bash
cd ./ai/test-predictor
.venv/bin/python3 src/tests/e2e_smoke.py
```

Exits non-zero if any check fails. No trained model or metadata is required.

`/rank-risk` and `/worst-systems` are intentionally not exercised here since
they iterate every known name/system and are too slow for a quick smoke test;
their request/response contract is covered by the checks against `/predict`,
`/predict-with-history`, and `/predict-pattern` instead.
