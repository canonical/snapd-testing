import os
import glob
import pandas as pd

from flask import Blueprint, jsonify, request

from common import config
from common.utils import setup_logging

logger = setup_logging("stats-api")
stats_bp = Blueprint('stats', __name__)

def count_filtered_success(directory, filters):
    """
    Scans a directory for .ts files and counts success/fail 
    only for rows matching the provided filters.
    """
    pattern = os.path.join(directory, "*.ts")
    files = glob.glob(pattern)
    
    passes = 0
    fails = 0
    matching_files = 0

    for f in files:
        try:
            df = pd.read_csv(f)
            
            # Apply filters dynamically based on provided query params
            # We use .astype(str) to ensure comparison works regardless of type
            for col, val in filters.items():
                if col in df.columns and val is not None:
                    df = df[df[col].astype(str) == str(val)]
            
            if not df.empty:
                matching_files += 1
                p = int(df['success'].sum())
                f_count = len(df) - p
                passes += p
                fails += f_count
        except Exception as e:
            logger.error(f"Error filtering file {f}: {e}")
            
    return passes, fails, matching_files

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

def get_filtered_history(directory, filters, limit=None):
    """Returns historical rows matching the provided filters."""
    pattern = os.path.join(directory, "*.ts")
    files = glob.glob(pattern)

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)

            for col, val in filters.items():
                if col in df.columns and val is not None:
                    df = df[df[col].astype(str) == str(val)]

            if not df.empty:
                frames.append(df)
        except Exception as e:
            logger.error(f"Error processing history file {f}: {e}")

    if not frames:
        return []

    history = pd.concat(frames, ignore_index=True)

    if 'start' in history.columns:
        history['_start_sort'] = pd.to_datetime(history['start'], errors='coerce')
        history = history.sort_values(by='_start_sort', ascending=False)
        history = history.drop(columns=['_start_sort'])

    if limit is not None and limit > 0:
        history = history.head(limit)

    # Ensure JSON-safe null values
    history = history.where(pd.notnull(history), None)
    return history.to_dict(orient='records')

@stats_bp.route('/stats', methods=['GET'])
def get_filtered_stats():
    # Extract filters from the URL query string
    filters = {
        "name": request.args.get('name'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb')
    }

    # Validate that at least some filters were provided
    if not any(filters.values()):
        return jsonify({"error": "Provide at least one filter (name, system, etc.)"}), 400

    # Process both directories
    proc_p, proc_f, file_count = count_filtered_success(config.TS_DIR, filters)

    results = {
        "pass": proc_p,
        "fail": proc_f,
        "total": proc_p + proc_f
    }

    if filters.get("system") or filters.get("name"):
        results["matching_files"] = file_count

    return jsonify({
        "filters_applied": {k: v for k, v in filters.items() if v is not None},
        "results": results
    }), 200

@stats_bp.route('/stats/all-systems', methods=['GET'])
def get_all_systems_audit():
    filters = {
        "name": request.args.get('name'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb')
    }
    
    stats = get_all_systems_stats(config.TS_DIR, filters)
    return jsonify({"filters": filters, "systems": stats}), 200

@stats_bp.route('/stats/history', methods=['GET'])
def get_system_name_history():
    filters = {
        "name": request.args.get('name'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb')
    }

    if not filters["name"] or not filters["system"]:
        return jsonify({"error": "Both 'name' and 'system' are required"}), 400

    limit_raw = request.args.get('limit')
    limit = None
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
            if limit <= 0:
                return jsonify({"error": "'limit' must be a positive integer"}), 400
        except ValueError:
            return jsonify({"error": "'limit' must be an integer"}), 400

    history = get_filtered_history(config.TS_DIR, filters, limit=limit)

    return jsonify({
        "filters_applied": {k: v for k, v in filters.items() if v is not None},
        "count": len(history),
        "history": history
    }), 200