#!/bin/bash

export SNAP_UT=core
export ARCH_UT=armhf
export BOARD_UT=pi2
export VERSION_UT=16
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="testflinger:ubuntu-core-16-rpi2:tests/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP="tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/failover,tests/main/store-state"