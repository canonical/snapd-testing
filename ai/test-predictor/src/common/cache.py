import glob
import os
import pandas as pd
from common import config
from common.utils import setup_logging

logger = setup_logging("cache-manager")

class SystemStateCache:
    def __init__(self, history_size=49):
        # Structured as: self.cache[system][name][verb][scenario] = [list of result_dicts]
        self.cache = {}
        self.history_size = history_size

    def _normalize_entry(self, data):
        """Standardizes the raw dictionary and handles NaNs in names."""
        name = data.get('n') or data.get('name')
        level = data.get('l') or data.get('level')
        
        # Handle the NaN Name issue specifically
        if not name or str(name).lower() == 'nan':
            if level == 'project':
                name = 'project:setup'
            elif level == 'suite':
                name = 'suite:setup'
            else:
                name = 'unknown_step'
        
        return {
            'name': str(name),
            'verb': str(data.get('v') or data.get('verb', 'unknown')),
            'level': str(level or 'task'),
            'system': str(data.get('s') or data.get('system', 'unknown')),
            'scenario': str(data.get('scenario', 'generic')),
            'success': int(data.get('success', 1)),
            'attempt': int(data.get('attempt', 1)),
            'start': data.get('start', '')
        }

    def update(self, raw_data):
        """Updates the specific [system][name][verb][scenario][attempt] bucket."""
        item = self._normalize_entry(raw_data)
        s, n, v, sce, a = item['system'], item['name'], item['verb'], item['scenario'], item['attempt']

        # Ensure the nested path exists down to the attempt level
        self.cache.setdefault(s, {}).setdefault(n, {}).setdefault(v, {}).setdefault(sce, {}).setdefault(a, [])

        history = self.cache[s][n][v][sce][a]
        history.append(item)

        # Maintain sliding window for this specific attempt type
        if len(history) > self.history_size:
            self.cache[s][n][v][sce][a] = history[-self.history_size:]

    def get_context(self, system, name, verb, attempt=config.DEFAULT_ATTEMPT, scenario=config.DEFAULT_SCENARIO):
        """Retrieves history for a specific attempt, or falls back to general history."""
        # Try the specific attempt first (The "Apples to Apples" match)
        try:
            specific_history = self.cache[system][name][verb][scenario][int(attempt)]
            if specific_history:
                return specific_history
        except KeyError:
            pass

        # FALLBACK: If no history for Attempt X, find the most common history for this test
        try:
            # Flatten all attempt buckets for this specific test configuration
            all_attempts = self.cache[system][name][verb][scenario]
            # Grab history from the most frequent attempt bucket (usually Attempt 1)
            # or just the first available one to provide SOME context to the LSTM
            for a in sorted(all_attempts.keys()):
                if all_attempts[a]:
                    return all_attempts[a]
        except KeyError:
            return []
            
        return []

    def prime_from_disk(self, processed_dir=config.PROCESSED_DIR):
        """Reconstructs the specific histories from all .ts files."""
        logger.info("Scanning .ts files to prime specific test histories...")
        ts_files = glob.glob(os.path.join(processed_dir, "*.ts"))
        logger.info(f"Found {len(ts_files)} .ts files to process.")

        if not ts_files:
            return

        all_chunks = []
        for f in ts_files:
            try:
                all_chunks.append(pd.read_csv(f))
            except Exception as e:
                logger.error(f"Error reading {f}: {e}")

        if not all_chunks:
            return

        logger.info("Concatenating data and sorting by start time...")
        master_df = pd.concat(all_chunks, ignore_index=True)
        if 'start' in master_df.columns:
            master_df['start'] = pd.to_datetime(master_df['start'])
            master_df = master_df.sort_values('start')
        groups = master_df.groupby(['system', 'name', 'verb', 'scenario', 'attempt'])

        logger.info("Updating cache with historical data...")
        # Populate the multi-level cache
        for (sys, name, verb, sce, att), group in groups:
            # Normalize and store the last N items for this specific bucket
            # We use _normalize_entry to handle the 'NaN' name logic
            history = [self._normalize_entry(r) for r in group.tail(self.history_size).to_dict('records')]
            
            # Ensure the nested structure exists and set the history
            self.cache.setdefault(sys, {}).setdefault(name, {}).setdefault(verb, {}).setdefault(sce, {})[att] = history
            
        logger.info("Cache primed successfully.")
