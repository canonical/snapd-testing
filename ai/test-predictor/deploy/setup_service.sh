#!/bin/bash

# Check if username is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <username>"
    echo "Example: $0 ubuntu"
    exit 1
fi

TARGET_USER=$1
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "--- Deploying for User: $TARGET_USER the project in: $PROJECT_ROOT ---"

# Inject User and Home into the templates
sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$PROJECT_ROOT|g" \
    "$PROJECT_ROOT/deploy/api.service.template" > "$PROJECT_ROOT/deploy/test-predictor-api.service"

sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$PROJECT_ROOT|g" \
    "$PROJECT_ROOT/deploy/predictor.service.template" > "$PROJECT_ROOT/deploy/test-predictor.service"

sed -e "s|{{USER}}|$TARGET_USER|g" -e "s|{{HOME}}|$PROJECT_ROOT|g" \
    "$PROJECT_ROOT/deploy/trainer.service.template" > "$PROJECT_ROOT/deploy/test-predictor-trainer.service"

# Link and Reload
sudo ln -sf "$PROJECT_ROOT/deploy/test-predictor-api.service" /etc/systemd/system/test-predictor-api.service
sudo ln -sf "$PROJECT_ROOT/deploy/test-predictor.service" /etc/systemd/system/test-predictor.service
sudo ln -sf "$PROJECT_ROOT/deploy/test-predictor-trainer.service" /etc/systemd/system/test-predictor-trainer.service
sudo ln -sf "$PROJECT_ROOT/deploy/test-predictor-cleaner.service" /etc/systemd/system/test-predictor-cleaner.service
sudo systemctl daemon-reload

echo "Enabling and Restarting services to apply changes..."
sudo systemctl enable --now test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner
sudo systemctl restart test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner

echo "--- Deployment Complete ---"
sudo systemctl status test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner --no-pager -l
