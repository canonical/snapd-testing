#!/bin/bash

"$SCRIPTS_DIR/utils/get-project.sh" "$PROJECT_URL" "$PROJECT" "" ""
(cd $PROJECT && git reset --hard origin && git fetch origin && git checkout $BRANCH && git pull && git checkout $COMMIT)
"$SCRIPTS_DIR/utils/get-spread.sh"
"$SCRIPTS_DIR/utils/run-spread.sh" "127.0.0.1" "22" "$PROJECT" "$SPREAD_TESTS" "$SPREAD_ENV" "$SKIP_TESTS" "$SPREAD_PARAMS"
