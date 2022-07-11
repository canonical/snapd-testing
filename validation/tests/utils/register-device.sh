#!/bin/bash
set -x

echo "Register device"

EMAIL=$1

SCRIPT="( snap list core &>/dev/null && sudo snap install --edge jq ) || (snap list core18 &>/dev/null && sudo snap install jq-core18 && sudo snap alias jq-core18.jq jq) || (snap list core20 &>/dev/null && sudo snap install --edge jq-core20 && sudo snap alias jq-core20.jq jq) || (snap list core22 &>/dev/null && sudo snap install --edge jq-core22 && sudo snap alias jq-core22.jq jq); \
        sudo cp /var/lib/snapd/state.json /var/lib/snapd/state.json.bak; \
        sudo cat /var/lib/snapd/state.json.bak | jq -r '.data.auth.users=[]' | sudo tee /var/lib/snapd/state.json > /dev/null; \
        sudo systemctl restart snapd; \
        sudo snap create-user $EMAIL; \
        sudo snap remove jq jq-core18 jq-core20 jq-core22"

if [ -z "$EMAIL" ]; then
    echo "No email provided to make the registration"
else
    echo "Registering device (no pass): $SCRIPT"
    remote.exec "$SCRIPT"
fi

