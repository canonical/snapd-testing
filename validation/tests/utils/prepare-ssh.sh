#!/bin/sh
set -ex

USER=$1
PASS=$2

remote.exec --user "$USER" --pass "$PASS" "sudo adduser --uid 12345 --extrausers --quiet --disabled-password --gecos '' test"
remote.exec --user "$USER" --pass "$PASS" "echo test:ubuntu | sudo chpasswd"
remote.exec --user "$USER" --pass "$PASS" "echo 'test ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-test"

remote.exec --user "$USER" --pass "$PASS" "sudo adduser --extrausers --quiet --disabled-password --gecos '' external"
remote.exec --user "$USER" --pass "$PASS" "echo external:ubuntu | sudo chpasswd"
remote.exec --user "$USER" --pass "$PASS" "echo 'external ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-external"
