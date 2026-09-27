#!/usr/bin/env python3
"""
Local training and testing harness for both the LSTM and ESN models.

Generates synthetic spread test data, trains both models, and runs
comparative predictions to validate the ensemble.

Usage:
    cd ai/test-predictor
    pip install -r .charm/requirements-app.txt -r .charm/requirements-ml.txt
    python -m scripts.local_train_test [--samples 5000] [--no-lstm] [--no-esn]
"""

import argparse
import json
import os
import sys
import time
import secrets

import numpy as np
import pandas as pd

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from common import config
from common.utils import setup_logging

logger = setup_logging("local-train-test")

# --- Synthetic Data Generation ---

SYSTEMS = [
    "ubuntu-24.04-64", "ubuntu-22.04-64", "ubuntu-20.04-64",
    "debian-12-64", "fedora-40-64", "opensuse-15.6-64",
]
BACKENDS = ["google", "qemu", "lxd"]
VERBS = ["preparing", "executing", "restoring"]
SCENARIOS = ["generic", "pr-validation", "nightly"]

# Test names with assigned flakiness profiles
TEST_PROFILES = {
    "tests/main/snap-mgmt": {"base_pass_rate": 0.98, "flakiness": 0.02},
    "tests/main/interfaces-network": {"base_pass_rate": 0.85, "flakiness": 0.15},
    "tests/main/interfaces-cups-control": {"base_pass_rate": 0.70, "flakiness": 0.30},
    "tests/main/snap-refresh": {"base_pass_rate": 0.95, "flakiness": 0.05},
    "tests/main/snap-connect": {"base_pass_rate": 0.92, "flakiness": 0.08},
    "tests/nested/manual/core20-early-config": {"base_pass_rate": 0.60, "flakiness": 0.40},
    "tests/main/snap-info": {"base_pass_rate": 0.99, "flakiness": 0.01},
    "tests/main/security-dev-input-event-denied": {"base_pass_rate": 0.75, "flakiness": 0.25},
    "tests/main/interfaces-broadcom-asic-control": {"base_pass_rate": 0.88, "flakiness": 0.12},
    "tests/regression/lp-12345": {"base_pass_rate": 0.50, "flakiness": 0.50},
}


def generate_synthetic_result(test_name, profile, system, backend, scenario, rng):
    """Generate a single synthetic test result with realistic temporal patterns."""
    # Flaky tests have probability that varies per-run
    noise = rng.normal(0, profile["flakiness"])
    effective_pass_rate = np.clip(profile["base_pass_rate"] + noise, 0, 1)
    success = int(rng.random() < effective_pass_rate)

    # Time generation
    start = pd.Timestamp("2025-01-01") + pd.Timedelta(seconds=rng.integers(0, 86400 * 180))
    duration_ms = int(rng.integers(5000, 900000))
    end = start + pd.Timedelta(milliseconds=duration_ms)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "verb": rng.choice(VERBS),
        "backend": backend,
        "system": system,
        "level": "task",
        "name": test_name,
        "variant": "",
        "instance": f"{backend}-{system}-{secrets.token_hex(3)}",
        "success": success,
        "aborted": False,
        "skipped": False,
    }


def generate_spread_json(n_items, rng):
    """Generate a complete spread results JSON file."""
    items = []
    test_names = list(TEST_PROFILES.keys())

    for _ in range(n_items):
        test_name = rng.choice(test_names)
        profile = TEST_PROFILES[test_name]
        system = rng.choice(SYSTEMS)
        backend = rng.choice(BACKENDS)
        scenario = rng.choice(SCENARIOS)

        item = generate_synthetic_result(test_name, profile, system, backend, scenario, rng)
        items.append(item)

    return {"items": items}


def generate_training_data(n_samples, output_dir):
    """Generate synthetic .ts files ready for training."""
    from common.processor import _clean_and_transform_data

    os.makedirs(output_dir, exist_ok=True)
    rng = np.random.default_rng(42)

    # Generate multiple JSON "runs" to create diverse .ts files
    items_per_file = max(50, n_samples // 10)
    n_files = max(1, n_samples // items_per_file)

    ts_files = []
    for i in range(n_files):
        scenario = rng.choice(SCENARIOS)
        attempt = 1
        data = generate_spread_json(items_per_file, rng)

        try:
            df, run_id = _clean_and_transform_data(data, scenario, attempt)
            filename = f"results_{run_id}_scenario_{scenario}_attempt_{attempt}.ts"
            filepath = os.path.join(output_dir, filename)
            # Use comma separator to match what model.py's read_csv expects
            df.to_csv(filepath, index=False)
            ts_files.append(filepath)
            logger.info("Generated %s (%d rows)", filename, len(df))
        except Exception as e:
            logger.warning("Failed to generate file %d: %s", i, e)

    logger.info("Generated %d .ts files with ~%d total samples.", len(ts_files), n_samples)
    return ts_files


# --- Training ---

def train_lstm(ts_files):
    """Train the LSTM model locally."""
    from common.model import ModelManager

    logger.info("=" * 60)
    logger.info("TRAINING LSTM MODEL")
    logger.info("=" * 60)

    os.makedirs(config.MODEL_DIR, exist_ok=True)
    model_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
    metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)

    manager = ModelManager(model_path, metadata_path)
    start_time = time.time()
    success = manager.train(ts_files)
    elapsed = time.time() - start_time

    if success:
        logger.info("LSTM training completed in %.1fs", elapsed)
    else:
        logger.error("LSTM training failed!")

    return success, elapsed


def train_esn(ts_files):
    """Train the ESN model locally."""
    from common.esn_model import ESNManager
    import pickle

    logger.info("=" * 60)
    logger.info("TRAINING ESN MODEL")
    logger.info("=" * 60)

    os.makedirs(config.MODEL_DIR, exist_ok=True)

    # Load LSTM encoders if available so both models encode identically
    lstm_encoders = None
    lstm_metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
    if os.path.exists(lstm_metadata_path):
        with open(lstm_metadata_path, 'rb') as f:
            lstm_encoders, _ = pickle.load(f)
        logger.info("ESN will reuse LSTM encoders for feature consistency.")

    manager = ESNManager()
    start_time = time.time()
    success = manager.train(ts_files, encoders=lstm_encoders)
    elapsed = time.time() - start_time

    if success:
        logger.info("ESN training completed in %.1fs", elapsed)
    else:
        logger.error("ESN training failed!")

    return success, elapsed


# --- Prediction Testing ---

def generate_test_set(n_predictions, rng):
    """
    Generate a proper held-out test set with diverse scenarios.
    Uses a different seed than training data to ensure no overlap.
    """
    test_cases = []
    test_names = list(TEST_PROFILES.keys())

    for _ in range(n_predictions):
        test_name = rng.choice(test_names)
        profile = TEST_PROFILES[test_name]
        system = rng.choice(SYSTEMS)

        # Generate a full history sequence for this test
        history = []
        for _ in range(config.SEQUENCE_LENGTH):
            noise = rng.normal(0, profile["flakiness"])
            p = np.clip(profile["base_pass_rate"] + noise, 0, 1)
            success = int(rng.random() < p)
            history.append(success)

        # The target is the NEXT outcome after the history
        noise = rng.normal(0, profile["flakiness"])
        p = np.clip(profile["base_pass_rate"] + noise, 0, 1)
        actual = int(rng.random() < p)

        test_cases.append({
            "test_name": test_name,
            "system": system,
            "profile": profile,
            "history": history,
            "actual": actual,
        })

    return test_cases


def run_predictions(ts_files, test_lstm=True, test_esn=True, n_predictions=200):
    """Run controlled comparative predictions from both models on a held-out test set."""
    from services.jobs.predictor import adjust_for_flaky_pattern

    logger.info("=" * 60)
    logger.info("RUNNING COMPARATIVE PREDICTIONS (%d test cases)", n_predictions)
    logger.info("=" * 60)

    # Load models
    lstm_manager = None
    esn_manager = None

    if test_lstm:
        from common.model import ModelManager
        model_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
        metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
        lstm_manager = ModelManager(model_path, metadata_path)
        if not lstm_manager._load_from_disk():
            logger.error("Cannot load LSTM model from disk.")
            test_lstm = False

    if test_esn:
        from common.esn_model import ESNManager
        esn_manager = ESNManager()
        if not esn_manager.load():
            logger.error("Cannot load ESN model from disk.")
            test_esn = False

    if not test_lstm and not test_esn:
        logger.error("No models available for prediction.")
        return

    # Generate held-out test set (seed=777, completely separate from training seed=42)
    rng = np.random.default_rng(777)
    test_cases = generate_test_set(n_predictions, rng)

    # Run predictions
    results = []

    for tc in test_cases:
        lstm_prob_raw = None
        lstm_prob_adjusted = None
        esn_prob = None

        # LSTM prediction with post-processing
        if test_lstm:
            model, encoders, _ = lstm_manager.get_state()
            if encoders and model:
                lstm_prob_raw = _predict_from_history(
                    tc["test_name"], tc["system"], tc["history"], model, encoders, "lstm"
                )
                if lstm_prob_raw is not None:
                    # Apply the same post-processing used in production
                    history_items = [{"success": v} for v in tc["history"]]
                    lstm_prob_adjusted = adjust_for_flaky_pattern(history_items, lstm_prob_raw)

        # ESN prediction (no post-processing — raw output)
        if test_esn:
            esn, encoders_esn, _ = esn_manager.get_state()
            if esn and encoders_esn:
                esn_prob = _predict_from_history(
                    tc["test_name"], tc["system"], tc["history"], esn, encoders_esn, "esn"
                )

        # Use adjusted LSTM prob for ensemble (matches production behavior)
        lstm_final = lstm_prob_adjusted if lstm_prob_adjusted is not None else lstm_prob_raw

        # Ensemble
        ensemble_prob = None
        if lstm_final is not None and esn_prob is not None:
            w = config.ESN_ENSEMBLE_WEIGHT
            ensemble_prob = (1 - w) * lstm_final + w * esn_prob
        elif lstm_final is not None:
            ensemble_prob = lstm_final
        elif esn_prob is not None:
            ensemble_prob = esn_prob

        results.append({
            "test": tc["test_name"],
            "system": tc["system"],
            "lstm_raw": lstm_prob_raw,
            "lstm_adjusted": lstm_prob_adjusted,
            "esn": esn_prob,
            "ensemble": ensemble_prob,
            "actual": tc["actual"],
            "expected_pass_rate": tc["profile"]["base_pass_rate"],
            "flakiness": tc["profile"]["flakiness"],
        })

    # --- Print detailed results (first 30 only to avoid flooding) ---
    print(f"\n{'TEST NAME':<45} | {'SYSTEM':<18} | {'LSTM(raw)':>9} | {'LSTM(adj)':>9} | {'ESN':>6} | {'ENSEMBLE':>8} | {'ACTUAL':>6}")
    print("-" * 125)

    for r in results[:30]:
        lr = f"{r['lstm_raw']*100:5.1f}%" if r['lstm_raw'] is not None else "  N/A  "
        la = f"{r['lstm_adjusted']*100:5.1f}%" if r['lstm_adjusted'] is not None else "  N/A  "
        es = f"{r['esn']*100:5.1f}%" if r['esn'] is not None else "  N/A "
        en = f"{r['ensemble']*100:5.1f}%" if r['ensemble'] is not None else "   N/A  "
        ac = "PASS" if r["actual"] else "FAIL"
        print(f"{r['test']:<45} | {r['system']:<18} | {lr:>9} | {la:>9} | {es} | {en} | {ac:>6}")

    if len(results) > 30:
        print(f"  ... ({len(results) - 30} more predictions not shown)")

    # --- Summary statistics ---
    print("\n" + "=" * 125)
    print("SUMMARY (%d predictions on held-out test set)" % len(results))
    print("=" * 125)

    def calc_metrics(preds_and_actuals, label):
        """Calculate accuracy, precision, recall, F1 for a list of (prob, actual) tuples."""
        if not preds_and_actuals:
            return
        tp = sum(1 for p, a in preds_and_actuals if p >= 0.5 and a == 1)
        tn = sum(1 for p, a in preds_and_actuals if p < 0.5 and a == 0)
        fp = sum(1 for p, a in preds_and_actuals if p >= 0.5 and a == 0)
        fn = sum(1 for p, a in preds_and_actuals if p < 0.5 and a == 1)

        accuracy = (tp + tn) / len(preds_and_actuals)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        print(f"  {label:<20s}: acc={accuracy*100:5.1f}%  prec={precision*100:5.1f}%  recall={recall*100:5.1f}%  F1={f1:.3f}  (n={len(preds_and_actuals)})")
        return accuracy

    if test_lstm:
        # Raw LSTM (no post-processing)
        lstm_raw_preds = [(r["lstm_raw"], r["actual"]) for r in results if r["lstm_raw"] is not None]
        calc_metrics(lstm_raw_preds, "LSTM (raw)")

        # LSTM with production post-processing
        lstm_adj_preds = [(r["lstm_adjusted"], r["actual"]) for r in results if r["lstm_adjusted"] is not None]
        calc_metrics(lstm_adj_preds, "LSTM (adjusted)")

    if test_esn:
        esn_preds = [(r["esn"], r["actual"]) for r in results if r["esn"] is not None]
        calc_metrics(esn_preds, "ESN")

    ens_preds = [(r["ensemble"], r["actual"]) for r in results if r["ensemble"] is not None]
    calc_metrics(ens_preds, "Ensemble")

    # Agreement
    both = [(r["lstm_adjusted"], r["esn"]) for r in results if r["lstm_adjusted"] is not None and r["esn"] is not None]
    if both:
        avg_agreement = 1.0 - np.mean([abs(l - e) for l, e in both])
        disagree_count = sum(1 for l, e in both if (l >= 0.5) != (e >= 0.5))
        print(f"\n  Model agreement:   {avg_agreement*100:.1f}% (disagree on {disagree_count}/{len(both)} predictions)")

    # --- Per-test breakdown ---
    print(f"\n{'TEST NAME':<45} | {'PASS RATE':>9} | {'FLAKY':>5} | {'LSTM acc':>8} | {'ESN acc':>7} | {'ENS acc':>7}")
    print("-" * 105)

    for test_name, profile in TEST_PROFILES.items():
        test_results = [r for r in results if r["test"] == test_name]
        if not test_results:
            continue

        n = len(test_results)
        lstm_correct = sum(1 for r in test_results if r["lstm_adjusted"] is not None and (r["lstm_adjusted"] >= 0.5) == bool(r["actual"]))
        esn_correct = sum(1 for r in test_results if r["esn"] is not None and (r["esn"] >= 0.5) == bool(r["actual"]))
        ens_correct = sum(1 for r in test_results if r["ensemble"] is not None and (r["ensemble"] >= 0.5) == bool(r["actual"]))

        lstm_n = sum(1 for r in test_results if r["lstm_adjusted"] is not None)
        esn_n = sum(1 for r in test_results if r["esn"] is not None)
        ens_n = sum(1 for r in test_results if r["ensemble"] is not None)

        lstm_acc_str = f"{lstm_correct/lstm_n*100:5.1f}%" if lstm_n else "  N/A "
        esn_acc_str = f"{esn_correct/esn_n*100:5.1f}%" if esn_n else " N/A "
        ens_acc_str = f"{ens_correct/ens_n*100:5.1f}%" if ens_n else " N/A "

        print(f"{test_name:<45} | {profile['base_pass_rate']*100:6.1f}%  | {profile['flakiness']*100:4.0f}% | {lstm_acc_str:>8} | {esn_acc_str:>7} | {ens_acc_str:>7}")

    return results


def _predict_from_history(test_name, system, history, model_or_esn, encoders, model_type):
    """Build a prediction from a success/fail history pattern."""
    from keras.utils import pad_sequences

    def get_id(key, value):
        enc = encoders.get(key)
        if enc is None:
            return 0.0
        try:
            encoded = float(enc.transform([str(value)])[0])
            max_index = max(len(enc.classes_) - 1, 1)
            return encoded / float(max_index)
        except (ValueError, KeyError):
            return 0.0

    # Build feature vectors for each timestep
    vectors = []
    for i, success in enumerate(history):
        vec = np.array([
            get_id('scenario', 'generic'),
            get_id('verb', 'executing'),
            get_id('backend', 'google'),
            get_id('system', system),
            get_id('name', test_name),
            float(success),
        ], dtype='float32')
        vectors.append(vec)

    # Mask the last timestep's success
    vectors[-1][-1] = config.CURRENT_SUCCESS_MASK_VALUE

    # Pad to sequence length
    X = np.zeros((config.SEQUENCE_LENGTH, config.NUM_FEATURES), dtype='float32')
    start = max(0, config.SEQUENCE_LENGTH - len(vectors))
    for i, vec in enumerate(vectors[-config.SEQUENCE_LENGTH:]):
        X[start + i] = vec

    try:
        if model_type == "lstm":
            X_input = X.reshape(1, config.SEQUENCE_LENGTH, config.NUM_FEATURES)
            prob = float(model_or_esn.predict(X_input, verbose=0)[0][0])
        else:
            prob = model_or_esn.predict(X)
        return max(0.0, min(1.0, prob))
    except Exception as e:
        logger.warning("Prediction failed for %s (%s): %s", test_name, model_type, e)
        return None


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Local training and testing for LSTM + ESN models")
    parser.add_argument("--samples", type=int, default=5000, help="Number of synthetic training samples")
    parser.add_argument("--test-size", type=int, default=200, help="Number of held-out test predictions")
    parser.add_argument("--no-lstm", action="store_true", help="Skip LSTM training/testing")
    parser.add_argument("--no-esn", action="store_true", help="Skip ESN training/testing")
    parser.add_argument("--predict-only", action="store_true", help="Skip training, only run predictions")
    parser.add_argument("--data-dir", default=None, help="Use existing .ts files from this directory")
    args = parser.parse_args()

    # Ensure output directories exist
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    os.makedirs(config.TS_DIR, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    # Get or generate training data
    if args.data_dir:
        import glob
        ts_files = glob.glob(os.path.join(args.data_dir, "*.ts"))
        if not ts_files:
            logger.error("No .ts files found in %s", args.data_dir)
            sys.exit(1)
        logger.info("Using %d existing .ts files from %s", len(ts_files), args.data_dir)
    elif not args.predict_only:
        logger.info("Generating %d synthetic training samples...", args.samples)
        ts_files = generate_training_data(args.samples, config.TS_DIR)
    else:
        ts_files = []

    # Train
    if not args.predict_only:
        if not args.no_lstm:
            lstm_ok, lstm_time = train_lstm(ts_files)
            print(f"\n  LSTM: {'OK' if lstm_ok else 'FAILED'} ({lstm_time:.1f}s)")

        if not args.no_esn:
            esn_ok, esn_time = train_esn(ts_files)
            print(f"  ESN:  {'OK' if esn_ok else 'FAILED'} ({esn_time:.1f}s)")

    # Predict
    run_predictions(ts_files, test_lstm=not args.no_lstm, test_esn=not args.no_esn, n_predictions=args.test_size)


if __name__ == "__main__":
    main()
