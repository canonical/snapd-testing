#!/bin/bash
set -x

echo "Running setup command"

if [ "$#" -ne 5 ]; then
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
USER=$3
PASS=$4
SETUP=$5

if [ -z "$SETUP" ]; then
    echo "No setup command to run"
elif [ -z "$PASS" ]; then
    echo "Running setup command (no pass): $SETUP"
    ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p $DEVICE_PORT $USER@$DEVICE_IP "$SETUP"
else
    echo "Running setup command (with pass): $SETUP"
    sshpass -p $PASS ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p $DEVICE_PORT $USER@$DEVICE_IP "$SETUP"
fi
