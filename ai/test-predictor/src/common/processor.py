import os, json, re, glob, shutil
import pandas as pd
from common import config
from common.config import setup_logging
from common.transformer import clean_and_transform_data
from common.trainer import (
    get_metadata, save_metadata, preprocess_dataframe, 
    prepare_sequences, build_or_load_model
)

logger = setup_logging("tp-processor")

def extract_attempt(filename):
    match = re.search(r"attempt_(\d+)", filename)
    return int(match.group(1)) if match else 1

def process_and_train_batch(json_files, ts_dir, model_dir):
    """The heavy lifting: Convert -> Train -> Archive"""
    logger.info(f"Processing batch of {len(json_files)} files...")
    
    ts_batch_paths = []    
    # Conversion
    for json_path in json_files:
        filename = os.path.basename(json_path)
        ts_path = os.path.join(ts_dir, filename.replace(".json", ".ts"))
        
        try:
            attempt = extract_attempt(filename)
            with open(json_path, 'r') as f:
                df, _ = clean_and_transform_data(json.load(f), attempt)
            df.to_csv(ts_path, index=False)
            ts_batch_paths.append((json_path, ts_path))
        except Exception as e:
            logger.warning(f"Skip {filename}: {e}")

    if not ts_batch_paths: return

    # Training
    try:
        logger.info(f"Training model with {len(ts_batch_paths)} new TS files...")

        model_path = os.path.join(model_dir, config.MODEL_NAME)
        meta_path = os.path.join(model_dir, config.METADATA_NAME)
        
        df_list = [pd.read_csv(p[1]) for p in ts_batch_paths]
        combined = pd.concat(df_list, ignore_index=True)
        
        enc, scal = get_metadata(meta_path)
        proc_df = preprocess_dataframe(combined, enc, scal)
        save_metadata(meta_path, enc, scal)
        
        X, y = prepare_sequences(proc_df)
        model = build_or_load_model(model_path, (X.shape[1], X.shape[2]))
        model.fit(X, y, epochs=5, batch_size=8, verbose=0)
        model.save(model_path)

        # C. Archive Successes
        for j_p, t_p in ts_batch_paths:
            shutil.move(t_p, os.path.join(config.PROCESSED_DIR, os.path.basename(t_p)))
            os.remove(j_p)

        logger.info(f"Trained & Archived {len(ts_batch_paths)} files.")
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
