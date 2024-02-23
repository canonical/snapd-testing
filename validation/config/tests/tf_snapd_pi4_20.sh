#!/bin/sh

export ARCH=${ARCH:-"arm64"}
export VERSION=${VERSION:-"uc20"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-20-rpi4:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/main/interfaces-cups,tests/core/persistent-journal-namespace,tests/main/store-state"}
