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
