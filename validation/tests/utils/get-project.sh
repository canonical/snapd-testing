#!/bin/bash
set -x

PROJECT_URL=$1
PROJECT_NAME=$2
BRANCH=${3:-master}
COMMIT=${4:-}

SNAPD_NAME=snapd
SNAPD_ZIP="https://github.com/snapcore/snapd/archive/$BRANCH.zip"
CCONF_NAME=console-conf-tests
CCONF_ZIP="https://github.com/sergiocazzolato/console-conf-tests/archive/$BRANCH.zip"
JOBS_NAME=snapd-testing
JOBS_ZIP="https://github.com/snapcore/snapd-testing/archive/$BRANCH.zip"

if [ -z "$PROJECT_NAME" ]; then
	echo "Project name cannot be empty, exiting..."
	exit 1
fi

if [ "$BRANCH" = beta ] || [ "$BRANCH" = edge ]; then
	# Get current dir
	CURR_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
	. "$CURR_DIR/snap_info.sh"

	if [ "$BRANCH" = beta ]; then
	    BRANCH=$(get_beta_branch "$ARCH")
	elif [ "$BRANCH" = edge ]; then
	    BRANCH=$(get_edge_commit "$ARCH")
	fi
fi
echo "Using branch $BRANCH for project $PROJECT_NAME"

if [ -d "$PROJECT_NAME" ]; then
	( cd "$PROJECT_NAME" && git reset --hard origin && git fetch origin && git checkout "$BRANCH" && git pull )
else
	if [ -n "$PROJECT_URL" ] && [ ! "$PROJECT_URL" = 'default' ] ; then
		git clone --branch "$BRANCH" --progress "$PROJECT_URL" "$PROJECT_NAME"
	else
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
		mv "$PROJECT_NAME"-"$BRANCH" "$PROJECT_NAME"
		rm -f "$BRANCH.zip"
	fi
fi

if [ -n "$COMMIT" ]; then
	( cd "$PROJECT_NAME" && git checkout "$COMMIT" )
fi

echo "Project downloaded and configured."
