import glob
import os
import pandas as pd
import pickle
from common import config
from common.utils import setup_logging

logger = setup_logging("cache-manager")

class SystemStateCache:
    def __init__(self, history_size=49):
        # Flattened structure: self.cache[system][name][verb] = [list of result_dicts]
        self.cache = {}
        self.history_size = history_size
        self.snapshot_path = os.path.join(config.MODEL_DIR, config.CACHE_SNAPSHOT)

    def _normalize_entry(self, data):
        """Standardizes the raw dictionary and handles NaNs in names."""
        # Force everything to string and strip to prevent 'fedora ' != 'fedora'
        name = str(data.get('name') or '').strip()
        
        # Handle the NaN Name issue from pandas or empty API strings
        if not name or name.lower() == 'nan':
            name = 'unknown_step'
        
        return {
            'name': name,
            'verb': str(data.get('verb') or 'unknown'),
            'system': str(data.get('system') or 'unknown'),
            'scenario': str(data.get('scenario') or config.DEFAULT_SCENARIO),
            'success': int(data.get('success', 0)), # Default to 0 (Failure) or a neutral 0.5
            'attempt': int(data.get('attempt') or config.DEFAULT_ATTEMPT),
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
        total_systems = len(self.cache)
        total_unique_tests = 0
        total_verb_buckets = 0

        for _, names in self.cache.items():
            total_unique_tests += len(names)
            for _, verbs in names.items():
                total_verb_buckets += len(verbs)

        logger.info(
            f"Cache Stats: {total_systems} Systems, "
            f"{total_unique_tests} Unique Test Names, "
            f"{total_verb_buckets} Total Verb Buckets loaded."
        )

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

    def save_snapshot(self, backup_dir=None):
        """
        Saves the current in-memory cache to a binary file.
        If backup_dir is provided, saves to that directory as CACHE_SNAPSHOT.pkl.
        """
        # Determine the target path
        if backup_dir:
            target_path = os.path.join(backup_dir, config.CACHE_SNAPSHOT)
        else:
            target_path = self.snapshot_path

        try:
            # Ensure the parent directory exists
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            
            with open(target_path, 'wb') as f:
                pickle.dump(self.cache, f)
            
            logger.info(f"Cache snapshot saved to {target_path}")
        except Exception as e:
            logger.error(f"Failed to save snapshot to {target_path}: {e}")


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
        
        if restored_data is not None:
            self.cache = restored_data
            logger.info("SystemStateCache initialized from disk snapshot.")
        else:
            logger.info("No snapshot found. Starting first-time priming...")
            self._force_prime_and_save()
        
        self._log_stats()

    def update(self, raw_data):
        """Updates the [system][name][verb] bucket with a chronological list."""
        item = self._normalize_entry(raw_data)
        s, n, v = item['system'], item['name'], item['verb']

        # Ensure the 3-level path exists
        self.cache.setdefault(s, {}).setdefault(n, {}).setdefault(v, [])

        history = self.cache[s][n][v]
        history.append(item)

        # Maintain sliding window across all attempts/scenarios for this test
        if len(history) > self.history_size:
            self.cache[s][n][v] = history[-self.history_size:]

    def get_context(self, system, name, verb, attempt=None, scenario=None):
        """
        Retrieves history from the verb bucket and filters by attempt/scenario.
        Falls back to full verb history if filtering results in 0 context.
        """
        try:
            full_history = self.cache[system][name][verb]
            
            # Apply Filters
            filtered = full_history
            if scenario:
                filtered = [i for i in filtered if i['scenario'] == scenario]
            if attempt is not None:
                filtered = [i for i in filtered if i['attempt'] == int(attempt)]
            
            # Return filtered if exists, otherwise fallback to full history for context
            if filtered:
                return filtered
            
            logger.debug(f"No specific match for {name} (Atmt {attempt}). Falling back to general history.")
            return full_history
            
        except (KeyError, ValueError):
            return []

    def prime_from_disk(self, ts_dir=config.TS_DIR):
        """Reconstructs histories grouped by system/name/verb."""
        logger.info("Scanning .ts files to prime test histories...")
        ts_files = glob.glob(os.path.join(ts_dir, "*.ts"))
        logger.info(f"Found {len(ts_files)} .ts files to process for cache priming.")

        if not ts_files:
            return

        all_chunks = []
        for f in ts_files:
            try:
                # Force types on read to prevent pandas from guessing 'system' is a number
                df = pd.read_csv(f, dtype={'system': str, 'name': str, 'verb': str})
                all_chunks.append(df)
            except Exception as e:
                logger.error(f"Error reading {f}: {e}")

        if not all_chunks:
            return

        master_df = pd.concat(all_chunks, ignore_index=True)
        
        # IMPORTANT: Clean the dataframe before grouping
        # Fill actual NaNs with empty strings so _normalize_entry works consistently
        master_df = master_df.fillna('')

        if 'start' in master_df.columns:
            master_df['start'] = pd.to_datetime(master_df['start'])
            master_df = master_df.sort_values('start')

        # Now group based on cleaned, normalized keys
        logger.info("Populating flattened cache...")
        for _, row in master_df.iterrows():
            # Use the same normalization for disk data as for API data
            item = self._normalize_entry(row.to_dict())
            s, n, v = item['system'], item['name'], item['verb']
            
            self.cache.setdefault(s, {}).setdefault(n, {}).setdefault(v, [])
            history = self.cache[s][n][v]
            history.append(item)
            
            if len(history) > self.history_size:
                self.cache[s][n][v] = history[-self.history_size:]
            
        logger.info(f"Cache primed successfully.")
