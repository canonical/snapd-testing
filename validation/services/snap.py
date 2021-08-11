#!/usr/bin/env python3

import subprocess


class Snap:

    SUPPORTED_CHANNELS = ['edge', 'beta', 'candidate', 'stable']

    def __init__(self, name):
        if not name:
            raise ValueError('snap name not defined')

        self.name = name
        self.info = self._get_info()

    def _get_info(self):
        line = 'snap info {}'.format(self.name)
        return subprocess.check_output(line, shell=True, universal_newlines=True).splitlines()

    def _get_channel_info(self, line, channel):
        parts = line.split()
        if parts[0][:-1] != channel:
           raise ValueError('channel {} not supported'.format(channel))

        # Case when there is not info for the channel
        if len(parts) == 2:
            return {}

        # Case when there are not 6 fields for the channel
        if len(parts) != 6:
            raise ValueError('values for channel not identified on line: {}'.format(line))

        version = parts[1]
        revision = parts[3]

        # Case when there revision has not format (N°)
        if revision[0] != '(' or revision[-1] != ')':
            raise ValueError('Wrong format for revision: {}'.format(revision))

        return {'version': version, 'revision': revision[1:-1]}


    def get_info_by_channel(self):
        info = {}
        for line in self.info:
            for channel in Snap.SUPPORTED_CHANNELS:
                channel_id = '{}:'.format(channel)
                if line.strip().startswith(channel_id):
                    info[channel] = self._get_channel_info(line.strip(), channel)

        return info
