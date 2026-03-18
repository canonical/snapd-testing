#!/bin/bash
echo "--- Stopping Test-Predictor Service ---"

# Stop the running process
sudo systemctl stop test-predictor

# Verify it is stopped
sudo systemctl status test-predictor --no-pager
