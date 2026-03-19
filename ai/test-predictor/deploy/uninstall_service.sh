#!/bin/bash
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "--- Uninstalling Test-Predictor Solution ---"

# 1. Stop and Disable (so they don't start on boot)
sudo systemctl disable --now test-predictor test-predictor-api test-predictor-trainer

# 2. Remove the symlinks from systemd
sudo rm -f /etc/systemd/system/test-predictor.service 
sudo rm -f /etc/systemd/system/test-predictor-api.service
sudo rm -f /etc/systemd/system/test-predictor-trainer.service

# 3. Clean up generated service files in deploy/
rm -f "$PROJECT_ROOT/deploy/test-predictor.service"
rm -f "$PROJECT_ROOT/deploy/test-predictor-api.service"
rm -f "$PROJECT_ROOT/deploy/test-predictor-trainer.service"

# 4. Reload systemd to finalize removal
sudo systemctl daemon-reload
sudo systemctl reset-failed

echo "Uninstall complete. Systemd units and temporary files removed."