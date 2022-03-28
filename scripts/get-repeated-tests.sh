#!/bin/bash

TESTS_SET_1=$1
TESTS_SET_2=$2

if [ -z "$TESTS_SET_1" ] || [ -z "$TESTS_SET_2" ]; then
	echo "tests list cannot be empty, exiting..."
	exit 1
fi

MATCH=""
for test in $TESTS_SET_1; do
	if [[ $TESTS_SET_2 =~ (^|[[:space:]])$test($|[[:space:]]) ]]; then
		MATCH="$MATCH $test"
	fi
done

echo ""
echo "## RUN REPEATED TESTS"
echo "## Note: to get spread-debug run 'wget -c https://storage.googleapis.com/snapd-spread-tests/spread/spread-debug-amd64.tar.gz -O - | tar -xz'"
echo "spread-debug -debug -order -workers 1 $MATCH"