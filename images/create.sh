#!/bin/bash
set -x

CHANNEL=${1:-edge}
VERSION=${2:-"16"}
SNAPS=${3:-""}
PLATFORMS=${4:-"dragonboard pc-amd64 pc-i386 pi3 pi2"}

for platform in $PLATFORMS; do
    image_option=""
    if [[ "$platform" == pc* ]]; then
        if [[ "$VERSION" == 2* ]]; then
            image_option="--image-size 8G"
        else
            image_option="--image-size 3G"
        fi
    fi

    snaps=""
    if [ -n "$SNAPS" ]; then
        for snap in $SNAPS; do
            if [ -z "$snaps" ]; then
                snaps="--snap $snap"
            else
                snaps="$snaps --snap $snap"
            fi
        done
    fi

    output="./output/${platform}-${VERSION}-${CHANNEL}"
    sudo rm -rf "$output" && mkdir -p "$output"
    sudo ubuntu-image "$image_option" "$snaps" \
         -c "$CHANNEL" \
         -O "$output" \
         "./models/${platform}-${VERSION}.model"
done
