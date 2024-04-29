#!/bin/bash

export SNAP_UT=core22
export ARCH_UT=armhf
export BOARD_UT=pi3
export VERSION_UT=22
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="testflinger:ubuntu-core-22-arm-rpi3:tests/smoke/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=