#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"armhf"}
export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"beta"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-18-arm-32"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export DEVICE_IP=${DEVICE_IP:-"127.0.0.1"}
export SETUP=${SETUP:-"sudo rm -rf /home/gopath"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core18/snapd-failover,tests/main/interfaces-broadcom-asic-control"}