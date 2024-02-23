#!/bin/bash

export VERSION=${VERSION:-"uc20"}
export ARCH=${ARCH:-"amd64"}

export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

#export DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-i-uc20-fde"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"dawson-003"}


export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-20-nuc:tests/smoke/"}
