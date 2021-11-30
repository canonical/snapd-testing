#!/bin/bash

TF_JOB=$1

if [ -z "$TF_JOB" ]; then
    echo "Job file var cannot be empty"
fi

echo "Creating job for cconf using a device"

if [ -z "$IMAGE_URL" ]; then
    PROVISION_METHOD="channel"
    PROVISION_VAR="$CHANNEL"
else
    PROVISION_METHOD="url"
    PROVISION_VAR="$IMAGE_URL"
fi

DEVICE_IP='$DEVICE_IP'

cat > "$TF_JOB" <<EOF
job_queue: $DEVICE_QUEUE
provision_data:
    $PROVISION_METHOD: $PROVISION_VAR
test_data:
    test_cmds: |
        #!/bin/bash
        sudo apt update || ps aux | grep apt
        sudo apt install -y git curl sshpass jq unzip
        git clone "$JOBS_URL"
        (cd "$JOBS_PROJECT" && git checkout "$JOBS_BRANCH")
        "$JOBS_PROJECT"/validation/scripts/utils/get-project.sh "$CCONF_URL" "$PROJECT" "$BRANCH" ""
        "$PROJECT"/tests/console-conf-tests/external/prepare_ssh "$DEVICE_IP" "$DEVICE_PORT" "$DEVICE_USER"
        "$JOBS_PROJECT"/validation/scripts/utils/remote/add-root-key.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS"
        "$JOBS_PROJECT"/validation/scripts/utils/remote/refresh.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS" "$CHANNEL" "$CORE_CHANNEL" "$SNAPD_CHANNEL"
        "$JOBS_PROJECT"/validation/scripts/utils/register-device.sh "$DEVICE_IP" "$DEVICE_PORT" "$TEST_USER" "$TEST_PASS" "$REGISTER_EMAIL"
        "$JOBS_PROJECT"/validation/scripts/utils/get-spread.sh "$SPREAD_URL"
        "$JOBS_PROJECT"/validation/scripts/utils/run-spread.sh "$DEVICE_IP" "$DEVICE_PORT" "$PROJECT/tests/console-conf-tests" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
EOF