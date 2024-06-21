#!/bin/sh
MACHINE=$1
FILE=$2

if [ -z "$MACHINE" ] || [ -z "$FILE" ]; then
    echo "Machine name and log file are required"
    exit 1
fi

tests="$(grep -E "Executing .*($MACHINE)" "$FILE" | sed 's/.* Executing//' | sed "s/($MACHINE).*//" | tr '\n' ' ')"
echo "spread-debug -debug -order -workers 1 $tests"