#!/bin/bash -x

echo "Running tests"

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

SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

case "$TESTS_BACKEND" in
    google)
        "$SCRIPTS_DIR/google/$PROJECT/run-google.sh"
        ;;
    testflinger)
        case "$TESTS_DEVICE" in
            device)
                "$SCRIPTS_DIR"/test_flinger/create-job-device.sh "$TF_JOB"
                ;;
            vm)
                "$SCRIPTS_DIR"/test_flinger/create-job-vm.sh "$TF_JOB"
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
