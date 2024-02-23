#!/bin/bash -x

echo "Running tests"

if [ -z "$TESTS_BACKEND" ]; then
    echo "Backend not defined in environment config"
    exit 1  
fi

if [ -z "$TESTS_DEVICE" ]; then
    echo "Test device not defined in environment config"
    exit 1  
fi

if [ -z "$PROJECT" ]; then
    echo "Project not defined in environment config"
    exit 1  
fi

VALIDATION_DIR="$(dirname "$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )")"

"$VALIDATION_DIR"/tests/utils/get-project.sh "$cat SPREAD_ENV" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
sed "/^backends:/r "$VALIDATION_DIR"/config/spread/tf_${CHANNEL}_spread.yaml" -i "$PROJECT"/spread.yaml
"$VALIDATION_DIR"/tests/utils/run-spread.sh "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"