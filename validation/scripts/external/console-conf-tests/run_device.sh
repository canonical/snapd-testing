#!/bin/bash

git clone $CCONF_URL $PROJECT
(cd $PROJECT && git reset --hard origin && git fetch origin && git checkout $BRANCH && git pull)
$PRE_HOOK
. "$PROJECT/external/prepare_ssh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" || true
. "$SCRIPTS_DIR/utils/add_root_key.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" || true
. "$SCRIPTS_DIR/utils/refresh.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$CHANNEL" "$CORE_CHANNEL" "$SNAPD_CHANNEL"
. "$SCRIPTS_DIR/utils/register_device.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$REGISTER_EMAIL" || true
. "$SCRIPTS_DIR/utils/run_setup.sh" "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER" "$DEVICE_PASS" "$SETUP" || true
. "$SCRIPTS_DIR/utils/get_spread.sh" "$SPREAD_URL"
. "$SCRIPTS_DIR/utils/run_spread.sh" "$DEVICE_IP" "$DEVICE_PORT" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
$POST_HOOK
