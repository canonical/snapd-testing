#!/usr/bin/env python3

import os
import pandas as pd
import shutil
import time
from common import config
from common.utils import setup_logging

logger = setup_logging("cleaner-job")

def cleanup_ts_files():
    ts_dir = config.TS_DIR
    
    # Calculate retention threshold in seconds
    # 86400 seconds in a day
    now = time.time()
    retention_seconds = config.FILE_RETENTION_DAYS * 86400
    threshold = now - retention_seconds

    logger.info(f"Starting cleanup in {ts_dir} (Retention: {config.FILE_RETENTION_DAYS} days)")

    files_deleted = 0
    try:
        for filename in os.listdir(ts_dir):
            file_path = os.path.join(ts_dir, filename)
            
            # Skip directories, only process files
            if os.path.isfile(file_path):
                # Get last modification time
                file_mtime = os.path.getmtime(file_path)
                
                if file_mtime < threshold:
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted old file: {filename}")
                        files_deleted += 1
                    except Exception as e:
                        logger.error(f"Failed to delete {filename}: {e}")
        
        logger.info(f"Cleanup finished. Total files deleted: {files_deleted}")
        
    except Exception as e:
        logger.error(f"Error accessing directory {ts_dir}: {e}")


def clean_ts_directory():
    """
    Ensures .ts files are model-ready: 
    - Deletes files missing mandatory columns.
    - Removes rows with empty strings or NaNs (especially empty 'name').
    - Enforces the correct column order.
    """
    ts_dir = config.TS_DIR
    if not os.path.exists(ts_dir):
        logger.error(f"Error: Directory '{ts_dir}' not found.")
        return

    ts_files = [f for f in os.listdir(ts_dir) if f.endswith('.ts')]
    
    for filename in ts_files:
        file_path = os.path.join(ts_dir, filename)
        try:
            df = pd.read_csv(file_path)
            initial_count = len(df)

            # VALIDATE: Delete file if a mandatory column is totally missing
            missing_cols = [c for c in config.MANDATORY_TS_COLUMNS if c not in df.columns]
            if missing_cols:
                logger.error(f"CRITICAL: {filename} missing {missing_cols}. Deleting.")
                os.remove(file_path)
                continue

            # CLEAN: Convert empty strings/whitespace to NA
            df = df.replace(r'^\s*$', pd.NA, regex=True)

            # FILTER: Drop rows with ANY missing mandatory value 
            # (This catches the empty 'name' fields in your logs)
            df_cleaned = df.dropna(subset=config.MANDATORY_TS_COLUMNS)

            # ENFORCE ORDER: Reorder columns to match MANDATORY_TS_COLUMNS exactly
            df_cleaned = df_cleaned[config.MANDATORY_TS_COLUMNS]

            # PERSIST: Save cleaned data or delete empty files
            if df_cleaned.empty:
                logger.warning(f"File {filename} is empty after cleaning. Deleting.")
                os.remove(file_path)
            elif len(df_cleaned) < initial_count:
                df_cleaned.to_csv(file_path, index=False)
                logger.info(f"Cleaned {filename}: Removed {initial_count - len(df_cleaned)} rows.")
            else:
                logger.info(f"Checked {filename}: OK.")

        except Exception as e:
            logger.error(f"Error processing {filename}: {e}. Removing corrupt file.")
            if os.path.exists(file_path):
                os.remove(file_path)

def cleanup_backups():
    old_models_dir = config.SHADOW_MODELS_DIR
    
    # Calculate retention threshold (86400 seconds = 1 day)
    now = time.time()
    retention_seconds = config.BACKUPS_RETENTION_DAYS * 86400
    threshold = now - retention_seconds

    logger.info(f"Starting backup cleanup in {old_models_dir} (Retention: {config.BACKUPS_RETENTION_DAYS} days)")

    backups_deleted = 0
    try:
        if not os.path.exists(old_models_dir):
            logger.warning(f"Directory not found: {old_models_dir}")
            return

        # Iterate through items in the shadow directory
        for item in os.listdir(old_models_dir):
            item_path = os.path.join(old_models_dir, item)
            
            # Check if it's a directory (timestamped backup folder)
            if os.path.isdir(item_path):
                # Get the last modification time of the folder
                folder_mtime = os.path.getmtime(item_path)
                
                if folder_mtime < threshold:
                    try:
                        # Recursively delete the entire backup directory
                        shutil.rmtree(item_path)
                        logger.info(f"Deleted old backup folder: {item}")
                        backups_deleted += 1
                    except Exception as e:
                        logger.error(f"Failed to delete backup folder {item}: {e}")
        
        logger.info(f"Backup cleanup finished. Total folders deleted: {backups_deleted}")
        
    except Exception as e:
        logger.error(f"Error accessing backup directory {old_models_dir}: {e}")

def cleanup_and_restore():
    # Run the existing cleanup logic first
    # Ensure this points to the correct directory defined in your config
    ts_dir = config.TS_DIR

    if not os.path.exists(ts_dir):
        os.makedirs(ts_dir)
        logger.info(f"Created TS directory: {ts_dir}")

    # Run the deletion part
    cleanup_backups()
    cleanup_ts_files()
    clean_ts_directory()
