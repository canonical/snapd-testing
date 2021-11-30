#!/bin/bash

ARCH=${ARCH:-"armhf"}

PROJECT=${PROJECT:-"console-conf-tests"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

DEVICE_QUEUE=${DEVICE_QUEUE:-"dragonboard"}

CHANNEL=${CHANNEL:-"beta"}

BRANCH=${BRANCH:-"master"}

SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-core-16-arm-64"}
SPREAD_ENV=${SPREAD_ENV:-"WIFI_READY=false"}

SKIP_REFRESH=${SKIP_REFRESH:-"true"}

TESTS_BACKEND=testflinger
TESTS_DEVICE=device