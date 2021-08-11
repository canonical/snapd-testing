#!/bin/bash

snap=$1
channel=${2:-stable}
arch=${3:-}

if [ -z "$snap" ]; then
    echo "snap need to be defined, exiting ..."
    exit 1
fi

curl -s -H "Snap-Device-Architecture: ${NESTED_ARCHITECTURE:-amd64}" -H "Snap-Device-Series: 16" -X GET -H "Content-Type: application/json" "https://api.snapcraft.io/v2/snaps/info/$snap" > snap.json

iters=$(cat snap.json | jq '."channel-map"' | jq length)

for iter in $(seq 0 $iters); do
    curr_channel=$(jq -r ".\"channel-map\"[$iter].channel.name" snap.json)
    curr_arch=$(jq -r ".\"channel-map\"[$iter].channel.architecture" snap.json)
    if [ "$curr_channel" = "$channel" ] && ( [ -z "$arch" ] || [ "$curr_arch" = "$arch" ] ); then
    	curr_url=$(jq -r ".\"channel-map\"[$iter].download.url" snap.json)
    	curr_rev=$(jq -r ".\"channel-map\"[$iter].revision" snap.json)
    	
    	echo "downloading snap from $curr_url"
    	wget "$curr_url" -q -O "$snap-$channel-$curr_arch-$curr_rev.snap"
    	echo "new snapd downloaded at $snap-$channel-$curr_arch-$curr_rev.snap"
    fi    
done

rm -f snap.json

