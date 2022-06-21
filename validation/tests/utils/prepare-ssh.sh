#!/bin/sh
set -ex

INSTANCE_IP="${1:-localhost}"
INSTANCE_PORT="${2:-8022}"
USER="${3:-user1}"

execute_remote(){
    # shellcheck disable=SC2029
    ssh -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -p "$INSTANCE_PORT" "$USER@$INSTANCE_IP" "$@"
}

for _ in $(seq 10); do
    if execute_remote "sudo adduser --uid 12345 --extrausers --quiet --disabled-password --gecos '' test"; then
        break
    else
        sleep 5
    fi
    echo "Couldn't add user test, retrying..."
done
execute_remote "echo test:ubuntu | sudo chpasswd"
execute_remote "echo 'test ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-test"

for _ in $(seq 10); do
    if execute_remote "sudo adduser --extrausers --quiet --disabled-password --gecos '' external"; then
        break
    else
        sleep 5
    fi
    echo "Couldn't add user external, retrying..."
done
execute_remote "echo external:ubuntu | sudo chpasswd"
execute_remote "echo 'external ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-external"
