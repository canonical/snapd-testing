#!/bin/bash

"$SCRIPTS_DIR/utils/get_project.sh" "$SNAPD_URL" "$PROJECT" "" ""

(cd $PROJECT && git reset --hard origin && git fetch origin && git checkout $BRANCH && git pull && git checkout $COMMIT)
$PRE_HOOK
. "$SCRIPTS_DIR/utils/run_setup.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$SETUP" || true
$POST_HOOK
