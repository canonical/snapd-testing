#!/bin/bash

export ARCH=${ARCH:-"amd64"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}
export DEVICE_DISTRO=${DEVICE_DISTRO:-"jammy"}

export CHANNEL=${CHANNEL:-"edge"}
export CORE_CHANNEL=${CORE_CHANNEL:-"edge"}
export SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

export BRANCH=${BRANCH:-"beta"}
export VERSION=${VERSION:-"uc22"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-22-64"}
export SKIP_TESTS=${SKIP_TESTS:-""}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm
