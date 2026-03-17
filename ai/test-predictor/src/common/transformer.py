import pandas as pd
import secrets

from config import setup_logging

logger = setup_logging("tp-transformer")

def clean_and_transform_data(raw_data, attempt):
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
    df = df.dropna(subset=['instance', 'start', 'end', 'verb', 'name'])
    df = df[df['verb'] != 'checking'].copy()
    
    # METADATA
    df['runid'] = run_id
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
    requested_order = [
        'runid', 'instance', 'start', 'duration_ms', 'attempt', 
        'verb', 'level', 'backend', 'system', 'name', 'success'
    ]
    
    final_cols = [c for c in requested_order if c in df.columns]

    logger.info(f"Transformed data for run_id={run_id} with {len(df)} items and columns: {final_cols}")
    return df[final_cols], run_id
