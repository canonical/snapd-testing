#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"arm64"}
export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"rpi4b4g"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-arm-64:tests/"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}
export SETUP=${SETUP:-""}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery"}