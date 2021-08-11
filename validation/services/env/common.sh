#!/bin/bash

WORKSPACE=${1:-$(pwd)}

export SPREAD_LOG=$WORKSPACE/spread.log
export SUMMARY_LOG=$WORKSPACE/summary.log
export ERRORS_LOG=$WORKSPACE/errors.log
export ATTACH_LOG=$WORKSPACE/attach.log
export SPREAD_URL=https://people.canonical.com/~sjcazzol/snappy/spreadSSHKeyFixed