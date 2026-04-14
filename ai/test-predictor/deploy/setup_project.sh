#!/bin/bash

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV_DIR="$PROJECT_ROOT/.venv"

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <username>"
    echo "Example: $0 ubuntu"
    exit 1
fi

TARGET_USER=$1

echo "--- Setting up Python environment in: $PROJECT_ROOT ---"

echo "Installing python3.10-venv (requires sudo)..."
sudo apt-get update
sudo apt-get install -y python3.10-venv

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists, reusing it..."
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

echo "Upgrading pip..."
python3 -m pip install --upgrade pip

echo "Installing project Python dependencies..."
python3 -m pip install \
    tensorflow \
    pandas \
    numpy \
    scikit-learn \
    flask \
    gunicorn \
    requests \
    apscheduler \
    statsmodels \
    networkx

echo "--- Environment setup complete ---"

echo "Running service setup for user: $TARGET_USER"
"$PROJECT_ROOT/deploy/setup_services.sh" "$TARGET_USER"
