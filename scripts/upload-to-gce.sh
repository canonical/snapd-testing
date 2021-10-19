#!/bin/bash

file=$1
subdir=${2:-'snaps'}

if [ -z "$file" ]; then
    echo "File cannot be empty, exiting..."
    exit 1
fi

if [ ! -f  "$file" ]; then
    echo "File does not exist, exiting..."
    exit 1
fi

file_name="$(basename $file)"
gsutil -o GSUtil:parallel_composite_upload_threshold=2000M cp "$file" "gs://snapd-spread-tests/$subdir/$file_name"
echo "public url: https://storage.googleapis.com/snapd-spread-tests/$subdir/$file_name"
