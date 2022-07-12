#!/bin/bash 
set -x

CERT_NAME="spread_external"
PASSPHRASE=""

# Create certificates
rm -f "/tmp/$CERT_NAME"
ssh-keygen -t rsa -N "$PASSPHRASE" -f "/tmp/$CERT_NAME"

# Authorize key in the remote system
remote.exec "sudo mkdir -p /root/.ssh"
remote.exec "sudo chmod 700 /root/.ssh"
remote.exec "sudo tee -a /root/.ssh/authorized_keys > /dev/null" < "/tmp/$CERT_NAME".pub
