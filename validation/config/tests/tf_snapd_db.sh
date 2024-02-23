#!/bin/sh

export VERSION=${VERSION:-"uc16"}
export ARCH=${ARCH:-"arm64"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-16-dragonboard:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/failover,tests/main/interfaces-cups,tests/main/store-state"}
