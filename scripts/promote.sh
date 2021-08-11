#!/bin/sh
set -ex

SNAP=${1:-core}
ORIGIN_CHANNEL=${2:-beta}
TARGET_CHANNEL=${3:-candidate}

for rev in $(snapcraft list-revisions "${SNAP}" | grep "${ORIGIN_CHANNEL}\*" | cut -d ' ' -f 1); do
    snapcraft release "${SNAP}" "$rev" "${TARGET_CHANNEL}"
done
