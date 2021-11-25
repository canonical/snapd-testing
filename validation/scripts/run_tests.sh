#!/bin/bash

echo "Running tests"

export WORKSPACE=${WORKSPACE:-$(pwd)}
export SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

ENV_FILE="$1"

if [ -z "$ENV_FILE" ]; then
    echo "Illegal number of parameters"
    exit 1	
fi

. "$SCRIPTS_DIR/utils/load_env.sh" "$1"

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
        case "$TESTS_DEVICE" in
            sru)
                . "$SCRIPTS_DIR/google/$PROJECT/run_google.sh"
                ;;
            *)  
                . "$SCRIPTS_DIR/google/$PROJECT/run_google.sh"
                ;;
        esac
        ;;
    testflinger)
        sudo rm -f $TF_DATA/*
        case "$TESTS_DEVICE" in
            device)
                . "$SCRIPTS_DIR/test_flinger/$PROJECT/job_device.sh"
                ;;
            vm)
                . "$SCRIPTS_DIR/test_flinger/$PROJECT/job_vm.sh"
                ;;
            *)
                echo "$TESTS_DEVICE not supported for testflinger"
                exit 1  
                ;;
        esac
        . "$SCRIPTS_DIR/test_flinger/run_job.sh"
        ;;
    *)
        echo "$TESTS_BACKEND not supported"
        exit 1
        ;;
esac