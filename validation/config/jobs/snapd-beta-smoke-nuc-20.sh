#!/bin/bash

export SNAP_UT=snapd
export ARCH_UT=amd64
export BOARD_UT=pc-amd64-nuc-20
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="${SPREAD_TESTS:-"testflinger:ubuntu-core-20-64-nuc:tests/smoke/"}"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP=