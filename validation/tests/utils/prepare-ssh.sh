#!/bin/sh
set -ex

INSTANCE_IP="${1:-localhost}"
INSTANCE_PORT="${2:-8022}"
USER="${3:-user1}"
PASS="${4:-}"

execute_remote(){
    if [ -z "$PASS" ]; then
        ssh -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "$USER@$INSTANCE_IP" "$@"
    else
        sshpass -p "$PASS" ssh -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "$USER@$INSTANCE_IP" "$@"
    fi
}

execute_remote "sudo adduser --uid 12345 --extrausers --quiet --disabled-password --gecos '' test"
execute_remote "echo test:ubuntu | sudo chpasswd"
execute_remote "echo 'test ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-test"

execute_remote "sudo adduser --extrausers --quiet --disabled-password --gecos '' external"
execute_remote "echo external:ubuntu | sudo chpasswd"
execute_remote "echo 'external ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-external"
