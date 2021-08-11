#!/bin/bash
set -x

echo "Loading environment variables"

ENV_FILE="$1"
if [ -f "$SCRIPTS_DIR/env/$ENV_FILE.sh" ]; then
    echo "Using environment file: $ENV_FILE"
    . "$SCRIPTS_DIR/env/$ENV_FILE.sh"
else
    echo "Environment file does not exist: $SCRIPTS_DIR/env/$ENV_FILE.sh"
    exit 1
fi	
