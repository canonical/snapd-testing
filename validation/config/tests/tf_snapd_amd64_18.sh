#!/bin/sh

export ARCH=${ARCH:-"amd64"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"bionic"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"stable"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-18-64"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/remodel,tests/core/remodel-base,tests/core/remodel-kernel,tests/core/remodel-gadget,tests/core/failover"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm