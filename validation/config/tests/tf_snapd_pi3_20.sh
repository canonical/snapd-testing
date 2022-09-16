#!/bin/sh

export ARCH=${ARCH:-"armhf"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"rpi3b"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"stable"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}
export VERSION=${VERSION:-"uc20"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-arm-32:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/persistent-journal-namespace"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
