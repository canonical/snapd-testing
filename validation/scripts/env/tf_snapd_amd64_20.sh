#!/bin/sh

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"snapd"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"focal"}

export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"stable"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-64"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm
