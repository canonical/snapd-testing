#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"armhf"}
export PROJECT=${PROJECT:-"console-conf-tests"}
export CHANNEL=${CHANNEL:-"beta"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-arm-32"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export WIFI_SSID=${WIFI_SSID:-""}
export WIFI_PASSWORD=${WIFI_PASSWORD:-""}
export DEVICE_IP=${DEVICE_IP:-"127.0.0.1"}
export SKIP_REFRESH=${SKIP_REFRESH:-"true"}

