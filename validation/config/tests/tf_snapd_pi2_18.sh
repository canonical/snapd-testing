#!/bin/bash

export ARCH=${ARCH:-"armhf"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"cert-rpi2"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-18-arm-32:tests/"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/main/remodel,tests/main/remodel-base,tests/main/remodel-gadget,tests/main/remodel-kernel,tests/core/failover"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
