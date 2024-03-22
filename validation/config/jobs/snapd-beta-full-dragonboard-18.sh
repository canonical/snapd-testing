#!/bin/bash

export SNAP_UT=snapd
export ARCH_UT=arm64
export BOARD_UT=dragonboard-18
export VERSION_UT=18
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="${SPREAD_TESTS:-"testflinger:ubuntu-core-18-dragonboard:"}"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=