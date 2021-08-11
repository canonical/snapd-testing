#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"armhf"}
export PROJECT=${PROJECT:-"console-conf-tests"}
export CHANNEL=${CHANNEL:-"beta"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"rpi2"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-arm-32"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export SPREAD_ENV=${SPREAD_ENV:-"WIFI_READY=false"}
export SKIP_REFRESH=${SKIP_REFRESH:-"true"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}