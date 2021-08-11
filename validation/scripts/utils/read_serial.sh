#!/bin/bash

REUSE_PID=$1
REUSE_FILE=".spread-reuse.${REUSE_PID}.yaml"

if [ -z "$REUSE_PID" ]; then
	echo "Reuse PID required, exiting..."
	exit 1
fi

if [ ! -f "$REUSE_FILE" ]; then
	echo "Reuse file not found, exiting..."
	exit 1
fi

for i in $(seq 10); do
	NAMES="$(cat $REUSE_FILE | grep name: | awk '{ print $2 }')"
	if [ -z "$NAMES" ]; then
		echo "Names not ready yet, retrying..."
		sleep 5
	else
		echo "Names alreyad set: $NAMES"
		continue
	fi
done

# Save all the logs in a same dir
LOGSDIR=gce-serial-logs
mkdir -p "$LOGSDIR"

# We use 240 because 2400 is a 40 minutes kill-timeout 
for i in $(seq 240); do
	if [ ! -f "$REUSE_FILE" ]; then
		echo "Reuse file deleted, exiting..."
		exit
	fi
	NAMES="$(cat $REUSE_FILE | grep name: | awk '{ print $2 }')"
	for NAME in $NAMES; do
		LOGNAME="${LOGSDIR}/${NAME}.serial.log"
		if [ ! -f "$LOGNAME" ]; then
			echo "Starting new serial log $LOGNAME"
			gcloud compute instances tail-serial-port-output --zone us-east1-b "$NAME" > "$LOGNAME" 2>&1 &
		fi
	done
	sleep 10
done
