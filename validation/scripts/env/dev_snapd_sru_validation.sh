#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"beta"}
export SETUP=${SETUP:-"sudo apt install -y snapd && sudo cp /etc/apt/sources.list sources.list.back && sudo echo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -c -s)-proposed restricted main multiverse universe" | sudo tee /etc/apt/sources.list -a && sudo apt update && sudo apt install -y --only-upgrade snapd && sudo mv sources.list.back /etc/apt/sources.list && sudo apt update"}
export SPREAD_TESTS=${SPREAD_TESTS:-""}
