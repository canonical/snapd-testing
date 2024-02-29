#!/bin/bash

# Logs
export SPREAD_LOG=spread.log
export SUMMARY_LOG=summary.log
export ERRORS_LOG=errors.log
export ATTACH_LOG=attach.log

# Default 
export PROJECT=${PROJECT:-"snapd"}
export PROJECT_URL=${PROJECT_URL:-"https://github.com/snapcore/snapd.git"}
export BRANCH=${BRANCH:-"master"}