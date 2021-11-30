#!/bin/bash

TF_JOB=$1

if [ -z "$TF_JOB" ]; then
    echo "Job file var cannot be empty"
fi

echo "Creating job for snapd using a vm"

HOST=localhost
PORT=8022
DEVICE_IP='$DEVICE_IP'

cat > "$TF_JOB" <<EOF
job_queue: $DEVICE_QUEUE
global_timeout: 50400
provision_data:
    distro: ${DEVICE_DISTRO:-bionic}
test_data:
    test_cmds: |
        #!/bin/bash
        ssh ${DEVICE_USER}@${DEVICE_IP} "sudo apt update || ps aux | grep apt"
        ssh ${DEVICE_USER}@${DEVICE_IP} "sudo apt install -y git curl jq sshpass unzip"
        ssh ${DEVICE_USER}@${DEVICE_IP} "git clone $JOBS_URL"
        ssh ${DEVICE_USER}@${DEVICE_IP} "(cd $JOBS_PROJECT && git checkout $JOBS_BRANCH)"
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/get-project.sh \"$SNAPD_URL\" \"$PROJECT\" \"$BRANCH\" ''"
        ssh ${DEVICE_USER}@${DEVICE_IP} "sudo $JOBS_PROJECT/validation/scripts/utils/remote/create-vm.sh \"$ARCH\" \"$IMAGE_URL\" \"$USER_ASSERTION_URL\" \"$BUILD_SNAPD\""
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/remote/add-root-key.sh \"$HOST\" \"$PORT\" \"$TEST_USER\" \"$TEST_PASS\""
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/remote/refresh.sh \"$HOST\" \"$PORT\" \"$TEST_USER\" \"$TEST_PASS\" \"$CHANNEL\" \"$CORE_CHANNEL\" \"$SNAPD_CHANNEL\""
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/register-device.sh \"$HOST\" \"$PORT\" \"$TEST_USER\" \"$TEST_PASS\" \"$REGISTER_EMAIL\""
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/get-spread.sh \"$SPREAD_URL\""
        ssh ${DEVICE_USER}@${DEVICE_IP} "$JOBS_PROJECT/validation/scripts/utils/run-spread.sh \"$HOST\" \"$PORT\" \"$PROJECT\" \"$SPREAD_TESTS\" \"$SPREAD_ENV\" \"$SKIP_TESTS\" \"$SPREAD_PARAMS\""
EOF
