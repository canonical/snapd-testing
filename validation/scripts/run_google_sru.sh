#!/bin/bash

echo "Running tests on google vm"

export WORKSPACE=${WORKSPACE:-$(pwd)}
export SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

if [ "$#" -ne 1 ]; then
    echo "Illegal number of parameters"
    exit 1
else
	. "$SCRIPTS_DIR/utils/load_env.sh" "$1"
fi

. "$SCRIPTS_DIR/google/$PROJECT/run_google_sru.sh" | tee run.log

if which pastebinit; then
	echo "Uploding execution log to paste.ubuntu.com"
	pastebinit run.log || true
else
	echo "Report not uploaded automatically, please install pastebinit for that"
fi

if grep -e "Successful tasks:" -e "Aborted tasks:" -e "Failed tasks:" run.log; then
    echo "Execution finished and spread results included in log"
    exit 0
else
    echo "Execution finished but not spread results included in log"
    exit 1
fi
