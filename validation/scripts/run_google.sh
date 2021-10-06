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

. "$SCRIPTS_DIR/google/$PROJECT/run_google.sh"
