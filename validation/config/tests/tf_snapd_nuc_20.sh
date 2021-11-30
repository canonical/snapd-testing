#!/bin/bash

ARCH=${ARCH:-"amd64"}

PROJECT=${PROJECT:-"snapd"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-i-uc20-fde"}

CHANNEL=${CHANNEL:-"beta"}
CORE_CHANNEL=${CORE_CHANNEL:-"beta"}
SNAPD_CHANNEL=${SNAPD_CHANNEL:-"beta"}

BRANCH=${BRANCH:-"beta"}

SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-20-64:tests/smoke/"}

TESTS_BACKEND=testflinger
TESTS_DEVICE=device
