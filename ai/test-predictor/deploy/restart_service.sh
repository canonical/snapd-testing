#!/bin/bash
echo "--- Restarting Test-Predictor Service ---"

# Stop the running process
sudo systemctl daemon-reload
sudo systemctl restart test-predictor

# Verify it is restarted
sudo systemctl status test-predictor --no-pager
