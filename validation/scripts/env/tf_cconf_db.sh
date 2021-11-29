#!/bin/sh

export ARCH=${ARCH:-"armhf"}
export PROJECT=${PROJECT:-"console-conf-tests"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"dragonboard"}

export CHANNEL=${CHANNEL:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-arm-64"}
export SPREAD_ENV=${SPREAD_ENV:-"WIFI_READY=false"}

export SKIP_REFRESH=${SKIP_REFRESH:-"true"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device