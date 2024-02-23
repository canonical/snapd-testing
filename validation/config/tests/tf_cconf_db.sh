#!/bin/sh

export VERSION=${VERSION:-"uc16"}
export ARCH=${ARCH:-"armhf"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-16-dragonboard"}
export SPREAD_ENV=${SPREAD_ENV:-"WIFI_READY=false"}
