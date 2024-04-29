#!/bin/bash

export SNAP_UT=snapd
export ARCH_UT=armhf
export BOARD_UT=pi3-20
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="${SPREAD_TESTS:-"testflinger:ubuntu-core-20-arm-rpi3:"}"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP="tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/persistent-journal-namespace,tests/main/store-state"