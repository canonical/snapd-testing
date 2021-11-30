#!/bin/bash

ARCH=${ARCH:-"i386"}

PROJECT=${PROJECT:-"console-conf-tests"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

DEVICE_QUEUE=${DEVICE_QUEUE:-"maas-x86-node"}

CHANNEL=${CHANNEL:-"beta"}

BRANCH=${BRANCH:-"master"}

SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-32"}

TESTS_BACKEND=testflinger
TESTS_DEVICE=vm