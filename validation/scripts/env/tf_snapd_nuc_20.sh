#!/bin/sh

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"snapd"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-i-uc20-fde"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"core20"}

export CHANNEL=${CHANNEL:-"beta"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-64:tests/smoke/"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=device
