#!/bin/bash

TF_JOB=$1

if [ -z "$TF_JOB" ]; then
    echo "Job file var cannot be empty"
fi

echo "Creating job for snapd using a device"

if [ -n "$DEVICE_DISTRO" ]; then
    PROVISION_METHOD="distro"
    PROVISION_VAR="$DEVICE_DISTRO"
elif [ -n "$IMAGE_URL" ]; then
    PROVISION_METHOD="url"
    PROVISION_VAR="$IMAGE_URL"
else
    PROVISION_METHOD="channel"
    PROVISION_VAR="$CHANNEL"
fi

DEVICE_IP='$DEVICE_IP'

cat > "$TF_JOB" <<EOF
job_queue: $DEVICE_QUEUE
global_timeout: 50400
provision_data:
    $PROVISION_METHOD: $PROVISION_VAR
test_data:
    test_cmds: |
        #!/bin/bash
        set -x
        sudo apt update
        sudo apt install -y git curl sshpass jq unzip
        git clone "$JOBS_URL"
        (cd "$JOBS_PROJECT" && git checkout "$JOBS_BRANCH")
        export PATH="\$PATH":"$JOBS_PROJECT"/external/snapd-testing-tools/tools:"$JOBS_PROJECT"/external/snapd-testing-tools/remote

        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.setup config --host "$DEVICE_IP" --port "$DEVICE_PORT" --user "$DEVICE_USER" --pass "$DEVICE_PASS"
        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.wait-for ssh
        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.refresh full
        "$JOBS_PROJECT"/validation/tests/utils/get-project.sh "$PROJECT_URL" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
        "$JOBS_PROJECT"/validation/tests/utils/prepare-ssh.sh "$DEVICE_USER" "$DEVICE_PASS"
        "$JOBS_PROJECT"/validation/tests/utils/register-device.sh "$REGISTER_EMAIL"
        "$JOBS_PROJECT"/validation/tests/utils/add-root-key.sh
        "$JOBS_PROJECT"/validation/tests/utils/get-spread.sh "$SPREAD_URL"
        "$JOBS_PROJECT"/validation/tests/utils/run-spread.sh "$DEVICE_IP" "$DEVICE_PORT" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
EOF

if [ "$IS_USERNAME_REQUIRED" = true ]; then
    cat >> "$TF_JOB" <<EOF
    test_username: $DEVICE_USER
    test_password: $DEVICE_PASS
EOF
fi
