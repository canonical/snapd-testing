#!/bin/bash

echo "Running tests"

export WORKSPACE=${WORKSPACE:-$(pwd)}
export SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

ENV_FILE="$1"

if [ -z "$ENV_FILE" ]; then
    echo "Illegal number of parameters"
    exit 1	
fi

echo "Loading env: $ENV_FILE"
. "$SCRIPTS_DIR/utils/load_env.sh" "$ENV_FILE"

if [ -z "$TESTS_BACKEND" ]; then
    echo "Backend not defined in environment config"
    exit 1  
fi

if [ -z "$TESTS_DEVICE" ]; then
    echo "Test device not defined in environment config"
    exit 1  
fi

if [ -z "$PROJECT" ]; then
    echo "Project not defined in environment config"
    exit 1  
fi

case "$TESTS_BACKEND" in
    google)
        . "$SCRIPTS_DIR/google/$PROJECT/run-google.sh"
        ;;
    testflinger)
        case "$TESTS_DEVICE" in
            device)
                "$SCRIPTS_DIR"/test_flinger/"$PROJECT"/create-job-device.sh "$TF_JOB"
                ;;
            vm)
                "$SCRIPTS_DIR"/test_flinger/"$PROJECT"/create-job-vm.sh "$TF_JOB"
                ;;
            *)
                echo "$TESTS_DEVICE not supported for testflinger"
                exit 1  
                ;;
        esac
        "$SCRIPTS_DIR"/test_flinger/run-job.sh "$TF_JOB"
        rm -f "$TF_JOB"
        ;;
    *)
        echo "$TESTS_BACKEND not supported"
        exit 1
        ;;
esac
