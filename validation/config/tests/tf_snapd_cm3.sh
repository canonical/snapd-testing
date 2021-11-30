#!/bin/bash

ARCH=${ARCH:-"armhf"}

PROJECT=${PROJECT:-"snapd"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

DEVICE_QUEUE=${DEVICE_QUEUE:-"cm3"}

CHANNEL=${CHANNEL:-"stable"}
CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

BRANCH=${BRANCH:-"beta"}

SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-arm-32:tests/"}

TESTS_BACKEND=testflinger
TESTS_DEVICE=device