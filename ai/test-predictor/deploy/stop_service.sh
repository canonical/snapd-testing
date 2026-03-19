#!/bin/bash
echo "--- Stopping Test-Predictor Service ---"

# Stop the running process
sudo systemctl stop test-predictor test-predictor-api

# Verify it is stopped
sudo systemctl status test-predictor test-predictor-api --no-pager
