import os, json, re, secrets
import pandas as pd

from common import config
from common.utils import setup_logging

logger = setup_logging("data-processor")

def _extract_scenario(filename):
    match = re.search(r"scenario_([a-zA-Z0-9-]+)", filename)
    return match.group(1) if match else config.DEFAULT_SCENARIO

def _extract_attempt(filename):
    match = re.search(r"attempt_(\d+)", filename)
    return int(match.group(1)) if match else config.DEFAULT_ATTEMPT

def _clean_and_transform_data(raw_data, scenario, attempt):
    """
    Core logic to transform raw JSON items into the specific TS format.
    """
    run_id = secrets.token_hex(4)

    if 'items' not in raw_data:
        logger.error("Invalid data format: 'items' key missing")
        raise ValueError("Invalid data format: 'items' key missing")

    df = pd.DataFrame(raw_data['items'])

    # CLEANING
    df = df.replace(r'^\s*$', pd.NA, regex=True)

    # Ensure name and variant are treated as strings to avoid errors
    df['name'] = df['name'].fillna('').astype(str)
    df['variant'] = df['variant'].fillna('').astype(str)

    # --- NEW VARIANT CONCATENATION LOGIC ---
    # Combine name and variant only if variant is not empty
    df['name'] = df.apply(
        lambda x: f"{x['name']}:{x['variant']}" if x['variant'] else x['name'], 
        axis=1
    )

    df = df.dropna(subset=['instance', 'start', 'end', 'verb', 'name'])
    df = df[df['verb'] != 'checking'].copy()
    
    # METADATA
    df['runid'] = run_id
    df['scenario'] = scenario 
    df['attempt'] = attempt

    # FILTER
    if 'aborted' in df.columns:
        df = df[df['aborted'] == False].copy()
        logger.info(f"Filtered out aborted items. Remaining items: {len(df)}")

    # TIME PROCESSING
    start_dt = pd.to_datetime(df['start'])
    end_dt = pd.to_datetime(df['end'])
    df['duration_ms'] = ((end_dt - start_dt).dt.total_seconds() * 1000).round(0).astype(int)

    # DATA TYPES
    if 'success' in df.columns:
        df['success'] = df['success'].astype(int)

    # SORTING
    df['start_dt'] = start_dt 
    df = df.sort_values(by=['instance', 'start_dt'])

    # FINAL COLUMN ORDER
    final_cols = [c for c in config.FEATURE_COLUMNS if c in df.columns]

    logger.info(f"Transformed data for run_id={run_id} with {len(df)} items and columns: {final_cols}")
    return df[final_cols], run_id


def process_results(json_files, ts_dir):
    """The heavy lifting: Convert -> Train -> Archive"""
    logger.info(f"Processing batch of {len(json_files)} files...")
    
    ts_batch_paths = []    
    # Conversion
    for json_path in json_files:
        filename = os.path.basename(json_path)
        ts_path = os.path.join(ts_dir, filename.replace(".json", ".ts"))
        
        try:
            attempt = _extract_attempt(filename)
            scenario = _extract_scenario(filename)
            with open(json_path, 'r') as f:
                df, _ = _clean_and_transform_data(json.load(f), scenario, attempt)
            df.to_csv(ts_path, index=False)

            os.remove(json_path)
            logger.info(f"Successfully processed and removed: {filename}")
      
            ts_batch_paths.append((json_path, ts_path))
        except Exception as e:
            logger.warning(f"Skip {filename}: {e}")
