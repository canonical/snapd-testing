#!/bin/bash

export ARCH=${ARCH:-"amd64"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-i-uc20-fde"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"dawson-i-uc20-fde"}

export CHANNEL=${CHANNEL:-"beta"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-64:tests/smoke/"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
