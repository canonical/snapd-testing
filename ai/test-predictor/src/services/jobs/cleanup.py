#!/usr/bin/env python3

import os
import time
import glob
import json
import pandas as pd
from common import config
from common.config import setup_logging
from common.processor import ( extract_attempt, clean_and_transform_data)
from common.trainer import (
    get_metadata, save_metadata, preprocess_dataframe, 
    prepare_sequences, build_or_load_model
)
 
logger = setup_logging("tp-cleanup-job")

def cleanup_processed_files():
    """Deletes JSON files in PROCESSED_DIR older than RETENTION_DAYS."""
    logger.info(f"[*] Cleaning PROCESSED_DIR (> {config.RETENTION_DAYS} days)...")
    cutoff = time.time() - (config.RETENTION_DAYS * 86400)
    files = glob.glob(os.path.join(config.PROCESSED_DIR, "*.json"))
    
    count = 0
    for f in files:
        if os.path.getmtime(f) < cutoff:
            os.remove(f)
            count += 1
    logger.info(f"[SUCCESS] Deleted {count} expired result files.")

def rebuild_model_memory():
    """Wipes the model and retrains it using ONLY the valid archived data."""
    logger.info(f"[*] Rebuilding Model 'Memory' (Retention: {config.RETENTION_DAYS} days)...")
    
    # 1. Paths
    model_path = os.path.join(config.MODEL_DIR, config.MODEL_NAME)
    metadata_path = os.path.join(config.MODEL_DIR, config.METADATA_NAME)
    archived_jsons = glob.glob(os.path.join(config.PROCESSED_DIR, "*.json"))

    if not archived_jsons:
        logger.warning("[!] No archived data found. Cannot rebuild.")
        return

    # 2. Total Wipe: Delete old model and metadata to start fresh
    for path in [model_path, metadata_path]:
        if os.path.exists(path):
            os.remove(path)

    # 3. Re-process JSONs into a temporary DataFrame
    all_dfs = []
    for json_path in archived_jsons:
        try:
            filename = os.path.basename(json_path)
            attempt = extract_attempt(filename)
            with open(json_path, 'r', encoding='utf-8') as f:
                df, _ = clean_and_transform_data(json.load(f), attempt)
                all_dfs.append(df)
        except Exception as e:
            logger.warning(f"[!] Skipping corrupted archive {filename}: {e}")

    if not all_dfs:
        logger.warning("[!] No valid data recovered from archives.")
        return

    combined_df = pd.concat(all_dfs, ignore_index=True)

    # 4. Fresh Preprocessing (New Encoders/Scalers)
    # Passing empty dict/new scaler to get_metadata because we deleted metadata_path
    encoders, scaler = get_metadata(metadata_path) 
    processed_df = preprocess_dataframe(combined_df, encoders, scaler)
    save_metadata(metadata_path, encoders, scaler)

    # 5. Fresh Training
    X, y = prepare_sequences(processed_df)
    
    logger.info(f"[*] Training fresh model on {len(archived_jsons)} archived samples...")
    # build_or_load_model creates a NEW model because we deleted the file in step 2
    model = build_or_load_model(model_path, (X.shape[1], X.shape[2]))
    model.fit(X, y, epochs=10, batch_size=8, verbose=1)
    
    model.save(model_path)
    logger.info(f"[SUCCESS] Model memory rebuilt using data from the last {config.RETENTION_DAYS} days.")

if __name__ == "__main__":
    cleanup_processed_files()
    rebuild_model_memory()
