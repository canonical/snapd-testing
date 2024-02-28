#!/bin/bash

export SNAP_UT=snapd
export ARCH_UT=armhf
export BOARD_UT=pi3-18
export VERSION_UT=18
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export SPREAD_TESTS="testflinger:ubuntu-core-18-rpi3:tests/"
export SPREAD_ENV=
export SPREAD_PARAMS=
export SPREAD_SKIP="tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/remodel,tests/core/remodel-base,tests/core/remodel-kernel,tests/core/remodel-gadget,tests/core/failover,tests/main/interfaces-network-manager,tests/main/store-state"