#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"beta"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-i-uc20-fde"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"core20"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-64:tests/smoke/"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}
export SKIP_TESTS=${SKIP_TESTS:-""}
