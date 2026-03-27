import glob
import os

import pandas as pd

from common import config
from common.utils import setup_logging

logger = setup_logging("cache-manager")

class SystemStateCache:
    def __init__(self, history_size=49):
        self.cache = {}  # { 'system_name': [[f1, f2...], [f1, f2...]] }
        self.history_size = history_size

    def _normalize_entry(self, data):
        """Standardizes the raw dictionary to match training expectations."""
        # Map shortened keys (n, v, l, s) to full names if necessary
        clean = {
            'name': data.get('n') or data.get('name'),
            'verb': data.get('v') or data.get('verb'),
            'level': data.get('l') or data.get('level'),
            'system': data.get('s') or data.get('system'),
            'success': data.get('success', 1),
            'duration_ms': data.get('duration_ms', 0),
            'attempt': data.get('attempt', 1),
            'scenario': data.get('scenario', 'generic')
        }

        # Fix the NaN Name issue based on the Level
        if not clean['name'] or str(clean['name']) == 'nan':
            if clean['level'] == 'project':
                clean['name'] = 'global:setup'
            elif clean['level'] == 'suite':
                clean['name'] = 'suite:setup'
            else:
                clean['name'] = 'unknown_step'
        
        return clean

    def get_context(self, system):
        """Returns the list of previous feature vectors for a system."""
        return self.cache.get(system, [])

    def update(self, system, raw_data):
        normalized = self._normalize_entry(raw_data)
        
        if system not in self.cache:
            self.cache[system] = []
            
        self.cache[system].append(normalized)
        
        # Keep the sliding window of 49 previous steps
        if len(self.cache[system]) > self.history_size:
            self.cache[system] = self.cache[system][-self.history_size:]

    def prime_from_disk(self, processed_dir):
        """Merges all available execution files per system to reconstruct history."""
        logger.info("Scanning all .ts files to reconstruct system histories...")
        
        ts_files = glob.glob(os.path.join(processed_dir, "*.ts"))
        if not ts_files:
            logger.warning("No .ts files found for priming.")
            return

        # 1. Collect ALL rows from ALL files into a single master dataframe
        # (If memory is an issue, we can limit this to the most recent files)
        all_chunks = []
        for f in ts_files:
            try:
                df = pd.read_csv(f)
                if not df.empty:
                    all_chunks.append(df)
            except Exception as e:
                logger.error(f"Failed to read {f}: {e}")

        if not all_chunks:
            return

        # Merge everything into one massive historical table
        master_df = pd.concat(all_chunks, ignore_index=True)
        
        # Ensure global chronological order
        if 'start' in master_df.columns:
            master_df['start'] = pd.to_datetime(master_df['start'])
            master_df = master_df.sort_values('start')
        
        # Group by system and take the tail for each one
        logger.info("Slicing history per system...")
        for system_name, group in master_df.groupby('system'):
            # Ensure system_name is a clean string
            sys_key = str(system_name)
            # Take the last N records and store as raw dicts
            self.cache[sys_key] = group.tail(self.history_size).to_dict('records')
            
        logger.info(f"Cache primed for {len(self.cache)} unique systems.")



