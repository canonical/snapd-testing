# Store Load

`store-load` is the policy and orchestration layer for controlled Snap Store
load tests. It accepts sanitized workload manifests, combines them with a
versioned load profile and an allowlisted target, and produces a bounded
execution plan.

The application currently provides the core before a load engine is attached:

- strict JSON/TOML input validation;
- structural workload-template rendering;
- absolute and target-specific load limits;
- protected-target confirmation;
- private, per-client credential-pool validation;
- proxy and optional network preflight checks; and
- deterministic, secret-free execution-plan output.

It does not generate load yet. A Locust adapter will consume the compiled plan
in the next implementation slice. GitHub Actions should eventually call this
CLI rather than duplicate any validation, safety, or orchestration logic.

## Boundaries

Raw captures and authorization material are outside this repository. A
sanitizer must emit a manifest matching
[`schemas/workload-v1.schema.json`](schemas/workload-v1.schema.json). Runtime
credentials are loaded from a mode `0600` JSON file and are never included in
plan or preflight output.

Request templates can reference client variables with `${variable}`. A token
that occupies an entire JSON string preserves the variable's JSON type; an
embedded token must resolve to a scalar. Authorization is selected with the
request's `auth` field and is never placed in manifest headers.

The target catalog is the only place that defines destination URLs. Manifest
paths must be relative, and both their original and rendered forms are checked
before use.

## Development

Python 3.11 or newer is required. The core has no third-party runtime
dependencies.

```bash
cd store-load
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

For source-tree execution without installation:

```bash
PYTHONPATH=src python3 -m store_load validate \
  --manifest examples/workload.json \
  --targets config/targets.example.toml \
  --profile config/profiles/steady-fleet.toml
```

Compile and inspect a plan without network activity:

```bash
PYTHONPATH=src python3 -m store_load plan \
  --manifest examples/workload.json \
  --targets config/targets.example.toml \
  --target enterprise \
  --profile config/profiles/steady-fleet.toml
```

Run all offline preflight checks with a credential file:

```bash
chmod 600 "$STORE_LOAD_CREDENTIALS_FILE"
PYTHONPATH=src python3 -m store_load preflight \
  --manifest examples/workload.json \
  --targets config/targets.example.toml \
  --target enterprise \
  --profile config/profiles/steady-fleet.toml \
  --skip-network
```

Omit `--skip-network` to send one unauthenticated `HEAD` request to the target's
configured probe path. Protected targets then require an exact confirmation,
for example `--confirm-target enterprise`. Redirects are not followed.

## Credential File

Credential files are runtime secrets and must not be committed. The version 1
shape is:

```json
{
  "version": 1,
  "credentials": [
    {
      "id": "device-0001",
      "device_authorization": "Macaroon ...",
      "user_authorization": null
    }
  ]
}
```

Every authenticated virtual client requires a distinct entry. An `auth` value
of `device` requires `device_authorization`; `user` requires both authorization
values.

## CLI Exit Codes

- `0`: validation or preflight succeeded;
- `1`: preflight completed but one or more checks failed;
- `2`: configuration or safety policy rejected the command.
