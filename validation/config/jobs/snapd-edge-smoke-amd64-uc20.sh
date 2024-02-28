#!/bin/bash

export IMAGE_UT=cdimage
export SNAP_UT=snapd
export ARCH_UT=amd64
export BOARD_UT=pc-amd64
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="google-nested-dev:ubuntu-20.04-64:tests/nested/manual/run-spread:custom"
export SPREAD_ENV="NESTED_CUSTOM_IMAGE_URL=http://cdimage.ubuntu.com/ubuntu-core/20/dangerous-edge/pending/ubuntu-core-20-amd64.img.xz NESTED_SPREAD_TESTS=external:ubuntu-core-20-64:tests/smoke/"
export SPREAD_PARAMS="-artifacts=./artifacts"
export SPREAD_SKIP=