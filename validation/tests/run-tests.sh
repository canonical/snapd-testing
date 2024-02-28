#!/bin/bash -x

echo "Running tests"

VALIDATION_DIR="$(dirname "$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )")"

if [ -z "$SPREAD_LOG" ]; then
    echo "Spread log not defined in environment config"
    exit 1
fi

if [ -z "$PROJECT" ]; then
    echo "Project not defined in environment config"
    exit 1
fi

if [ -z "$ARCH" ]; then
    echo "Architecture not defined in environment config"
    exit 1
fi


if [ "$ARCH_UT" = "amd64" ]; then
    "$VALIDATION_DIR"/tests/utils/get-project.sh "$cat SPREAD_ENV" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
    "$VALIDATION_DIR"/tests/utils/run-spread.sh "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
    LOG=$(find .artifacts -name spread.log)
    cp "$LOG" "$SPREAD_LOG"
else
    "$VALIDATION_DIR"/tests/utils/get-project.sh "$cat SPREAD_ENV" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
    sed "/^backends:/r "$VALIDATION_DIR"/config/spread/tf_${CHANNEL}_spread.yaml" -i "$PROJECT"/spread.yaml
    "$VALIDATION_DIR"/tests/utils/run-spread.sh "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS" | tee "$SPREAD_LOG"
fi
