import os
import glob
import re
from collections import Counter
from datetime import date
import pandas as pd

from flask import Blueprint, jsonify, request

from common import config
from common.processor import select_recent_pr_runs
from common.stats import (
    query_aborted_stats,
    query_filtered_outcomes,
    query_grouped_outcomes,
    snapshot_available,
)
from common.utils import setup_logging

logger = setup_logging("stats-api")
stats_bp = Blueprint('stats', __name__)
FAILURE_VERBS = ('preparing', 'executing', 'restoring')
TEST_ID_COLUMNS = (
    'runid', 'job_id', 'backend', 'system', 'scenario', 'attempt', 'name',
)
TIME_FILTERS = ('from', 'to')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _stats_snapshot_path():
    return os.path.join(config.MODEL_DIR, config.STATS_SNAPSHOT)


def _cached_stats(filters, group_columns=()):
    snapshot_path = _stats_snapshot_path()
    if not snapshot_available(snapshot_path):
        return None
    try:
        if group_columns:
            outcomes = query_grouped_outcomes(
                snapshot_path, filters, group_columns
            )
        else:
            outcomes = query_filtered_outcomes(snapshot_path, filters)
        return outcomes, query_aborted_stats(snapshot_path, filters)
    except Exception as error:
        logger.error(f"Failed to query stats snapshot: {error}", exc_info=True)
        return None


def _is_aborted(item):
    return str(item.get('aborted', '')).strip().lower() == 'true'


def _item_value(item, key, default=''):
    value = item.get(key)
    try:
        return default if value is None or pd.isna(value) else value
    except (TypeError, ValueError):
        return value


def _parse_date_bound(value):
    if not DATE_PATTERN.fullmatch(value):
        raise ValueError
    return date.fromisoformat(value)


def _time_filter_error(filters):
    try:
        start = _parse_date_bound(filters['from']) if filters.get('from') else None
        end = _parse_date_bound(filters['to']) if filters.get('to') else None
    except (TypeError, ValueError):
        return "'from' and 'to' must be valid dates in YYYY-MM-DD format"

    if start is not None and end is not None and start > end:
        return "'from' must not be later than 'to'"
    return None


def _filter_frame(df, filters, excluded=()):
    for column, value in filters.items():
        if column in TIME_FILTERS or column in excluded:
            continue
        if column in df.columns and value is not None:
            df = df[df[column].astype(str) == str(value)]

    if not filters.get('from') and not filters.get('to'):
        return df
    if 'start' not in df.columns:
        return df.iloc[0:0]

    start_dates = pd.to_datetime(df['start'], errors='coerce', utc=True).dt.date
    if filters.get('from'):
        df = df[start_dates >= _parse_date_bound(filters['from'])]
        start_dates = start_dates.loc[df.index]
    if filters.get('to'):
        df = df[start_dates <= _parse_date_bound(filters['to'])]
    return df


def _aborted_item_matches(item, filters):
    values = dict(item)

    name = str(_item_value(values, 'name'))
    variant = str(_item_value(values, 'variant'))
    values['name'] = f"{name}:{variant}" if variant else name

    if not all(
        column in TIME_FILTERS
        or value is None
        or str(values.get(column, '')) == str(value)
        for column, value in filters.items()
    ):
        return False

    if not filters.get('from') and not filters.get('to'):
        return True
    start = pd.to_datetime(_item_value(values, 'start', None), errors='coerce', utc=True)
    if pd.isna(start):
        return False
    start_date = start.date()
    if filters.get('from') and start_date < _parse_date_bound(filters['from']):
        return False
    if filters.get('to') and start_date > _parse_date_bound(filters['to']):
        return False
    return True


def get_aborted_stats(directory, filters):
    """Aggregate aborted rows from archived CSV files."""
    files = glob.glob(os.path.join(directory, "*.csv"))
    by_test = Counter()
    by_system = Counter()
    by_target = {}
    matching_files = 0

    for path in files:
        try:
            frame = pd.read_csv(path)
            frame = frame.where(pd.notnull(frame), None)
            items = frame.to_dict(orient='records')

            matched_file = False
            for item in items:
                if not _is_aborted(item) or not _aborted_item_matches(item, filters):
                    continue

                name = str(_item_value(item, 'name', 'unknown'))
                variant = str(_item_value(item, 'variant'))
                by_test[f"{name}:{variant}" if variant else name] += 1
                backend = str(_item_value(item, 'backend', 'unknown'))
                system = str(_item_value(item, 'system', 'unknown'))
                by_system[system] += 1
                by_target.setdefault(backend, Counter())[system] += 1
                matched_file = True

            if matched_file:
                matching_files += 1
        except Exception as e:
            logger.error(f"Error processing aborted-results file {path}: {e}")

    return {
        "aborted": sum(by_test.values()),
        "matching_files": matching_files,
        "by_test": dict(sorted(by_test.items())),
        "by_system": dict(sorted(by_system.items())),
        "by_target": {
            backend: dict(sorted(systems.items()))
            for backend, systems in sorted(by_target.items())
        },
    }


def _test_outcome_identities(df, failure_verb=None):
    """Return pass candidates and failures keyed by test execution identity."""
    successes = pd.to_numeric(df['success'], errors='coerce')
    verbs = df['verb'].astype(str)
    failure_verbs = (failure_verb,) if failure_verb else FAILURE_VERBS

    identity_rows = pd.DataFrame(index=df.index)
    for column in TEST_ID_COLUMNS:
        identity_rows[column] = df[column] if column in df.columns else ''
    identity_rows = identity_rows.where(pd.notnull(identity_rows), '').astype(str)

    def identities(mask):
        return set(identity_rows.loc[mask].itertuples(index=False, name=None))

    failed = identities(verbs.isin(failure_verbs) & (successes == 0))
    passed = identities((verbs == 'executing') & (successes == 1))
    return passed, failed


def _summarize_test_outcomes(df, failure_verb=None):
    """Classify each test execution once, with failure taking precedence."""
    passed, failed = _test_outcome_identities(df, failure_verb)
    return len(passed - failed), len(failed)


def count_filtered_outcomes(directory, filters):
    """Count test passes and phase failures matching the provided filters."""
    files = glob.glob(os.path.join(directory, "*.ts"))
    passed = set()
    failed = set()
    matching_files = 0
    failure_verb = filters.get('verb')

    for path in files:
        try:
            df = pd.read_csv(path)
            df = _filter_frame(df, filters, excluded=('verb',))

            if df.empty:
                continue

            file_passed, file_failed = _test_outcome_identities(df, failure_verb)
            if file_passed or file_failed:
                matching_files += 1
                passed.update(file_passed)
                failed.update(file_failed)
        except Exception as e:
            logger.error(f"Error filtering file {path}: {e}")

    return len(passed - failed), len(failed), matching_files

def get_all_systems_stats(directory, filters):
    """Aggregates stats for every unique system found in the directory."""
    pattern = os.path.join(directory, "*.ts")
    files = glob.glob(pattern)
    
    system_outcomes = {}

    for f in files:
        try:
            df = pd.read_csv(f)
            df = _filter_frame(df, filters, excluded=('system', 'verb'))
            
            if df.empty: continue

            for sys_name, system_df in df.groupby('system'):
                passed, failed = _test_outcome_identities(
                    system_df, filters.get('verb')
                )
                if not passed and not failed:
                    continue

                outcomes = system_outcomes.setdefault(
                    str(sys_name), {"passed": set(), "failed": set()}
                )
                outcomes["passed"].update(passed)
                outcomes["failed"].update(failed)
        except Exception as e:
            logger.error(f"Error processing {f}: {e}")

    return {
        system: {
            "pass": len(outcomes["passed"] - outcomes["failed"]),
            "fail": len(outcomes["failed"]),
        }
        for system, outcomes in system_outcomes.items()
    }


def get_all_targets_stats(directory, filters):
    """Aggregate pass/fail stats by backend and system in one scan."""
    files = glob.glob(os.path.join(directory, "*.ts"))
    target_outcomes = {}

    for path in files:
        try:
            df = pd.read_csv(path)
            df = _filter_frame(df, filters, excluded=('verb',))

            if df.empty:
                continue

            for (backend, system), target_df in df.groupby(['backend', 'system']):
                passed, failed = _test_outcome_identities(
                    target_df, filters.get('verb')
                )
                if not passed and not failed:
                    continue

                outcomes = target_outcomes.setdefault(
                    (str(backend), str(system)), {"passed": set(), "failed": set()}
                )
                outcomes["passed"].update(passed)
                outcomes["failed"].update(failed)
        except Exception as e:
            logger.error(f"Error processing target stats file {path}: {e}")

    return {
        target: {
            "pass": len(outcomes["passed"] - outcomes["failed"]),
            "fail": len(outcomes["failed"]),
        }
        for target, outcomes in target_outcomes.items()
    }

def get_filtered_history(directory, filters, limit=None):
    """Returns historical rows matching the provided filters."""
    pattern = os.path.join(directory, "*.ts")
    files = select_recent_pr_runs(glob.glob(pattern))

    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df = _filter_frame(df, filters)

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
        "backend": request.args.get('backend'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb'),
        "from": request.args.get('from'),
        "to": request.args.get('to'),
    }

    time_error = _time_filter_error(filters)
    if time_error:
        return jsonify({"error": time_error}), 400

    # Validate that at least some filters were provided
    if not any(filters.values()):
        return jsonify({"error": "Provide at least one filter (name, system, etc.)"}), 400

    cached = _cached_stats(filters)
    if cached is None:
        proc_p, proc_f, file_count = count_filtered_outcomes(
            config.TS_DIR, filters
        )
        aborted_stats = get_aborted_stats(config.ARCHIVE_DIR, filters)
    else:
        (proc_p, proc_f, file_count), aborted_stats = cached

    results = {
        "pass": proc_p,
        "fail": proc_f,
        "aborted": aborted_stats["aborted"],
        "total": proc_p + proc_f + aborted_stats["aborted"],
        "matching_files": file_count,
    }

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
        "verb": request.args.get('verb'),
        "from": request.args.get('from'),
        "to": request.args.get('to'),
    }

    time_error = _time_filter_error(filters)
    if time_error:
        return jsonify({"error": time_error}), 400
    
    cached = _cached_stats(filters, ("system",))
    if cached is None:
        stats = get_all_systems_stats(config.TS_DIR, filters)
        aborted_stats = get_aborted_stats(config.ARCHIVE_DIR, filters)
    else:
        grouped, aborted_stats = cached
        stats = {key[0]: value for key, value in grouped.items()}
    for system, aborted in aborted_stats["by_system"].items():
        stats.setdefault(system, {"pass": 0, "fail": 0})
        stats[system]["aborted"] = aborted
    for system_stats in stats.values():
        system_stats.setdefault("aborted", 0)

    return jsonify({"filters": filters, "systems": stats}), 200


@stats_bp.route('/stats/all-targets', methods=['GET'])
def get_all_targets_audit():
    filters = {
        "name": request.args.get('name'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb'),
        "from": request.args.get('from'),
        "to": request.args.get('to'),
    }

    time_error = _time_filter_error(filters)
    if time_error:
        return jsonify({"error": time_error}), 400

    cached = _cached_stats(filters, ("backend", "system"))
    if cached is None:
        stats = get_all_targets_stats(config.TS_DIR, filters)
        aborted_stats = get_aborted_stats(config.ARCHIVE_DIR, filters)
    else:
        stats, aborted_stats = cached
    for backend, systems in aborted_stats["by_target"].items():
        for system, aborted in systems.items():
            stats.setdefault((backend, system), {"pass": 0, "fail": 0})[
                "aborted"
            ] = aborted

    targets = []
    for (backend, system), target_stats in sorted(stats.items()):
        passed = target_stats["pass"]
        failed = target_stats["fail"]
        aborted = target_stats.get("aborted", 0)
        total = passed + failed + aborted
        if total:
            targets.append({
                "backend": backend,
                "system": system,
                "pass": passed,
                "fail": failed,
                "aborted": aborted,
                "total": total,
            })

    return jsonify({
        "filters_applied": {k: v for k, v in filters.items() if v is not None},
        "targets": targets,
    }), 200

@stats_bp.route('/stats/history', methods=['GET'])
def get_system_name_history():
    filters = {
        "name": request.args.get('name'),
        "system": request.args.get('system'),
        "attempt": request.args.get('attempt'),
        "scenario": request.args.get('scenario'),
        "verb": request.args.get('verb'),
        "from": request.args.get('from'),
        "to": request.args.get('to'),
    }


    time_error = _time_filter_error(filters)
    if time_error:
        return jsonify({"error": time_error}), 400

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