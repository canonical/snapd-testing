#!/bin/bash

CHANNEL=$1


"$JOBS_PROJECT"/validation/tests/utils/get-project.sh "$PROJECT_URL" "$PROJECT" "$BRANCH" "$VERSION" "$ARCH" "$COMMIT"
sed "/^backends:/r validation/config/spread/tf_${CHANNEL}_spread.yaml" -i "$PROJECT"/spread.yaml
"$JOBS_PROJECT"/validation/tests/utils/run-spread.sh "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"

