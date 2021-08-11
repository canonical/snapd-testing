#!/bin/bash

. "$SCRIPTS_DIR/utils/snap_info.sh"
if ! dpkg -l jq unzip >/dev/null; then
	sudo apt install -y jq unzip
fi

if [ "$BRANCH" = beta ]; then
	BRANCH=$(get_beta_branch "$ARCH")
elif [ "$BRANCH" = edge ]; then
	BRANCH=$(get_edge_commit "$ARCH")
fi

"$SCRIPTS_DIR/utils/get_project.sh" "$SNAPD_URL" "$PROJECT" "" ""
(cd $PROJECT && git reset --hard origin && git fetch origin && git checkout $BRANCH && git pull && git checkout $COMMIT)
$PRE_HOOK
. "$SCRIPTS_DIR/utils/get_spread.sh"
. "$SCRIPTS_DIR/utils/run_spread.sh" "127.0.0.1" "22" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
cp $PROJECT/spread.yaml.bak $PROJECT/spread.yaml
$POST_HOOK
