#!/bin/bash

export SNAP_UT=snapd
export ARCH_UT=armhf
export BOARD_UT=pi2-18
export VERSION_UT=18
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="${SPREAD_TESTS:-"testflinger:ubuntu-core-18-arm-rpi2:"}"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP="tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/failover,tests/main/store-state"