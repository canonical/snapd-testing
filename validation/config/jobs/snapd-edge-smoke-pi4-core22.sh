#!/bin/bash

export SNAP_UT=core22
export ARCH_UT=arm64
export BOARD_UT=pi4
export VERSION_UT=22
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="testflinger:ubuntu-core-22-arm64-rpi4:tests/smoke/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=