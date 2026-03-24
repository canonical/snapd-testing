#!/bin/bash
PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "--- Uninstalling Test-Predictor Solution ---"

# Stop and Disable (so they don't start on boot)
sudo systemctl disable --now test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner

# Remove the symlinks from systemd
sudo rm -f /etc/systemd/system/test-predictor.service 
sudo rm -f /etc/systemd/system/test-predictor-api.service
sudo rm -f /etc/systemd/system/test-predictor-trainer.service
sudo rm -f /etc/systemd/system/test-predictor-cleaner.service

# Clean up generated service files in deploy/
rm -f "$PROJECT_ROOT/deploy/test-predictor.service"
rm -f "$PROJECT_ROOT/deploy/test-predictor-api.service"
rm -f "$PROJECT_ROOT/deploy/test-predictor-trainer.service"
rm -f "$PROJECT_ROOT/deploy/test-predictor-cleaner.service"

# Reload systemd to finalize removal
sudo systemctl daemon-reload
sudo systemctl reset-failed

echo "Uninstall complete. Systemd units and temporary files removed."
