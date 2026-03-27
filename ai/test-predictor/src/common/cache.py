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
            'duration_ms': float(data.get('duration_ms', 0.0)),
            'attempt': int(data.get('attempt', 1)),
            'start': data.get('start', '')
        }

    def update(self, raw_data):
        """Updates the specific [system][name][verb][scenario] bucket."""
        item = self._normalize_entry(raw_data)
        s, n, v, sce = item['system'], item['name'], item['verb'], item['scenario']

        # Ensure the nested path exists
        if s not in self.cache: 
            self.cache[s] = {}
        if n not in self.cache[s]:
            self.cache[s][n] = {}
        if v not in self.cache[s][n]:
            self.cache[s][n][v] = {}
        if sce not in self.cache[s][n][v]:
            self.cache[s][n][v][sce] = []

        # Store only the key values the LSTM needs
        history = self.cache[s][n][v][sce]
        history.append(item)

        # Maintain sliding window
        if len(history) > self.history_size:
            self.cache[s][n][v][sce] = history[-self.history_size:]

    def get_context(self, system, name, verb, scenario=config.DEFAULT_SCENARIO):
        """Retrieves history for a specific test configuration."""
        try:
            return self.cache[system][name][verb][scenario]
        except KeyError:
            return []

    def prime_from_disk(self, processed_dir=config.PROCESSED_DIR):
        """Reconstructs the specific histories from all .ts files."""
        logger.info("Scanning .ts files to prime specific test histories...")
        ts_files = glob.glob(os.path.join(processed_dir, "*.ts"))
        
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

        master_df = pd.concat(all_chunks, ignore_index=True)
        if 'start' in master_df.columns:
            master_df['start'] = pd.to_datetime(master_df['start'])
            master_df = master_df.sort_values('start')

        # Populate the multi-level cache
        for _, row in master_df.iterrows():
            self.update(row.to_dict())
            
        logger.info("Cache primed successfully.")
