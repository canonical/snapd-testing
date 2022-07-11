#!/bin/bash

TF_JOB=$1

if [ -z "$TF_JOB" ]; then
    echo "Job file var cannot be empty"
fi

echo "Creating job for snapd using a vm"

DEVICE_IP='$DEVICE_IP'

cat > "$TF_JOB" <<EOF
job_queue: $DEVICE_QUEUE
global_timeout: 50400
provision_data:
    distro: ${DEVICE_DISTRO:-bionic}
test_data:
    test_cmds: |
        #!/bin/bash

        cat > script.sh <<END
        #!/bin/bash -e
        apt -qq update
        apt -qq install -y git curl sshpass jq unzip
        git clone "$JOBS_URL"
        (cd "$JOBS_PROJECT" && git checkout "$JOBS_BRANCH")
        export PATH="\$PATH":"$JOBS_PROJECT"/external/snapd-testing-tools/tools:"$JOBS_PROJECT"/external/snapd-testing-tools/remote
        
        "$JOBS_PROJECT"/validation/tests/utils/get-project.sh "$PROJECT_URL" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.setup config --host "$VM_HOST" --port "$VM_PORT" --user "$VM_USER" --pass "$VM_PASS"
        "$JOBS_PROJECT"/validation/tests/utils/create-vm.sh "$ARCH" "$IMAGE_URL" "$USER_ASSERTION_URL" "$BUILD_SNAPD"
        "$JOBS_PROJECT"/validation/tests/utils/prepare-ssh.sh "$VM_USER" "$VM_PASS"
        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.setup config --host "$VM_HOST" --port "$VM_PORT" --user "$TEST_USER" --pass "$TEST_PASS"
        "$JOBS_PROJECT"/external/snapd-testing-tools/remote/remote.refresh full
        "$JOBS_PROJECT"/validation/tests/utils/register-device.sh "$REGISTER_EMAIL"
        "$JOBS_PROJECT"/validation/tests/utils/add-root-key.sh
        "$JOBS_PROJECT"/validation/tests/utils/get-spread.sh "$SPREAD_URL"
        "$JOBS_PROJECT"/validation/tests/utils/run-spread.sh "$VM_HOST" "$VM_PORT" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
        END

        ssh ${DEVICE_USER}@${DEVICE_IP} 'sudo bash -s' < script.sh        
EOF
