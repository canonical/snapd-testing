#!/bin/sh
set -ex

INSTANCE_IP="${1:-localhost}"
INSTANCE_PORT="${2:-8022}"
ME="${3:-$(whoami)}"
USER_NAME="${4:-test}"
USER_PASS="${5:-}"
USER_TYPE="${6:-external}"

execute_remote(){
    # shellcheck disable=SC2029
    ssh -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "$ME@$INSTANCE_IP" "$@"
}

if [ "$USER_TYPE" = "regular" ]; then
	TYPE=""
else
	TYPE="--external"
fi

execute_remote "sudo adduser --quiet $TYPE --disabled-password --gecos '' $USER_NAME"
if ! [ -z $USER_PASS ]; then
	execute_remote "echo $USER_NAME:$USER_PASS | sudo chpasswd"
fi
execute_remote "echo '$USER_NAME ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-$USER_NAME"