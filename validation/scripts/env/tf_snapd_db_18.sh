#!/bin/sh

export ARCH=${ARCH:-"arm64"}
export PROJECT=${PROJECT:-"snapd"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"dragonboard"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-18-arm-64:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
