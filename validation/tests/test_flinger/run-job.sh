#!/bin/bash

TF_JOB=$1

if [ ! -f "$TF_JOB" ]; then
	echo "Job file does not exist"
fi

echo "listing configuration"
echo "NO_PROXY=$NO_PROXY"
echo "HTTP_PROXY=$HTTP_PROXY"
echo "HTTPS_PROXY=$HTTPS_PROXY"

echo "listing queues"
HTTP_PROXY=$HTTP_PROXY "$TF_CLIENT" list-queues

echo "Submitting job to testflinger"
JOB_ID=$($TF_CLIENT submit -q $TF_JOB)
echo "JOB_ID: ${JOB_ID}"

echo "Print job: $TF_JOB "
cat $TF_JOB

echo "Showing job data"
$TF_CLIENT poll ${JOB_ID}
