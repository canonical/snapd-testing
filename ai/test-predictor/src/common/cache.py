import glob
import os
import pandas as pd
from common import config
from common.utils import setup_logging

logger = setup_logging("cache-manager")

class SystemStateCache:
    def __init__(self, history_size=49):
        # Flattened structure: self.cache[system][name][verb] = [list of result_dicts]
        self.cache = {}
        self.history_size = history_size

    def _normalize_entry(self, data):
        """Standardizes the raw dictionary and handles NaNs in names."""
        # Force everything to string and strip to prevent 'fedora ' != 'fedora'
        name = str(data.get('name') or '').strip()
        level = str(data.get('level') or 'task').strip()
        
        # Handle the NaN Name issue from pandas or empty API strings
        if not name or name.lower() == 'nan':
            if level == 'project':
                name = 'project:setup'
            elif level == 'suite':
                name = 'suite:setup'
            else:
                name = 'unknown_step'
        
        return {
            'name': name,
            'verb': str(data.get('verb') or 'unknown').strip(),
            'level': level,
            'system': str(data.get('system') or 'unknown').strip(),
            'scenario': str(data.get('scenario') or 'generic').strip(),
            'success': int(data.get('success', 1)),
            'attempt': int(data.get('attempt', 1)),
            'start': str(data.get('start') or '')
        }

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

    def prime_from_disk(self, processed_dir=config.PROCESSED_DIR):
        """Reconstructs histories grouped by system/name/verb."""
        logger.info("Scanning .ts files to prime test histories...")
        ts_files = glob.glob(os.path.join(processed_dir, "*.ts"))
        
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
