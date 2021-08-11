#!/bin/bash

KEY=${1:-default}
MODELS_PATH="./images/models"

for model in $(find $MODELS_PATH/*.json); do
    cat "$model" | snap sign -k "$KEY" >"${model/-model.json/.model}" 2>&1
done
