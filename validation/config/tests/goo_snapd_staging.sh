#!/bin/bash

ARCH=${ARCH:-"amd64"}

PROJECT=${PROJECT:-"snapd"}
PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}

CHANNEL=${CHANNEL:-"beta"}

SPREAD_TESTS=${SPREAD_TESTS:-"google:ubuntu-16.04-64:tests/"}
SPREAD_ENV=${SPREAD_ENV:-"SPREAD_REMOTE_STORE=staging SNAPPY_USE_STAGING_STORE=1"}
SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}

TESTS_BACKEND=google
TESTS_DEVICE=normal