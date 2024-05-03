#!/bin/bash

export SNAP_UT=core20
export ARCH_UT=armhf
export BOARD_UT=pi3
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="testflinger:ubuntu-core-20-arm-32-rpi3:tests/smoke/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=