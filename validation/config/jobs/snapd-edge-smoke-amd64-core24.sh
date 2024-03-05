#!/bin/bash

export SNAP_UT=core24
export ARCH_UT=amd64
export BOARD_UT=pc-amd64
export VERSION_UT=24
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="google-nested-dev:ubuntu-22.04-64:tests/nested/manual/run-spread:custom"
export SPREAD_ENV="NESTED_CUSTOM_IMAGE_URL=https://storage.googleapis.com/snapd-spread-tests/images/pc-amd64-24-edge-core24_edge/pc.img.xz NESTED_SPREAD_TESTS=external:ubuntu-core-24-64:tests/smoke/"
export SPREAD_PARAMS="-artifacts=./artifacts"
export SPREAD_SKIP=