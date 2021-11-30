#!/bin/bash

WORKSPACE=${1:-$(pwd)}

SPREAD_LOG=$WORKSPACE/spread.log
SUMMARY_LOG=$WORKSPACE/summary.log
ERRORS_LOG=$WORKSPACE/errors.log
ATTACH_LOG=$WORKSPACE/attach.log
SPREAD_URL=https://people.canonical.com/~sjcazzol/snappy/spreadSSHKeyFixed