#!/bin/bash

export SNAP_UT=core20
export ARCH_UT=amd64
export BOARD_UT=pc-amd64
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="google-nested-dev:ubuntu-20.04-64:tests/nested/manual/run-spread:custom"
export SPREAD_ENV="NESTED_CUSTOM_IMAGE_URL=https://storage.googleapis.com/snapd-spread-tests/images/pc-amd64-20-stable-core20_edge/pc.img.xz NESTED_SPREAD_TESTS=external:ubuntu-core-20-64:tests/smoke/"
export SPREAD_PARAMS="-artifacts=./artifacts"
export SPREAD_SKIP=