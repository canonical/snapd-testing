#!/bin/bash

snap=$1

if [ -z "$snap" ]; then
    echo "snap need to be defined, exiting ..."
    exit 1
fi

curl -s -H "Snap-Device-Architecture: amd64" -H "Snap-Device-Series: 16" -X GET -H "Content-Type: application/json" "https://api.snapcraft.io/v2/snaps/info/$snap"  | python3 -m json.tool
