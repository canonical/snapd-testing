#!/bin/bash

echo "Running tests on a test flinger device"

export WORKSPACE=${WORKSPACE:-$(pwd)}
export SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if [ "$#" -ne 1 ]; then
    echo "Illegal number of parameters"
    exit 1
else
    . "$SCRIPTS_DIR/utils/load_env.sh" "$1"
fi

echo "Deleting test flinger data"
sudo rm -f $TF_DATA/*

. "$SCRIPTS_DIR/test_flinger/$PROJECT/job_device.sh"
. "$SCRIPTS_DIR/test_flinger/run_job.sh" | tee run.log

if grep -e "Successful tasks:" -e "Aborted tasks:" -e "Failed tasks:" run.log; then
    echo "Execution finished and spread results included in log"
    exit 0
else
    echo "Execution finished but not spread results included in log"
    exit 1
fi
