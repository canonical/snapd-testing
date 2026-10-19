# spread-test-selector

`spread-test-selector` is a machine charm that exposes a small local API for deciding which spread tests should run from a set of changed files and refreshing the test map from MongoDB through `query_features.py` from the snapd repository.

## What it does

The charm manages this state on the unit:

- a MongoDB credentials JSON file written from a Juju secret
- `spread-execution-map.json`, `interface-users.json` and `base-executions.json`, the execution data used to look up tests by backend and changed Go file

The service listens on `listen-host:listen-port` and can handle multiple requests in parallel.

## Configuration

Available config options are defined in [charmcraft.yaml](charmcraft.yaml):

- `listen-host`: address the API service listens on, default `0.0.0.0` (all interfaces, so CI can reach it)
- `listen-port`: port for the local API service, default `8181`
- `mongodb-secret-id`: Juju secret ID containing the MongoDB credentials JSON
- `snapd-repo-url`: git repository to fetch `tests/utils/features` (`query_features.py`) from, default `https://github.com/canonical/snapd.git`
- `snapd-ref`: branch, tag or full commit SHA of `snapd-repo-url` to fetch it from, default `master`
- `query-features-path`: optional path to an existing `query_features.py` on the unit; when set, nothing is fetched
- `execution-data-dir`: directory containing `spread-execution-map.json`, `interface-users.json` and `base-executions.json`
- `refresh-helper-command`: optional override for the helper command that rebuilds `spread-execution-map.json` and `base-executions.json`
- `base-tests-max-minutes`: maximum execution time, in minutes, for the base test coverage set that runs for every change, default `15`; `0` means no limit (cover every feature)
- `refresh-timeout-seconds`: timeout used when refreshing from MongoDB
- `refresh-jobs`: number of systems queried in parallel during a refresh, default `2`; each query can use several hundred MB of memory

The unit does not need a snapd checkout: the charm keeps a shallow, sparse checkout of only `tests/utils/features` from `snapd-repo-url` at `snapd-ref` in `/var/lib/spread-test-selector/snapd`, created on install and updated on upgrade and config changes and by every refresh, whether triggered through the HTTP API or the `mongodb-updated` action. The `query_features.py` there must match how the data was uploaded; for example, coverage stored in its own collection needs a `query_features.py` that reads it.

The charm installs `git` and `python3-venv` with apt when missing, and creates a virtualenv in `/var/lib/spread-test-selector/venv` with `pymongo` (4.9 or newer, from PyPI), in which the refresh helper and `query_features.py` run. The dashboard packages in the features folder's `requirements.txt` are not installed.

Failures while installing or configuring leave the unit blocked or waiting with the reason; run the `reconcile` action (or change the config) to retry. Upgrading the charm always restarts the API service.

## HTTP API

CI on the same cloud calls the service directly at `http://<unit-address>:<listen-port>` (port `8181` by default). The charm opens the port; depending on the cloud, `juju expose spread-test-selector` (optionally with `--to-cidrs` limited to the CI network) is needed to allow traffic to it. The API has no authentication, so only expose it to networks you trust.

- `GET /health`: returns `{"status": "ok"}`
- `POST /calculate-tests` with `{"changed_files": {"modified": [...], "added": [...], "deleted": [...]}, "backend_systems": [...], "tests": [...]}`: returns `{"tests": [...], "count": N}`, the subset of `tests` to run. Without execution data (before the first refresh, or if a data file is missing) it returns all of `tests`.
- `POST /mongodb-updated`: updates the features checkout and rebuilds the execution data from MongoDB; it answers when the refresh is done (about 30 seconds with 18 systems), so give the request a timeout of at least `refresh-timeout-seconds`

```bash
curl -X POST --max-time 360 http://<unit-address>:8181/mongodb-updated
curl -X POST -d @request.json http://<unit-address>:8181/calculate-tests
```

## How tests are selected

`POST /calculate-tests` applies these rules separately for each `backend:system`. The logic is in `src/service_api.py` and `src/calculate_tests.py`.

**The result is always a subset of the `tests` input.** A test that is not in the input is never returned.

### 1. Ignored files

Changes under `docs/` (the API documentation site) and `.github/` (CI configuration) are dropped. They select nothing and never make everything run.

### 2. Run everything

Every input test runs if any of these is true for any changed file (modified, added or deleted):

1. **There is no execution data**: `spread-execution-map.json` or `base-executions.json` is missing, e.g. before the first refresh.
2. **A non-Go file without a rule of its own changed**: non-Go files have no coverage data, e.g. C code such as snap-confine, `spread.yaml`, `go.mod`, packaging or `data/`.
3. **A file under `tests/` that is not inside a test folder changed**: e.g. shared helpers in `tests/lib/`, tooling, or a file directly in a suite folder.
4. **A modified Go file has no tests in the coverage map** (see 3C).

These non-Go files do not trigger rule 2:

- documentation (`.md`, `.rst`)
- C unit tests (`*-test.c`, `*-test.h`)
- files in unit test folders (`testdata`, `testutil`, `testtime`, or any folder ending in `test`)
- files under `tests/` (rule 3 and 3B handle them)

### 3. Otherwise, these tests are selected

- **A. Base tests**: the system's tests in `base-executions.json`, always. They are chosen on refresh to cover as many features as fit in `base-tests-max-minutes`; features mentioning refresh are always covered.
- **B. Changed tests**: a changed file inside a test folder, `tests/<suite>/<test>/...` for the suites in `SPREAD_SUITES` in `src/service_api.py` (e.g. `tests/main/foo/task.yaml`), selects that test with all its variants. A test that is not in the input (e.g. a deleted test) selects nothing.
- **C. Modified Go files**: the tests that cover that exact file in `spread-execution-map.json`. No tests means everything runs, except for `interfaces/builtin/<name>.go`: there, the tests that connected the interface (`interface-users.json`) are added too, and the file never makes everything run, since many interface files have no coverage of their own. Without `interface-users.json` (or an entry for the system), the normal rule applies.
- **D. Added or deleted Go files**: the tests that cover any file in the same folder or its subfolders. These never make everything run.
- **E. Tests missing from the coverage map**: any input test the map does not know, e.g. a test or variant added since the coverage run, or one that failed in it (failed tests are left out of the map until a run where they pass). New tests therefore always run until the coverage data includes them.

### Go files that do not count

Go unit test files (`_test.go`) and Go files in unit test helper packages (`testutil`, `testtime`, `testdata`, or folders ending in `test`) never appear in spread coverage, so they are skipped in 3C and 3D. The exception is `asserts/assertstest`, which spread tests use through the fake store; it counts like any other Go file.

### In short

Start with the base tests, then add the tests that cover the changed Go files, the tests whose folders changed, and the tests the coverage map does not know. If anything changed that there is no data for, run everything.

## Juju secret format

The charm expects `mongodb-secret-id` to point to a Juju secret whose content contains either:

- a JSON string under one of these keys:
  - `mongodb-credentials-json`
  - `mongodb_credentials_json`
  - `credentials-json`
  - `credentials_json`
  - `json`
- or direct top-level keys named `host`, `port`, `user`, and `password`

The preferred format is a single JSON string, for example:

```json
{
  "host": "mongodb.example.internal",
  "port": 27017,
  "user": "snapd",
  "password": "secret"
}
```

Example secret creation (Juju 3.6). `juju add-secret` prints the secret's URI (`secret:...`); the secret must be granted to the application before the charm can read it:

```bash
juju add-secret spread-test-selector-mongodb mongodb-credentials-json='{"host":"mongodb.example.internal","port":27017,"user":"snapd","password":"secret"}'
juju grant-secret spread-test-selector-mongodb spread-test-selector
juju config spread-test-selector mongodb-secret-id=<secret-uri>
```

## Actions

Actions are defined in [charmcraft.yaml](charmcraft.yaml):

- `calculate-tests`: accepts `backend-systems`, `changed-files-json`, and `tests`, then returns the tests that should be executed
- `mongodb-updated`: rebuilds `spread-execution-map.json`, `interface-users.json` and `base-executions.json`
- `reconcile`: rebuilds charm-managed files and service state
- `service-status`: returns the systemd status for the API service

Example action calls (Juju 3.6; `juju run` waits for the action and prints its results; `--string-args` stops Juju from parsing the JSON value as YAML):

```bash
juju run spread-test-selector/0 calculate-tests --string-args \
  changed-files-json='{"modified":["snap/foo.go"],"added":["interfaces/new.go"],"deleted":[]}' \
  tests='amd64:ubuntu-24.04:tests/spread/foo,arm64:ubuntu-24.04:tests/spread/foo' \
  backend-systems='amd64:ubuntu-24.04,arm64:ubuntu-24.04'

# a refresh takes a while; allow up to refresh-timeout-seconds plus a minute
juju run spread-test-selector/0 mongodb-updated --wait 10m

juju run spread-test-selector/0 reconcile
juju run spread-test-selector/0 service-status
```

## Files on the unit

Default paths:

- `/var/lib/spread-test-selector/mongodb-credentials.json`
- `/var/lib/spread-test-selector/spread-execution-map.json`
- `/var/lib/spread-test-selector/base-executions.json`
- `/var/lib/spread-test-selector/service-config.json`

## Checking the logic against recent snapd commits

`tools/check_recent_commits.py` runs the same `calculate-tests` logic as the API over the most recent first-parent commits of a snapd ref, using `spread -list` at that ref as the allowed tests, and prints how many tests each commit would run and why:

```bash
tools/check_recent_commits.py --snapd ~/source/snapd --data-dir /tmp/tel \
  --credentials ~/mongo-creds.json --commits 20 --fetch --details
```

- `--data-dir` holds `spread-execution-map.json` and `base-executions.json`; they are built from mongodb with the refresh helper when missing (or with `--refresh`), which needs `--credentials` and `pymongo` available to `python3`. Spread lists are cached there per commit.
- `--systems` and `--suites` choose what is listed (default: the openstack systems in the map, `tests/main/`); `--focus` picks the system shown in the table.
- `--show-tests <commit>` prints the selected tests for one commit, and `--json` writes all results.

## Checking a pull request against a running service

`tools/check_pr.py` mimics snapd's CI test selection for one pull request, with and without the service. For each job in the pull request's `.github/workflows/data-fundamental-systems.json` (or the matrices given with `--systems-file`), it:

1. reads the changed files from GitHub with the `gh` CLI, as CI sees them
2. runs snapd's `.github/scripts/spread_tests_to_run.py` (the "Setup run tests variable" step of `spread-tests.yaml`) with the job's backend, systems, tasks and rules, and expands the result with `spread -list` in a temporary worktree of the pull request: the tests CI runs today
3. rewrites the changed files with `tests/utils/features/shared_test_users.py --rewrite` and calls `POST /calculate-tests` once per system with that system's tests: the tests CI would run with the service

```bash
tools/check_pr.py 17856 --snapd ~/source/snapd --service http://<unit-address>:8181
```

Both snapd scripts come from the pull request's checkout, or from the `--snapd` checkout when the pull request does not have them yet. The skip list from `canonical/snapd-testing-skip`, which CI applies afterwards, and the `Skip spread` and `Run all` labels are not mimicked. `--no-shared-users` sends the changed files as they are, `--show-tests` prints the selected tests and `--json` writes all results.

## Notes

- `calculate-tests` requires structured JSON (`changed-files-json`) with `modified`, `added`, and `deleted` keys.
- `calculate-tests` requires an allowed tests list (`tests`) and uses the same selection logic as `src/calculate_tests.py`.
- The API service keeps the parsed `spread-execution-map.json`, `interface-users.json` and `base-executions.json` in memory and reloads a file only when it changes (its modification time or size), so requests do not re-read the large map; the refresh helper replaces the files atomically.
- The MongoDB credentials file is only readable by root.
- A refresh uses the latest timestamp for all three files, so they always come from the same upload, queries each system once for both coverage and interfaces (`refresh-jobs` at a time), and replaces the files only after all queries succeeded.
- Base tests are recalculated on MongoDB refresh using `query_features.py feat cover` with `--max-minutes` taken from charm config. When `base-tests-max-minutes` is above 0, `--force-match-keywords refresh` is also passed, so features mentioning refresh are always covered by the base tests even if that exceeds the time limit.
- `mongodb-updated` first updates the `tests/utils/features` checkout, then runs the helper command, which in turn uses `query_features.py` to query MongoDB. The action waits for up to `refresh-timeout-seconds` plus a minute.
