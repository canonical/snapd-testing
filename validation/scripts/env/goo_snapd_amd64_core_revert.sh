#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"beta"}
export SPREAD_TESTS=${SPREAD_TESTS:-"google-nested:ubuntu-16.04-64:tests/nested/core/core-revert"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export SPREAD_ENV=${SPREAD_ENV:-"SPREAD_CORE_CHANNEL=stable SPREAD_CORE_REFRESH_CHANNEL=beta"}
