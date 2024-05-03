#!/bin/bash

export SNAP_UT=core20
export ARCH_UT=arm64
export BOARD_UT=pi4
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="testflinger:ubuntu-core-20-arm-64-rpi4:tests/smoke/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=