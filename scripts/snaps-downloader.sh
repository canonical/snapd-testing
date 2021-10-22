#!/bin/bash

show_help() {
    echo "usage: snaps-downloader.sh [--channel <channel>] [--arch <arch>] [--revision <rev>] <snap>"
}

if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        --channel)
            channel="$2"
            shift 2
            ;;
        --arch)
            arch="$2"
            shift 2
            ;;
        --revision)
            revision="$2"
            shift 2
            ;;
        *)
            snap="$1"
            shift 1
            ;;
    esac
done

if [ -z "$snap" ]; then
    echo "snap need to be defined, exiting ..."
    exit 1
fi

if [ -n "$arch" ] && [ -n "$revision" ]; then
    echo "arch or revision can't be used together, exiting ..."
    exit 1
fi

curl -s -H "Snap-Device-Architecture: ${NESTED_ARCHITECTURE:-amd64}" -H "Snap-Device-Series: 16" -X GET -H "Content-Type: application/json" "https://api.snapcraft.io/v2/snaps/info/$snap" > snap.json

errors=$(cat snap.json | jq -r ".\"error-list\"" || true)
if [ -n "$errors" ] && [ "$errors" != "null" ]; then
    echo "errors detected requesting snap information:"
    echo "$errors"
    exit 1
fi

if [ -n "$revision" ]; then
    # Download an specific revision
    first_url=$(cat snap.json | jq -r ".\"channel-map\"[0].download.url")
    first_id="$(basename $first_url | cut -f1 -d_)"
    final_url="https://api.snapcraft.io/api/v1/snaps/download/${first_id}_${revision}.snap"
    echo "downloading snap from $final_url"
    wget "$final_url" -q -O "${snap}_${revision}.snap"
    echo "new snap downloaded at ${snap}_${revision}.snap"
else
    # Download based on arch and/or channel
    iters=$(cat snap.json | jq '."channel-map"' | jq length)

    for iter in $(seq 0 $iters); do
        curr_channel=$(jq -r ".\"channel-map\"[$iter].channel.name" snap.json)
        curr_arch=$(jq -r ".\"channel-map\"[$iter].channel.architecture" snap.json)

        if [ "$curr_channel" = "$channel" ] && ( [ -z "$arch" ] || [ "$curr_arch" = "$arch" ] ); then
        	curr_url=$(jq -r ".\"channel-map\"[$iter].download.url" snap.json)
        	curr_rev=$(jq -r ".\"channel-map\"[$iter].revision" snap.json)
        	
        	echo "downloading snap from $curr_url"
        	wget "$curr_url" -q -O "${snap}-${channel}-${curr_arch}_${curr_rev}.snap"
        	echo "new snap downloaded at ${snap}-${channel}-${curr_arch}_${curr_rev}.snap"
        fi
    done
fi

rm -f snap.json
