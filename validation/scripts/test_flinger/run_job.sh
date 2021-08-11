#!/bin/bash

echo "Preparing testflinger client"
if ! snap list testflinger-cli; then
    sudo snap install testflinger-cli
else
    sudo snap refresh testflinger-cli || true
fi

echo "Submitting job to testflinger"
JOB_ID=$(sudo $TF_CLIENT submit -q $TF_JOB)
echo "JOB_ID: ${JOB_ID}"

echo "Print job: $TF_JOB "
sudo cat $TF_JOB | tee $JOB_ID.job

echo "Showing job data"
sudo $TF_CLIENT poll ${JOB_ID} | tee $JOB_ID.log
