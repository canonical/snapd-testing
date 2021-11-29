#!/bin/sh

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"console-conf-tests"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}

export CHANNEL=${CHANNEL:-"beta"}

export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-64"}

export TESTS_BACKEND=testflinger
export TESTS_DEVICE=vm