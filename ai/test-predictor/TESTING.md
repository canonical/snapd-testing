# Testing Guide

## Prerequisites

- Python virtual environment activated
- Project root:

```bash
source .venv/bin/activate
cd ./ai/test-predictor
```

## Test Suite Overview

Main calibration tests are in:

- `src/tests/test_prediction_calibration.py`

Current coverage includes:

- Core scenario expected ranges (pass/fail transition anchors)
- Flaky-corner range checks for calibration branches
- Probability bounds over large generated scenario sweeps (100+)
- Recovery and deterioration monotonicity checks
- Aggregate trend-property checks over hundreds of generated patterns

## Run All Unit Tests

```bash
cd ./ai/test-predictor
PYTHONPATH=src python -m unittest -v src.tests.test_prediction_calibration
```

## Run a Single Test Method

Example: flaky corner ranges

```bash
cd ./ai/test-predictor
PYTHONPATH=src python -m unittest -v src.tests.test_prediction_calibration.TestPredictionCalibration.test_flaky_corner_ranges
```

## Quick Syntax Validation

```bash
cd ./ai/test-predictor
python -m py_compile \
  src/services/jobs/predictor.py \
  src/tests/test_prediction_calibration.py
```

## Notes

- Tests call `adjust_for_flaky_pattern` directly and do not require starting the API server.
- `PYTHONPATH=src` is required so imports like `services.jobs.predictor` resolve correctly.
- Range-based assertions are used for calibration behavior to keep tests robust to minor tuning changes.

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
