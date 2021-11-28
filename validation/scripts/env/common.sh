#!/bin/sh

export JOBS_PROJECT=${JOBS_PROJECT:-"snapd-testing"}
export JOBS_URL=${JOBS_URL:-"https://github.com/snapcore/snapd-testing.git"}
export JOBS_BRANCH=${JOBS_BRANCH:-"master"}
export SNAPD_URL=${SNAPD_URL:-"https://github.com/snapcore/snapd.git"}
export SPREAD_URL=${SPREAD_URL:-"https://storage.googleapis.com/snapd-spread-tests/spread/spread-amd64.tar.gz"}
export CCONF_URL=${CCONF_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}
export TF_CLIENT=${TF_CLIENT:-"/snap/bin/testflinger-cli"}
export TF_DATA=${TF_DATA:-"/var/snap/testflinger-cli/current"}
export GOPATH=$WORKSPACE/gopath

export BUILD_SNAPD=${BUILD_SNAPD:-false}

export DEVICE_USER=${DEVICE_USER:-"ubuntu"}
export DEVICE_PASS=${DEVICE_PASS:-""}
export DEVICE_PORT=${DEVICE_PORT:-22}
export TEST_USER=${TEST_USER:-"test"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}

export BRANCH=${BRANCH:-"master"}
export COMMIT=${COMMIT:-"HEAD"}
