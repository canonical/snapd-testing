import glob
import os
import time
import threading
import tempfile
import pandas as pd
import pickle
from common import config
from common.processor import extract_github_ids, extract_pr
from common.utils import setup_logging

logger = setup_logging("cache-manager")

class SystemStateCache:
    def __init__(self, history_size=config.CACHE_HISTORY_SIZE):
        # Flattened structure: self.cache[system][name][verb] = [list of result_dicts]
        self.cache = {}
        self.history_size = history_size
        self.snapshot_path = os.path.join(config.MODEL_DIR, config.CACHE_SNAPSHOT)

    def _normalize_entry(self, data):
        """Standardizes the raw dictionary and handles NaNs in names."""
        # Force everything to string and strip to prevent 'ubuntu ' != 'ubuntu'
        name = str(data.get('name') or '').strip()
        
        # Handle the NaN Name issue from pandas or empty API strings
        if not name or name.lower() == 'nan':
            name = 'unknown_name'

        # Helper to safely convert strings/NaNs to integers
        def safe_int(val, default):
            try:
                # If val is '' or None, use default. 
                # float() handles cases like "1.0" which int() would reject.
                if val == '' or val is None:
                    return default
                return int(float(val))
            except (ValueError, TypeError):
                return default

        return {
            'name': name,
            'verb': str(data.get('verb') or 'unknown'),
            'backend': str(data.get('backend') or 'unknown'),
            'system': str(data.get('system') or 'unknown'),
            'scenario': str(data.get('scenario') or config.DEFAULT_SCENARIO),
            'level': str(data.get('level') or 'unknown'),
            'job_id': safe_int(data.get('job_id'), None),
            'run_id': safe_int(data.get('run_id'), None),
            'pr': safe_int(data.get('pr'), None),
            'success': safe_int(data.get('success'), 0),
            'attempt': safe_int(data.get('attempt'), config.DEFAULT_ATTEMPT),
            'start': str(data.get('start') or '')
        }

    def _restore_from_snapshot(self):
        """Internal helper to load the pickle file."""
        if not os.path.exists(self.snapshot_path):
            return None
        try:
            with open(self.snapshot_path, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.error(f"Failed to load snapshot: {e}")
            return None

    def _has_missing_provenance(self, cache):
        for names in cache.values():
            for verbs in names.values():
                for history in verbs.values():
                    if any(
                        item.get('job_id') is None
                        or item.get('run_id') is None
                        or 'pr' not in item
                        or 'level' not in item
                        or item.get('name') == 'unknown_step'
                        for item in history
                    ):
                        return True
        return False

    def _force_prime_and_save(self):
        """Internal helper to consolidate the 'Prime -> Save' workflow."""
        try:
            self.prime_from_disk()
            self.save_snapshot()
            logger.info("Cache successfully rebuilt and snapshot updated.")
        except Exception as e:
            logger.error(f"Failed during force reinitialization: {e}")

    def _log_stats(self):
        """Calculates and logs the density of the current cache."""
        stats = self.stats()
        logger.info(
            "Cache Stats: %d systems, %d tests, %d verb buckets, %d entries, "
            "%d entries missing GitHub provenance",
            stats['systems'],
            stats['tests'],
            stats['verb_buckets'],
            stats['entries'],
            stats['missing_provenance'],
        )

    def stats(self):
        """Return cache density and GitHub provenance counts."""
        result = {
            'systems': len(self.cache),
            'tests': 0,
            'verb_buckets': 0,
            'entries': 0,
            'missing_provenance': 0,
            'provenance_samples': [],
        }

        for names in self.cache.values():
            result['tests'] += len(names)
            for verbs in names.values():
                result['verb_buckets'] += len(verbs)
                for history in verbs.values():
                    result['entries'] += len(history)
                    for item in history:
                        job_id = item.get('job_id')
                        run_id = item.get('run_id')
                        if job_id is None or run_id is None:
                            result['missing_provenance'] += 1
                        elif len(result['provenance_samples']) < 3:
                            result['provenance_samples'].append({
                                'job_id': job_id,
                                'run_id': run_id,
                            })

        return result

    def restore_backup(self, backup_dir):
        """Restores the cache snapshot from a specified backup directory."""
        backup_path = os.path.join(backup_dir, config.CACHE_SNAPSHOT)
        if not os.path.exists(backup_path):
            logger.error(f"Backup snapshot not found at {backup_path}")
            return False
        try:
            with open(backup_path, 'rb') as f:
                self.cache = pickle.load(f)
            logger.info(f"Cache successfully restored from backup: {backup_path}")

            # Persist this restored state as the new local default snapshot
            self.save_snapshot()
            logger.info("Local snapshot updated with restored backup data.")
            return True
        except Exception as e:
            logger.error(f"Failed to restore cache from backup: {e}")
            return False

    def save_snapshot(self, output_dir=None):
        """
        Saves the current in-memory cache to a binary file.
        If output_dir is provided, saves to that directory as CACHE_SNAPSHOT.pkl.
        """
        # Determine the target path
        if output_dir:
            target_path = os.path.join(output_dir, config.CACHE_SNAPSHOT)
        else:
            target_path = self.snapshot_path

        temporary_path = None
        try:
            # Ensure the parent directory exists
            parent = os.path.dirname(target_path)
            os.makedirs(parent, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix="cache-snapshot-", suffix=".pkl", dir=parent
            )
            with os.fdopen(descriptor, 'wb') as f:
                pickle.dump(self.cache, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temporary_path, target_path)
            temporary_path = None

            logger.info(f"Cache snapshot saved to {target_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save snapshot to {target_path}: {e}")
            return False
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)


    def reinitialize(self):
        """
        Forces a full scan of .ts files, rebuilding the cache from 
        scratch and overwriting the existing snapshot.
        """
        logger.info("Manual reinitialization triggered. Clearing current cache...")
        self.cache = {}
        self._force_prime_and_save()

    def initialize(self):
        """Standard entry point: Restore if possible, otherwise prime."""
        restored_data = self._restore_from_snapshot()
        
        if restored_data is not None and not self._has_missing_provenance(restored_data):
            self.cache = restored_data
            logger.info("SystemStateCache initialized from disk snapshot.")
        else:
            if restored_data is None:
                logger.info("No snapshot found. Starting first-time priming...")
            else:
                logger.info("Snapshot lacks required cache fields. Rebuilding from .ts files...")
            self.cache = {}
            del restored_data
            self._force_prime_and_save()
        
        self._log_stats()

    def _limit_pr_runs(self, history):
        if config.PR_RUNS_LIMIT <= 0:
            return history

        remaining_by_pr = {}
        limited = []
        for item in reversed(history):
            pr = item.get('pr')
            if pr is None:
                limited.append(item)
                continue
            used = remaining_by_pr.get(pr, 0)
            if used < config.PR_RUNS_LIMIT:
                limited.append(item)
                remaining_by_pr[pr] = used + 1
        return list(reversed(limited))

    def _compact_history(self, history):
        contexts = {}
        for item in history:
            key = tuple(
                item.get(column)
                for column in ('backend', 'scenario', 'attempt', 'level')
            )
            contexts.setdefault(key, []).append(item)

        compacted = []
        for context_history in contexts.values():
            compacted.extend(context_history[-self.history_size:])
        compacted.sort(key=lambda item: item.get('start', ''))
        return compacted

    def update(self, raw_data, compact=True):
        """Updates the [system][name][verb] bucket with a chronological list."""
        item = self._normalize_entry(raw_data)
        s, n, v = item['system'], item['name'], item['verb']

        # Ensure the 3-level path exists
        self.cache.setdefault(s, {}).setdefault(n, {}).setdefault(v, [])

        history = self.cache[s][n][v]
        history.append(item)
        if compact:
            self.cache[s][n][v] = self._compact_history(history)
        return s, n, v

    def get_context(
        self, system, name, verb, attempt=None, scenario=None, backend=None,
        max_items=None, level=None,
    ):
        """
        Retrieves filtered history, capped by default to CACHE_HISTORY_SIZE.
        """
        try:
            # Access the base bucket
            full_history = self.cache[system][name][verb]
            
            # Apply Filters
            filtered = full_history
            if backend:
                filtered = [i for i in filtered if i.get('backend') == backend]
            if scenario:
                filtered = [i for i in filtered if i.get('scenario') == scenario]
            if attempt is not None:
                filtered = [i for i in filtered if i.get('attempt') == int(attempt)]
            if level:
                filtered = [i for i in filtered if i.get('level') == level]

            filtered = self._limit_pr_runs(filtered)
            
            max_history = self.history_size if max_items is None else max(0, max_items)
            
            return filtered[-max_history:]
            
        except (KeyError, ValueError, TypeError):
            # Returns empty list if system/test/verb doesn't exist in cache yet
            return []

    def _select_recent_context_runs(self, ts_files):
        """Select recent PR files independently for each execution context."""
        if config.PR_RUNS_LIMIT <= 0:
            return ts_files

        selected = set()
        files_by_context = {}
        for path in ts_files:
            try:
                _job_id, run_id = extract_github_ids(os.path.basename(path))
                pr = extract_pr(os.path.basename(path))
                if pr is None:
                    selected.add(path)
                    continue

                frame = pd.read_csv(
                    path,
                    nrows=1,
                    usecols=lambda column: column in {
                        'backend', 'system', 'scenario', 'attempt',
                    },
                )
                row = frame.iloc[0].to_dict() if not frame.empty else {}
                context = (
                    pr,
                    str(row.get('backend', '')),
                    str(row.get('system', '')),
                    str(row.get('scenario', config.DEFAULT_SCENARIO)),
                    str(row.get('attempt', config.DEFAULT_ATTEMPT)),
                )
                files_by_context.setdefault(context, []).append((run_id, path))
            except Exception as e:
                logger.warning(f"Could not classify cache input {path}: {e}")
                selected.add(path)

        for entries in files_by_context.values():
            entries.sort(key=lambda entry: (-entry[0], entry[1]))
            selected.update(path for _run_id, path in entries[:config.PR_RUNS_LIMIT])
        return [path for path in ts_files if path in selected]


    def prime_from_disk(self, ts_dir=config.TS_DIR):
        """Reconstructs histories grouped by system/name/verb."""
        logger.info("Scanning .ts files to prime test histories...")
        all_ts_files = glob.glob(os.path.join(ts_dir, "*.ts"))
        ts_files = self._select_recent_context_runs(all_ts_files)
        logger.info(
            "Selected %d of %d .ts files for cache priming.",
            len(ts_files),
            len(all_ts_files),
        )

        if not ts_files:
            return

        total_files = len(ts_files)
        for file_index, f in enumerate(ts_files, start=1):
            try:
                # Force types on read to prevent pandas from guessing 'system' is a number
                df = pd.read_csv(f, dtype={'system': str, 'name': str, 'verb': str})
                try:
                    job_id, run_id = extract_github_ids(os.path.basename(f))
                    pr = extract_pr(os.path.basename(f))
                    for column, value in (('job_id', job_id), ('run_id', run_id), ('pr', pr)):
                        if column not in df.columns:
                            df[column] = value
                        else:
                            numeric = pd.to_numeric(df[column], errors='coerce')
                            df[column] = numeric if value is None else numeric.fillna(value)
                except ValueError:
                    logger.warning(f"Could not recover GitHub IDs from {f}")
                df = df.fillna('')
                touched_histories = set()
                for item in df.to_dict(orient='records'):
                    touched_histories.add(self.update(item, compact=False))
                for system, name, verb in touched_histories:
                    history = self.cache[system][name][verb]
                    history.sort(key=lambda item: item.get('start', ''))
                    self.cache[system][name][verb] = self._compact_history(history)
            except Exception as e:
                logger.error(f"Error reading {f}: {e}")

            if file_index % 500 == 0 or file_index == total_files:
                logger.info(
                    "Cache priming progress: %d/%d files.",
                    file_index,
                    total_files,
                )

        logger.info("Cache primed successfully.")


class DependencyMatrixCache:
    """
    In-memory cache for dependency analysis results (matrices, graphs, rankings).
    Automatically expires entries after DEPENDENCY_CACHE_TTL_HOURS.
    Thread-safe with locks.
    Persists to disk (MODEL_DIR/DEPENDENCY_CACHE_SNAPSHOT).
    """

    def __init__(self):
        self._cache = {}  # {(system, scenario): {"result": {...}, "timestamp": ...}}
        self._lock = threading.Lock()
        self._snapshot_path = os.path.join(config.MODEL_DIR, config.DEPENDENCY_CACHE_SNAPSHOT)

    def _make_key(self, system, scenario):
        """Generate cache key for a system/scenario pair."""
        return (system, scenario)

    def _is_stale(self, entry):
        """Check if a cache entry is older than DEPENDENCY_CACHE_TTL_HOURS."""
        if entry is None:
            return True
        age_seconds = time.time() - entry["timestamp"]
        max_age_seconds = config.DEPENDENCY_CACHE_TTL_HOURS * 3600
        return age_seconds > max_age_seconds

    def get(self, system, scenario):
        """Retrieve cached result if fresh, otherwise None."""
        with self._lock:
            key = self._make_key(system, scenario)
            entry = self._cache.get(key)
            if entry and not self._is_stale(entry):
                age_hours = (time.time() - entry["timestamp"]) / 3600
                logger.info(
                    "Using cached analysis for system=%s scenario=%s (age: %.1f hours)",
                    system,
                    scenario,
                    age_hours,
                )
                return entry["result"]
        return None

    def set(self, system, scenario, result):
        """Store analysis result in cache and persist to disk."""
        with self._lock:
            key = self._make_key(system, scenario)
            self._cache[key] = {
                "result": result,
                "timestamp": time.time(),
            }
        logger.info(
            "Cached analysis for system=%s scenario=%s",
            system,
            scenario,
        )
        self.save()

    def clear(self):
        """Clear entire cache."""
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
        logger.info("Cleared %d cached analyses", n)

    def load(self):
        """Load cache from disk snapshot if it exists."""
        if not os.path.exists(self._snapshot_path):
            logger.info(
                "Dependency cache snapshot not found at %s. Starting fresh.",
                self._snapshot_path,
            )
            return
        try:
            with self._lock:
                with open(self._snapshot_path, 'rb') as f:
                    self._cache = pickle.load(f)
            logger.info(
                "Dependency cache loaded from disk: %d entries",
                len(self._cache),
            )
        except Exception as e:
            logger.error(
                "Failed to load dependency cache from %s: %s",
                self._snapshot_path,
                e,
            )

    def save(self):
        """Save cache to disk snapshot."""
        try:
            os.makedirs(os.path.dirname(self._snapshot_path), exist_ok=True)
            with self._lock:
                with open(self._snapshot_path, 'wb') as f:
                    pickle.dump(self._cache, f)
            logger.info(
                "Dependency cache saved to disk: %d entries",
                len(self._cache),
            )
        except Exception as e:
            logger.error(
                "Failed to save dependency cache to %s: %s",
                self._snapshot_path,
                e,
            )

    def stats(self):
        """Return cache statistics."""
        with self._lock:
            stats = {
                "cached_entries": len(self._cache),
                "entries": [],
            }
            for (system, scenario), entry in self._cache.items():
                age_hours = (time.time() - entry["timestamp"]) / 3600
                stats["entries"].append({
                    "system": system,
                    "scenario": scenario,
                    "age_hours": round(age_hours, 2),
                    "stale": self._is_stale(entry),
                })
        return stats
