#!/usr/bin/env python3

import os
import glob
import argparse
from common import config
from common.processor import process_and_train_batch

def main():
    parser = argparse.ArgumentParser(description="Manual JSON to TS Converter & Trainer.")
    
    # 1. Allow custom input pattern (e.g. ./tests/*.json)
    parser.add_argument("--input", 
                        default=os.path.join(config.RESULTS_DIR, "*.json"),
                        help="Glob pattern for input JSON files")
    
    # 2. Allow overriding the model/ts directories
    parser.add_argument("--tsdir", default=config.TS_DIR)
    parser.add_argument("--modeldir", default=config.MODEL_DIR)
    
    # 3. Option to JUST convert without training
    parser.add_argument("--no-train", action="store_true", 
                        help="Skip training and archiving (stay in TS_DIR)")

    args = parser.parse_args()

    # Find the files based on input pattern
    inbox_files = glob.glob(args.input)
    
    if not inbox_files:
        print(f"[!] No files found matching: {args.input}")
        return

    print(f"[*] Manual Client: Found {len(inbox_files)} files.")

    # REUSE: The shared task logic
    # Note: If --no-train is passed, you might want to call a simpler function,
    # but here we use the shared batch processor for consistency.
    process_and_train_batch(
        json_files=inbox_files, 
        ts_dir=args.tsdir, 
        model_dir=args.modeldir
    )

if __name__ == "__main__":
    main()
