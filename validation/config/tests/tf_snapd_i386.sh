#!/bin/bash

ARCH=${ARCH:-"i386"}

PROJECT=${PROJECT:-"snapd"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}
DEVICE_DISTRO=${DEVICE_DISTRO:-"xenial"}

CHANNEL=${CHANNEL:-"stable"}
CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

BRANCH=${BRANCH:-"beta"}

SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-32"}
SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided"}

TESTS_BACKEND=testflinger
TESTS_DEVICE=vm