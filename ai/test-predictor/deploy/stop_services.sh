#!/bin/bash
echo "--- Stopping Test-Predictor Services ---"

# Stop the running processes
sudo systemctl stop test-predictor-ingestion test-predictor-explorer test-predictor-processor

# Verify they are stopped
sudo systemctl status test-predictor-* --no-pager
