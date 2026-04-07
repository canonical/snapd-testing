#!/bin/bash
echo "--- Restarting Test-Predictor Service ---"

# Stop the running process
sudo systemctl daemon-reload
sudo systemctl restart test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner test-predictor-dependency

# Verify it is restarted
sudo systemctl status test-predictor test-predictor-api test-predictor-trainer test-predictor-cleaner test-predictor-dependency --no-pager
