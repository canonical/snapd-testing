#!/bin/bash

snap=$1
channel=${2:-""}

if [ -z "$snap" ]; then
    echo "snap need to be defined, exiting ..."
    exit 1
fi

curl -s -H "Snap-Device-Architecture: amd64" -H "Snap-Device-Series: 16" -X GET -H "Content-Type: application/json" "https://api.snapcraft.io/v2/snaps/info/$snap" > snap.json

iters=$(cat snap.json | jq '."channel-map"' | jq length)

echo "Snap: $snap"
echo "-----------"
for iter in $(seq 0 $iters); do
    curr_channel=$(jq -r ".\"channel-map\"[$iter].channel.name" snap.json)
    curr_arch=$(jq -r ".\"channel-map\"[$iter].channel.architecture" snap.json)
  	curr_rev=$(jq -r ".\"channel-map\"[$iter].revision" snap.json)
    if [ -z "$channel" ] || [ "$channel" = "$curr_channel" ]; then
        echo "Channel: $curr_channel"
        echo "Arch: $curr_arch"
        echo "Rev: $curr_rev"
        echo "-----------"
    fi
done

rm -f snap.json
