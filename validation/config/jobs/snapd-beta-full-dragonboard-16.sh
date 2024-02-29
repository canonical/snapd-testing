#!/bin/bash

export SNAP_UT=core
export ARCH_UT=arm64
export BOARD_UT=dragonboard-refresh
export VERSION_UT=16
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="testflinger:ubuntu-core-16-dragonboard:tests/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=