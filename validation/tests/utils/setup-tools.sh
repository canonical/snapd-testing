#!/bin/bash

PROJECT_PATH=$1
TOOLS_PATH=$2
HOST=$3
PORT=$4
USER=$5
PASS=$6

# Move all the tools to the PATH
cp -f "$PROJECT_PATH"/external/snapd-testing-tools/tools/* "$TOOLS_PATH"
cp -f "$PROJECT_PATH"/external/snapd-testing-tools/remote/* "$TOOLS_PATH"

# Configure the remote connection
remote.setup config --host "$HOST" --port "$PORT" --user "$USER" --pass "$PASS"
