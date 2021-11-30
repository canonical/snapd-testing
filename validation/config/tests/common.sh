#!/bin/sh

# Jobs project
export JOBS_PROJECT=${JOBS_PROJECT:-"snapd-testing"}
export JOBS_URL=${JOBS_URL:-"https://github.com/snapcore/snapd-testing.git"}
export JOBS_BRANCH=${JOBS_BRANCH:-"master"}

# Test projects
export SNAPD_URL=${SNAPD_URL:-"https://github.com/snapcore/snapd.git"}
export CCONF_URL=${CCONF_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

# Branch and Commit used as default in the test projects 
export BRANCH=${BRANCH:-"master"}
export COMMIT=${COMMIT:-"HEAD"}

# Spread config
export SPREAD_URL=${SPREAD_URL:-"https://storage.googleapis.com/snapd-spread-tests/spread/spread-amd64.tar.gz"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}

# Testflinger confir
export TF_CLIENT=${TF_CLIENT:-"/snap/bin/testflinger-cli"}
export TF_DATA=${TF_DATA:-"/var/snap/testflinger-cli/common"}
export TF_JOB=${TF_JOB:-"tf_job.yaml"}

# User/pass to access the device under test
export DEVICE_USER=${DEVICE_USER:-"ubuntu"}
export DEVICE_PASS=${DEVICE_PASS:-""}
export DEVICE_PORT=${DEVICE_PORT:-22}

# User/pass to access the vm used for i386/amd64 architectures + other vm configuration
export TEST_USER=${TEST_USER:-"test"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}
export BUILD_SNAPD=${BUILD_SNAPD:-false}

