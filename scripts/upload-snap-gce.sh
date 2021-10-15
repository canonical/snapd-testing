#!/bin/bash

snap=$1

if [ -z "$snap" ]; then
    echo "Snap cannot be empty, exiting..."
    exit 1
fi

snap_name="$(basename $snap)"
gsutil -o GSUtil:parallel_composite_upload_threshold=2000M cp "$snap" "gs://snapd-spread-tests/snaps/$snap_name"
echo "public url: https://storage.googleapis.com/snapd-spread-tests/snaps/$snap_name"
