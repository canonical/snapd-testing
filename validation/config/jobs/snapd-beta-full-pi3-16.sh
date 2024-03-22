#!/bin/bash

export SNAP_UT=core
export ARCH_UT=armhf
export BOARD_UT=pi3
export VERSION_UT=16
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="${SPREAD_TESTS:-"testflinger:ubuntu-core-16-rpi3:"}"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP="tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/remodel,tests/core/remodel-base,tests/core/remodel-kernel,tests/core/remodel-gadget,tests/core/failover,tests/main/interfaces-network-manager,tests/main/store-state"