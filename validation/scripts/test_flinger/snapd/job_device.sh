#!/bin/bash

echo "Creating job for snapd using a device"

if [ -z "$IMAGE_URL" ]; then
    PROVISION_METHOD="channel"
    PROVISION_VAR="$CHANNEL"
else
    PROVISION_METHOD="url"
    PROVISION_VAR="$IMAGE_URL"
fi

. "$SCRIPTS_DIR/utils/snap_info.sh"
if ! dpkg -l jq unzip >/dev/null; then
    sudo apt install -y jq unzip
fi

if [ "$BRANCH" = beta ]; then
    BRANCH=$(get_beta_branch "$ARCH")
elif [ "$BRANCH" = edge ]; then
    BRANCH=$(get_edge_commit "$ARCH")
fi

DEVICE_IP='$DEVICE_IP'

cat > job.yaml <<EOF
job_queue: $DEVICE_QUEUE
global_timeout: 36000
provision_data:
    $PROVISION_METHOD: $PROVISION_VAR
test_data:
    test_cmds: |
        #!/bin/bash
        sudo apt update || ps aux | grep apt
        sudo apt install -y git curl sshpass jq unzip
        git clone $JOBS_URL
        (cd $JOBS_PROJECT && git checkout $JOBS_BRANCH)
        "$JOBS_PROJECT/validation/scripts/utils/get_project.sh" "$SNAPD_URL" "$PROJECT" "$BRANCH" "$COMMIT"
        $PRE_HOOK
        . $PROJECT/tests/lib/external/prepare-ssh.sh "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER"
        . $JOBS_PROJECT/validation/scripts/utils/remote/add_root_key.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS"
        . $JOBS_PROJECT/validation/scripts/utils/remote/refresh.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS" "$CHANNEL" "$CORE_CHANNEL" "$SNAPD_CHANNEL"
        . $JOBS_PROJECT/validation/scripts/utils/register_device.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS" "$REGISTER_EMAIL"
        . $JOBS_PROJECT/validation/scripts/utils/run_setup.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS" "$SETUP"
        . $JOBS_PROJECT/validation/scripts/utils/get_spread.sh "$SPREAD_URL"
        . $JOBS_PROJECT/validation/scripts/utils/run_spread.sh "$DEVICE_IP" "$DEVICE_PORT" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
        $POST_HOOK
EOF

export TF_JOB="$(pwd)/job.yaml"
