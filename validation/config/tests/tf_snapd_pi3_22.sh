#!/bin/sh

export VERSION=${VERSION:-"uc22"}
export ARCH=${ARCH:-"armhf"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-22-rpi3:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/persistent-journal-namespace,tests/main/store-state"}
