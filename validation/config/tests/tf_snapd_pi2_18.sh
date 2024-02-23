#!/bin/bash

export VERSION=${VERSION:-"uc18"}
export ARCH=${ARCH:-"armhf"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-18-rpi2:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/remodel,tests/core/remodel-base,tests/core/remodel-kernel,tests/core/remodel-gadget,tests/core/failover,tests/main/interfaces-network-manager,tests/main/store-state"}
