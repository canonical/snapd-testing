#!/usr/bin/env python3
import glob, os, argparse, time
from common import config
from common.config import setup_logging
from common.processor import process_and_train_batch

logger = setup_logging("tp-process-job")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run as a continuous service")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between polls")
    args = parser.parse_args()

    pattern = os.path.join(config.RESULTS_DIR, "*.json")

    while True:
        files = glob.glob(pattern)
        if files:
            logger.info(f"[*] Found {len(files)} files...")
            process_and_train_batch(files, config.TS_DIR, config.MODEL_DIR)
        else:
            if not args.loop: 
                logger.info("[*] Inbox empty. Exiting.")
                break
        
        if not args.loop: break
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
