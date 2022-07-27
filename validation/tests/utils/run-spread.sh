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
    spread_env="$(echo $SPREAD_ENV | tr ',' ' ')"
    echo "Using spread env: $spread_env"
    export "$spread_env"
fi
export SPREAD_EXTERNAL_ADDRESS=$DEVICE_IP:$DEVICE_PORT

# Determine the spread location
SPREAD="$(which spread)"
if [ -z "$SPREAD" ]; then
    SPREAD="$(pwd)/spread"
    if [ ! -x "$SPREAD" ]; then
        echo "Spread not found"
        exit 1
    fi
fi
echo "Using spread $SPREAD"

# Run spread
if [ ! -d "$PROJECT_PATH" ]; then
    echo "Project path does not exist: $PROJECT_PATH"
    ls -al
    exit 1
fi

echo "Moving to manual all the tests to skip: $SKIP_TESTS"
tests_skip="$(echo $SKIP_TESTS | tr ',' ' ')"
for test in $tests_skip; do
    if [ -f "$PROJECT_PATH/$test/task.yaml" ]; then
        cp "$PROJECT_PATH/$test/task.yaml" "$PROJECT_PATH/$test/task.yaml.back"
        echo "manual: true" >> "$PROJECT_PATH/$test/task.yaml"
    fi
done

spread_params="$(echo $SPREAD_PARAMS | tr ',' ' ')"
spread_tests="$(echo $SPREAD_TESTS | tr ',' ' ')"

echo "Running command: spread $spread_params $spread_tests"
( cd "$PROJECT_PATH" && "$SPREAD" $spread_params $spread_tests )

echo "Restoring skipped tests"
for test in $tests_skip; do
    if [ -f "$PROJECT_PATH/$test/task.yaml.back" ]; then
        mv "$PROJECT_PATH/$test/task.yaml.back" "$PROJECT_PATH/$test/task.yaml"
    fi
done
