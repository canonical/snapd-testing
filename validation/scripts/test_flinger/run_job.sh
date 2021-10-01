#!/bin/bash

echo "Preparing testflinger client"
if ! snap list testflinger-cli; then
    sudo snap install testflinger-cli
else
    sudo snap refresh testflinger-cli || true
fi

echo "Submitting job to testflinger"
JOB_ID=$($TF_CLIENT submit -q $TF_JOB)
echo "JOB_ID: ${JOB_ID}"

echo "Print job: $TF_JOB "
cat $TF_JOB | tee $JOB_ID.job

echo "Showing job data"
$TF_CLIENT poll ${JOB_ID} | tee $JOB_ID.log
