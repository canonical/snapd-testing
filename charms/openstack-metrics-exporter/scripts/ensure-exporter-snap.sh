#!/usr/bin/env bash
set -euo pipefail

snap_url="${EXPORTER_SNAP_URL:-https://storage.googleapis.com/snapd-spread-tests/dependencies/golang-openstack-metrics-exporter_1.6.0_amd64.snap}"

if [[ -z "$snap_url" ]]; then
    echo "EXPORTER_SNAP_URL is required" >&2
    exit 1
fi

snap_filename="${snap_url##*/}"
snap_filename="${snap_filename%%\?*}"
if [[ -z "$snap_filename" ]]; then
    echo "Could not determine snap filename from URL: $snap_url" >&2
    exit 1
fi

snap_path="/tmp/$snap_filename"
wget -q -O "$snap_path" "$snap_url"

if snap install --dangerous "$snap_path"; then
    rm -f "$snap_path"
    exit 0
fi

if snap refresh --dangerous "$snap_path"; then
    rm -f "$snap_path"
    exit 0
fi

rm -f "$snap_path"
echo "failed to install snap from url: $snap_url" >&2
exit 1
