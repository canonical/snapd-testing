import os
import glob
import pandas as pd

from flask import Blueprint, jsonify, request

from common import config
from common.utils import setup_logging

logger = setup_logging("stats-api")
audit_bp = Blueprint('stats', __name__)

def count_filtered_success(directory, filters):
    """
    Scans a directory for .ts files and counts success/fail 
    only for rows matching the provided filters.
    """
    pattern = os.path.join(directory, "*.ts")
    files = glob.glob(pattern)
    
    passes = 0
    fails = 0

    for f in files:
        try:
            df = pd.read_csv(f)
            
            # Apply filters dynamically based on provided query params
            # We use .astype(str) to ensure comparison works regardless of type
            for col, val in filters.items():
                if col in df.columns and val is not None:
                    df = df[df[col].astype(str) == str(val)]
            
            if not df.empty:
                p = int(df['success'].sum())
                f_count = len(df) - p
                passes += p
                fails += f_count
        except Exception as e:
            logger.error(f"Error filtering file {f}: {e}")
            
    return passes, fails

def get_all_systems_stats(directory, filters):
    """Aggregates stats for every unique system found in the directory."""
    pattern = os.path.join(directory, "*.ts")
    files = glob.glob(pattern)
    
    system_stats = {} # { "system_name": {"pass": 0, "fail": 0} }

    for f in files:
        try:
            df = pd.read_csv(f)
            # Apply all filters EXCEPT system
            for col, val in filters.items():
                if col != "system" and col in df.columns and val is not None:
                    df = df[df[col].astype(str) == str(val)]
            
            if df.empty: continue

            # Group by system within the remaining filtered data
            summary = df.groupby('system')['success'].agg(['sum', 'count'])
            for sys_name, row in summary.iterrows():
                passes = int(row['sum'])
                fails = int(row['count']) - passes
                
                if sys_name not in system_stats:
                    system_stats[sys_name] = {"pass": 0, "fail": 0}
                
                system_stats[sys_name]["pass"] += passes
                system_stats[sys_name]["fail"] += fails
        except Exception as e:
            logger.error(f"Error processing {f}: {e}")
            
    return system_stats

@audit_bp.route('/stats', methods=['GET'])
def get_filtered_stats():
    # Extract filters from the URL query string
    filters = {
        "name": request.args.get('name'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb'),
        "level": request.args.get('level')
    }

    # Validate that at least some filters were provided
    if not any(filters.values()):
        return jsonify({"error": "Provide at least one filter (name, system, etc.)"}), 400

    # Process both directories
    proc_p, proc_f = count_filtered_success(config.PROCESSED_DIR, filters)

    return jsonify({
        "filters_applied": {k: v for k, v in filters.items() if v is not None},
        "results": {
            "pass": proc_p,
            "fail": proc_f,
            "total": proc_p  + proc_f
        }
    }), 200

@audit_bp.route('/stats/all-systems', methods=['GET'])
def get_all_systems_audit():
    filters = {
        "name": request.args.get('name'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb'),
        "level": request.args.get('level')
    }
    
    stats = get_all_systems_stats(config.PROCESSED_DIR, filters)
    return jsonify({"filters": filters, "systems": stats}), 200