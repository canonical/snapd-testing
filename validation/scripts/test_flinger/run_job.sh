#!/bin/bash

echo "Preparing testflinger client"
if ! snap list testflinger-cli; then
    sudo snap install testflinger-cli --devmode
elif snap list testflinger-cli | grep -v devmode; then
    sudo snap remove testflinger-cli
    sudo snap install testflinger-cli --devmode
else
    sudo snap refresh testflinger-cli || true
fi

# Moving the job to the snap data dir when using jenkins
# This is needed due to user permissions needed to create
# a directory when running the submit command
# Also in other environments where sudo cannot be used we need
# to run the snap from the home directory
if [ "$(whoami)" = "jenkins" ]; then
    sudo mv "$TF_JOB" "$TF_DATA"/job.yaml
    TF_JOB="$TF_DATA"/job.yaml

    echo "Submitting job to testflinger"
    JOB_ID=$(sudo $TF_CLIENT submit -q $TF_JOB)
    echo "JOB_ID: ${JOB_ID}"

    echo "Print job: $TF_JOB "
    sudo cat $TF_JOB | tee $JOB_ID.job

    echo "Showing job data"
    sudo $TF_CLIENT poll ${JOB_ID} | tee $JOB_ID.log
else
    echo "Submitting job to testflinger"
    JOB_ID=$($TF_CLIENT submit -q $TF_JOB)
    echo "JOB_ID: ${JOB_ID}"

    echo "Print job: $TF_JOB "
    cat $TF_JOB | tee $JOB_ID.job

    echo "Showing job data"
    $TF_CLIENT poll ${JOB_ID} | tee $JOB_ID.log
fi