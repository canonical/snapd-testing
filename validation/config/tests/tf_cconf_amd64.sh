#!/bin/bash

export ARCH=${ARCH:-"amd64"}

export PROJECT=${PROJECT:-"console-conf-tests"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"bionic"}

export CHANNEL=${CHANNEL:-"beta"}
export CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"master"}
export VERSION=${VERSION:-"uc16"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-64"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm