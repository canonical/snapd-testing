#!/bin/bash 
set -x

PROJECT_URL=$1
PROJECT_NAME=$2
TARGET_DIR=$3
BRANCH=${4:-master}
VERSION=${5:-uc16}
ARCH=${6:-}

if [ -z "$PROJECT_NAME" ]; then
    echo "Project name cannot be empty, exiting..."
    exit 1
fi

if [ "$BRANCH" = beta ] || [ "$BRANCH" = edge ]; then
    # Get current dir
    CURR_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
    . "$CURR_DIR/snap_info.sh"

    SNAP=core
    if [ "$VERSION" != '16' ]; then
        SNAP=snapd
    fi

    if [ "$BRANCH" = beta ]; then
        echo "Getting beta branch, remember that it just works for snapd and core snaps testing"
        BRANCH=$(get_beta_branch "$SNAP" "$ARCH")
    elif [ "$BRANCH" = edge ]; then
        echo "Getting edge branch, remember that it just works for core snap testing"
        BRANCH=$(get_edge_commit "$SNAP" "$ARCH")
    fi
fi
echo "Using branch $BRANCH for project $PROJECT_NAME"

echo "Getting project from the defined url"
SNAPD_NAME=snapd
SNAPD_ZIP="https://github.com/snapcore/snapd/archive/$BRANCH.zip"
CCONF_NAME=console-conf-tests
CCONF_ZIP="https://github.com/sergiocazzolato/console-conf-tests/archive/$BRANCH.zip"
JOBS_NAME=snapd-testing
JOBS_ZIP="https://github.com/snapcore/snapd-testing/archive/$BRANCH.zip"

if [ -d "$TARGET_DIR/$PROJECT_NAME" ]; then
    ( cd "$TARGET_DIR/$PROJECT_NAME" && git reset --hard origin && git fetch origin && git checkout "$BRANCH" && git pull )
else
    if [ -z "$PROJECT_URL" ] || [ "$PROJECT_URL" = 'default' ]; then 
        if [ "$PROJECT_NAME" == "$SNAPD_NAME" ]; then
            wget -q "$SNAPD_ZIP"
        elif [ "$PROJECT_NAME" == "$CCONF_NAME" ]; then
            wget -q "$CCONF_ZIP"
        elif [ "$PROJECT_NAME" == "$JOBS_NAME" ]; then
            wget -q "$JOBS_ZIP"
        else
            echo "Project configuration not supported, exiting..."
            exit 1
        fi
        rm -rf "$PROJECT_NAME"-"$BRANCH"
        unzip -q "$BRANCH.zip"
        mv "$PROJECT_NAME"-"$BRANCH" "$TARGET_DIR/$PROJECT_NAME"
        rm -f "$BRANCH.zip"
    else
        #git clone --branch "$BRANCH" "$PROJECT_URL" "$TARGET_DIR/$PROJECT_NAME"
        git clone --branch tests-fix-run-spread-test https://github.com/sergiocazzolato/snapd.git "$TARGET_DIR/$PROJECT_NAME" 
    fi
fi

echo "Project downloaded and configured."
