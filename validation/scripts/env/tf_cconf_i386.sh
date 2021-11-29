#!/bin/sh

export ARCH=${ARCH:-"i386"}
export PROJECT=${PROJECT:-"console-conf-tests"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}

export CHANNEL=${CHANNEL:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-32"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm