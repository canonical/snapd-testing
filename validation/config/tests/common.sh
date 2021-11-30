#!/bin/bash

# Jobs project
JOBS_PROJECT=${JOBS_PROJECT:-"snapd-testing"}
JOBS_URL=${JOBS_URL:-"https://github.com/snapcore/snapd-testing.git"}
JOBS_BRANCH=${JOBS_BRANCH:-"master"}

# Test projects
SNAPD_URL=${SNAPD_URL:-"https://github.com/snapcore/snapd.git"}
CCONF_URL=${CCONF_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

# Branch and Commit used as default in the test projects 
BRANCH=${BRANCH:-"master"}
COMMIT=${COMMIT:-"HEAD"}

# Spread config
SPREAD_URL=${SPREAD_URL:-"https://storage.googleapis.com/snapd-spread-tests/spread/spread-amd64.tar.gz"}
SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}

# Testflinger confir
TF_CLIENT=${TF_CLIENT:-"/snap/bin/testflinger-cli"}
TF_DATA=${TF_DATA:-"/var/snap/testflinger-cli/common"}
TF_JOB=${TF_JOB:-"tf_job.yaml"}

# User/pass to access the device under test
DEVICE_USER=${DEVICE_USER:-"ubuntu"}
DEVICE_PASS=${DEVICE_PASS:-""}
DEVICE_PORT=${DEVICE_PORT:-22}

# User/pass to access the vm used for i386/amd64 architectures + other vm configuration
TEST_USER=${TEST_USER:-"test"}
TEST_PASS=${TEST_PASS:-"ubuntu"}
BUILD_SNAPD=${BUILD_SNAPD:-false}

