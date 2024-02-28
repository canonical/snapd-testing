#!/bin/bash

export IMAGE_UT=cdimage
export SNAP_UT=snapd
export ARCH_UT=arm64
export BOARD_UT=pi4
export VERSION_UT=20
export PROJECT=snapd
export BRANCH=master
export CHANNEL=edge

export SPREAD_TESTS="testflinger:ubuntu-core-20-rpi4:tests/smoke/"
export SPREAD_ENV="IMAGE_URL=http://cdimage.ubuntu.com/ubuntu-core/20/dangerous-edge/pending/ubuntu-core-20-arm64+raspi.img.xz"
export SPREAD_PARAMS=
export SPREAD_SKIP=