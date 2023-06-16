#!/bin/sh

export ARCH=${ARCH:-"arm64"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"rpi4b8g"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"stable"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}
export VERSION=${VERSION:-"uc20"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-arm-64:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/main/interfaces-cups,tests/core/persistent-journal-namespace,tests/main/store-state"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
