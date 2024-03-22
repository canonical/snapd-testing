#!/bin/bash

export SNAP_UT=core
export ARCH_UT=amd64
export BOARD_UT=pc-amd64
export VERSION_UT=16
export PROJECT=snapd
export BRANCH=beta
export CHANNEL=beta

export NESTED_SPREAD_TESTS="${SPREAD_TESTS:-"external:ubuntu-core-16-64:"}"
export SPREAD_TESTS="google-nested-dev:ubuntu-16.04-64:tests/nested/manual/run-spread:custom"
export SPREAD_ENV="NESTED_CUSTOM_IMAGE_URL=https://storage.googleapis.com/snapd-spread-tests/images/pc-amd64-16-stable-core_beta/pc.img.xz NESTED_SPREAD_TESTS=\"$NESTED_SPREAD_TESTS\""
export SPREAD_PARAMS="-artifacts=./artifacts"
export SPREAD_SKIP=