#!/bin/bash

get_snap_revision(){
    local ARCHITECTURE=$1
    local SNAP=$2
    local CHANNEL=$3

    curl -s -H "X-Ubuntu-Architecture: $ARCHITECTURE" -H 'X-Ubuntu-Series: 16' https://search.apps.ubuntu.com/api/v1/snaps/details/"$SNAP"?channel="$CHANNEL" | jq -j '.revision'
}

get_snap_version(){
    local ARCHITECTURE=$1
    local SNAP=$2
    local CHANNEL=$3

    version=$(curl -s -H "X-Ubuntu-Architecture: $ARCHITECTURE" -H 'X-Ubuntu-Series: 16' https://search.apps.ubuntu.com/api/v1/snaps/details/"$SNAP"?channel="$CHANNEL" | jq -j '.version')

    # Clean version when it is like 16-2.38
    version="${version//16-/}"

    # Clean version when it is like 2.38~pre1
    echo "$version" | cut -f1 -d "~"
}

get_snap_last_updated(){
    local ARCHITECTURE=$1
    local SNAP=$2
    local CHANNEL=$3

    curl -s -H "X-Ubuntu-Architecture: $ARCHITECTURE" -H 'X-Ubuntu-Series: 16' https://search.apps.ubuntu.com/api/v1/snaps/details/"$SNAP"?channel="$CHANNEL" | jq -j '.last_updated'
}

get_beta_branch(){
    local ARCHITECTURE=$1
    local version
    version=$(get_snap_version "$ARCHITECTURE" core beta)

    if git ls-remote --tags git@github.com:snapcore/snapd.git "$version" | grep -q "$version"; then
        echo "$version"
    elif git ls-remote --heads git@github.com:snapcore/snapd.git "release/$version" | grep -q "$version"; then
        echo "release/$version"
    else
        echo "NULL"
    fi
}

get_edge_commit(){
    local ARCHITECTURE=$1
    local last_updated
    local snapd_vendor_commit

    git clone --bare -q https://git.launchpad.net/snapd-vendor
    cd snapd-vendor.git || return
     
    last_updated=$(get_snap_last_updated "$ARCHITECTURE" core edge)
    if [ "$last_updated" = null ]; then
        echo "NULL"
    else
        snapd_vendor_commit=$(git rev-list -n 1 --first-parent --before="$last_updated" master)
        git log -n1 "$snapd_vendor_commit" | grep "Content updated" | sed -e "s|.*(\(.*\))|\1|"
    fi

    cd .. || return
    rm -rf snapd-vendor.git
}