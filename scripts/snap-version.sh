#!/bin/bash

snap=$1
channel=$2
arch=$3

if [ -z "$snap" ] || [ -z "$channel" ] || [ -z "$arch" ]; then
    echo "snap, channel and architecture need to be defined, exiting ..."
    echo "usage: snap-version.sh <snapname> <channel> <architecture>"
    exit 1
fi

curl -s -H "Snap-Device-Architecture: amd64" -H "Snap-Device-Series: 16" -X GET -H "Content-Type: application/json" "https://api.snapcraft.io/v2/snaps/info/$snap" > snap.json

iters=$(cat snap.json | jq '."channel-map"' | jq length)

found="false"
for iter in $(seq 0 $iters); do
    curr_channel=$(jq -r ".\"channel-map\"[$iter].channel.name" snap.json)
    curr_arch=$(jq -r ".\"channel-map\"[$iter].channel.architecture" snap.json)
    curr_ver=$(jq -r ".\"channel-map\"[$iter].version" snap.json)
    if [ "$channel" == "$curr_channel" ] && [ "$arch" = "$curr_arch" ]; then
        echo "$curr_ver"
        found="true"
        break
    fi
done

rm -f snap.json
if [ "$found" == "false" ]; then
    echo "version not found for snap: \"$snap\", channel: \"$channel\" and architecture: \"$arch\""
    exit 1
fi