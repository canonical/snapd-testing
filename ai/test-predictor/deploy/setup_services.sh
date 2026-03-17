#!/bin/bash

# Check if username is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <username>"
    echo "Example: $0 ubuntu"
    exit 1
fi

TARGET_USER=$1
# Get the home directory for that specific user
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

if [ -z "$TARGET_HOME" ]; then
    echo "Error: User '$TARGET_USER' not found on this system."
    exit 1
fi

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "--- Deploying for User: $TARGET_USER ($TARGET_HOME) ---"

# 1. Inject User and Home into the templates
sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$TARGET_HOME|g" \
    "$PROJECT_ROOT/deploy/ingestion.service.template" > "$PROJECT_ROOT/deploy/ingestion.service"

sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$TARGET_HOME|g" \
    "$PROJECT_ROOT/deploy/explorer.service.template" > "$PROJECT_ROOT/deploy/explorer.service"

sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$TARGET_HOME|g" \
    "$PROJECT_ROOT/deploy/processor.service.template" > "$PROJECT_ROOT/deploy/processor.service"

# 2. Link and Reload
sudo ln -sf "$PROJECT_ROOT/deploy/ingestion.service" /etc/systemd/system/test-predictor-ingestion.service
sudo ln -sf "$PROJECT_ROOT/deploy/explorer.service" /etc/systemd/system/test-predictor-explorer.service
sudo ln -sf "$PROJECT_ROOT/deploy/processor.service" /etc/systemd/system/test-predictor-processor.service

sudo systemctl daemon-reload

echo "Enabling and Restarting services to apply changes..."
sudo systemctl enable --now test-predictor-ingestion test-predictor-explorer test-predictor-processor
sudo systemctl restart test-predictor-ingestion test-predictor-explorer test-predictor-processor

echo "--- Deployment Complete ---"
sudo systemctl status test-predictor-ingestion test-predictor-explorer test-predictor-processor --no-pager -l
