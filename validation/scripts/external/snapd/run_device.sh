#!/bin/bash

"$SCRIPTS_DIR/utils/get_project.sh" "$SNAPD_URL" "$PROJECT" "" ""

. "$SCRIPTS_DIR/utils/snap_info.sh"
if ! dpkg -l jq unzip >/dev/null; then
	sudo apt install -y jq unzip
fi

if [ "$BRANCH" = beta ]; then
	BRANCH=$(get_beta_branch "$ARCH")
elif [ "$BRANCH" = edge ]; then
	BRANCH=$(get_edge_commit "$ARCH")
fi

(cd $PROJECT && git reset --hard origin && git fetch origin && git checkout $BRANCH && git pull && git checkout $COMMIT)
$PRE_HOOK
. "$PROJECT/tests/lib/external/prepare-ssh.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" || true
. "$SCRIPTS_DIR/utils/add_root_key.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" || true
. "$SCRIPTS_DIR/utils/refresh.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$CHANNEL" "$CORE_CHANNEL" "$SNAPD_CHANNEL"
. "$SCRIPTS_DIR/utils/register_device.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$REGISTER_EMAIL" || true
. "$SCRIPTS_DIR/utils/run_setup.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$SETUP" || true
. "$SCRIPTS_DIR/utils/get_spread.sh" "$SPREAD_URL"
. "$SCRIPTS_DIR/utils/run_spread.sh" "$DEVICE_IP" "$DEVICE_PORT" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
$POST_HOOK
