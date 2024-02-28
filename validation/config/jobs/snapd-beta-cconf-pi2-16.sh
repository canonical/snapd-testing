#!/bin/bash

export SNAP_UT=core
export ARCH_UT=armhf
export BOARD_UT=pi2-cconf
export VERSION_UT=16
export PROJECT=cconf
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="testflinger:ubuntu-core-16-pi2:"
export SPREAD_ENV="WIFI_READY=false"
export SPREAD_PARAMS=
export SPREAD_SKIP=