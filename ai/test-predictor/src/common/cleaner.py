#!/usr/bin/env python3

import os
import shutil
import time
from common import config
from common.config import setup_logging

logger = setup_logging("cleaner-job")

def cleanup_processed_files():
    # Construct the path to the processed directory
    processed_dir = os.path.join(config.PROCESSED_DIR, "processed")
    
    if not os.path.exists(processed_dir):
        logger.warning(f"Directory not found: {processed_dir}")
        return

    # Calculate retention threshold in seconds
    # 86400 seconds in a day
    now = time.time()
    retention_seconds = config.FILE_RETENTION_DAYS * 86400
    threshold = now - retention_seconds

    logger.info(f"Starting cleanup in {processed_dir} (Retention: {config.FILE_RETENTION_DAYS} days)")

    files_deleted = 0
    try:
        for filename in os.listdir(processed_dir):
            file_path = os.path.join(processed_dir, filename)
            
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
        logger.error(f"Error accessing directory {processed_dir}: {e}")


def cleanup_and_restore():
    # Run the existing cleanup logic first
    # Ensure this points to the correct directory defined in your config
    processed_dir = config.PROCESSED_DIR 
    ts_dir = config.TS_DIR

    if not os.path.exists(processed_dir):
        logger.warning(f"Directory not found: {processed_dir}")
        return

    if not os.path.exists(ts_dir):
        os.makedirs(ts_dir)
        logger.info(f"Created TS directory: {ts_dir}")

    # Run the deletion part
    cleanup_processed_files()

    # Restore remaining files to TS_DIR
    logger.info(f"Restoring remaining files from {processed_dir} to {ts_dir}...")
    
    files_restored = 0
    try:
        # Get list of files left after cleanup
        remaining_files = [f for f in os.listdir(processed_dir) 
                          if os.path.isfile(os.path.join(processed_dir, f))]

        for filename in remaining_files:
            src_path = os.path.join(processed_dir, filename)
            dest_path = os.path.join(ts_dir, filename)

            try:
                # Use move to transfer the file back to the training queue
                shutil.move(src_path, dest_path)
                files_restored += 1
            except Exception as e:
                logger.error(f"Failed to move {filename}: {e}")

        logger.info(f"Restore finished. Total files moved back to TS: {files_restored}")

    except Exception as e:
        logger.error(f"Error during restoration: {e}")
