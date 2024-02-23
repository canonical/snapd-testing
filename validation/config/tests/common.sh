#!/bin/sh

# Jobs project
export JOBS_PROJECT=${JOBS_PROJECT:-"snapd-testing"}
export JOBS_URL=${JOBS_URL:-"https://github.com/snapcore/snapd-testing.git"}
export JOBS_BRANCH=${JOBS_BRANCH:-"master"}

# Test projects
export SNAPD_URL=${SNAPD_URL:-"https://github.com/sergiocazzolato/snapd.git"}
export CCONF_URL=${CCONF_URL:-"https://github.com/sergiocazzolato/console-conf-tests.git"}

# Default 
export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/sergiocazzolato/snapd.git"}
export BRANCH=${BRANCH:-"tests-support-testflinger"}
export COMMIT=${COMMIT:-"HEAD"}


