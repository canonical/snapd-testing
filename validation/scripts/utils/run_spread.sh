#!/bin/bash
set -x

echo "Running spread"

if [ "$#" -ne 7 ]; then
    echo "Illegal number of parameters: $#"
    i=1
    for param in $*; do
        echo "param $i: $param"
        i=$(( i + 1 ))
    done
    exit 1
fi

export WORKSPACE=${WORKSPACE:-$(pwd)}

DEVICE_IP=$1
DEVICE_PORT=$2
PROJECT_PATH=$3
SPREAD_TESTS=$4
SPREAD_ENV=$5
SKIP_TESTS=$6
SPREAD_PARAMS=$7

if [ -z "$SPREAD_TESTS" ]; then
    echo "Spread tests not defined, skipping execution"
    exit
fi

# Export env variables
if [ ! -z "$SPREAD_ENV" ]; then
    echo "Using spread env: $SPREAD_ENV"
    export $SPREAD_ENV
fi
export SPREAD_EXTERNAL_ADDRESS=$DEVICE_IP:$DEVICE_PORT

if [[ $(which spread) ]]; then
    echo "Spread found"
else
    if [ -f "$WORKSPACE/spread/spread" ]; then
        export PATH=$PATH:$WORKSPACE/spread
    else
        echo "Spread not found"
    fi
fi

# Run spread
cd $PROJECT_PATH

echo "Moving to manual all the tests to skip: $SKIP_TESTS"
tests_skip="$(echo $SKIP_TESTS | tr ',' ' ')"
for test in $tests_skip; do
    if [ -f $test/task.yaml ]; then
        cp $test/task.yaml $test/task.yaml.back
        echo "manual: true" >> $test/task.yaml
    fi
done

spread_params="$(echo $SPREAD_PARAMS | tr ',' ' ')"
spread_tests="$(echo $SPREAD_TESTS | tr ',' ' ')"
echo "Running command: spread $spread_params $spread_tests"
spread $spread_params $spread_tests

echo "Restoring skipped tests"
for test in $tests_skip; do
    if [ -f $test/task.yaml.back ]; then
        mv $test/task.yaml.back $test/task.yaml
    fi
done
