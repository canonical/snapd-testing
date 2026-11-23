# Snap Store Benchmarking Tool

This benchmark exercises the snap enterprise and production stores.
Benchmarks aim to stress the system in the following ways:
- Test the latency of the `/v2/snaps/refresh` endpoint by either generating
  or replaying a previously captured refresh request.
- Test download latency and throughput using `wrk` to sweep concurrent downloads.
  Only completed, validated payloads count towards successful-download throughput.

Latency tests generate or replay a snapd `POST /v2/snaps/refresh` request with
[wrk2](https://github.com/giltene/wrk2). A Python runner reads the YAML profile,
generates a temporary Lua request script, and runs each configured request rate.

wrk2 controls throughput rather than an exact total request count. The profile's
`start`, `end`, and `increment` values are aggregate requests per second. Each
rate runs for `duration`, so a 30-second stage at 10 requests per second aims to
send about 300 requests.

## Prerequisites

- Python 3.10 or newer
- PyYAML and Matplotlib
- wrk ```sudo apt install wrk```
- wrk2 ```sudo snap install wrk2```

Install the Python dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r benchmarks/snap-store/requirements.txt
```

## Code layout

The top-level [latency_benchmark.py](latency_benchmark.py),
[download_benchmark.py](download_benchmark.py), and
[graph_results.py](graph_results.py) are the command-line entry points.

- [latency/](latency/):
  - profile loading (`profile.py`)
  - refresh request capture/generation and preflight (`requests.py`)
  - stage execution (`runner.py`)
  - metric validation (`metrics.py`)
  - arguments/dry-run output (`cli.py`)
- [download/](download/):
  - profile loading (`profile.py`)
  - Store/CDN resolution and preflight (`requests.py`)
  - load planning and Lua generation (`load.py`)
  - metric parsing and failure checks (`metrics.py`)
  - sanitized run metadata (`metadata.py`)
  - stage execution (`runner.py`)
  - arguments/dry-run output (`cli.py`)
  - exceptions (`errors.py`)
- [common/](common/):
  - shared validation (`profile.py`)
  - exceptions (`errors.py`)
  - HTTP transport (`http.py`)
  - Store lookups (`store.py`)
  - load-generator execution (`wrk.py`)
  - logs/CSV summaries (`results.py`)
  - graph data loading, rendering, and CLI (`graph_data.py`, `graphs.py`, `graph_cli.py`).

## Configure

Edit [profiles/snapd-refresh-profile.yaml](profiles/snapd-refresh-profile.yaml):

```yaml
target:
  url: https://api.snapcraft.io/v2/snaps/refresh
  # Generate the refresh request for this snap ...
  snap: snapd
  channel: latest/edge
  auth_file: ../device-authorization
  # ... or replay a captured request instead:
  # request_file: ../request

load:
  requests_per_second:
    start: 1
    end: 10
    increment: 1
  duration: 30s
  connections: 10
  threads: 2
  timeout: 10s
  pause_seconds: 5

warmup:
  requests: 5
```

`target.url` is the complete endpoint used for DNS, TLS, the HTTP `Host` header,
and the request path. Change it to the complete enterprise Store refresh URL to
benchmark that Store. The captured `Host` and `Content-Length` values are not
replayed; wrk2 derives them from the URL and body.

The refresh request comes from one of two mutually exclusive sources:

- `target.request_file`: a captured snapd request, replayed as-is apart from
  `Host` and `Content-Length`.
- `target.snap`: generate the request for the named snap on `target.channel`
  (default `latest/stable`) and `target.architecture` (default `amd64`). At run
  time the runner resolves the snap-id and the channel's current revision
  through the Store info API; set `target.revision` to pretend an older
  revision is installed and measure the "refresh available" response instead
  of "no refresh needed". `target.store_url` defaults to the origin of
  `target.url`. Device authorization is read from `target.auth_file` (default
  `device-authorization` next to the profile), a file containing a single
  `Macaroon ...` header value copied from a capture or a device's snapd state.

The generated Lua file is temporary and result files do not include request
header values or the body.

The rate sequence is inclusive. `start: 10`, `end: 50`, and `increment: 10`
runs stages at 10, 20, 30, 40, and 50 requests per second. `end` must be exactly
reachable from `start` using `increment`.

Rate sweeps keep `connections` and `threads` fixed; only requests per second
changes. The default profile uses 10 connections and 2 threads at every rate,
including odd rates such as 1, 3, and 5 RPS. `threads` must divide `connections`
evenly: two threads require even connections, while four require multiples of
four. The runner never silently reduces the thread count.

These rules also apply to rate-based downloads. Concurrency-mode downloads
sweep connection counts while keeping threads fixed.

Choose a fixed pool appropriate for the entire sweep. A large pool at low RPS
can leave connections idle beyond the Store's keep-alive timeout; fixed workers
improve comparability but do not eliminate that effect.

## Run

Preview the parsed profile and generated wrk2 commands without making a network
request:

```bash
python3 benchmarks/snap-store/latency_benchmark.py --dry-run
```

Run the preflight request and all load stages:

```bash
python3 benchmarks/snap-store/latency_benchmark.py --run
```

Pass a different profile as the positional argument:

```bash
python3 benchmarks/snap-store/latency_benchmark.py \
  benchmarks/snap-store/profiles/enterprise-gimp-refresh-profile.yaml --run
```

For the local Store at `https://snapd.enterprise-test.com`, use
[profiles/local-enterprise-gimp-refresh-profile.yaml](profiles/local-enterprise-gimp-refresh-profile.yaml):

```bash
python3 benchmarks/snap-store/latency_benchmark.py \
  benchmarks/snap-store/profiles/local-enterprise-gimp-refresh-profile.yaml --dry-run
```

Replace `--dry-run` with `--run` to execute. The profile uses 20 connections,
4 threads, and a 10–100 RPS sweep. Both the info lookup and refresh requests
target the local Store. Ensure its hostname resolves on the benchmark machine,
its TLS certificate is trusted, and `target.auth_file` contains device
authorization accepted by that Store. For an isolated test Store with an
untrusted certificate, `target.verify_tls: false` disables Python setup-request
certificate verification; trusting the test CA is preferable.

The runner sends one preflight request by default, requires the configured HTTP
status, and does not follow redirects. Disable it with `preflight.enabled: false`
only when the target has been verified separately.

Measured latency stages also check every response against
`preflight.expected_status` (default 200), even when preflight is disabled.
An unexpected status (including a redirect), socket error, zero successful
requests, or missing/inconsistent metrics stops the sweep after saving the
stage log and summary. The runner exits with status 1 without increasing load.
This is an end-of-stage check, not live monitoring.

With `warmup.requests` set, the runner sends that many best-effort requests
before measuring load stages. Latency warmup runs before the preflight check
and is skipped when preflight is disabled. Download warmup runs after preflight
against the resolved download URL. Warmup uses `load.timeout`; failures are
logged but do not stop the run.

Only run a benchmark with authorization from the Store operator. Start with a
low rate, watch Store health, and stop the process with `Ctrl+C` if errors or
latency rise unexpectedly.

## Benchmark downloads

All bundled download profiles use standard `wrk`, not a requests-per-second target.
The default [hello-world profile](profiles/helloworld-download-profile.yaml)
runs four 30-second stages at 2, 4, 8 and 16 concurrent downloads. With `--run`,
the runner asks
the Store info API for the channel's current revision, checks that the
advertised size matches `target.expected_size_bytes`, and benchmarks the
resulting download URL. Set `target.url` instead of `target.snap` to benchmark
an explicit download URL, for example a pinned revision. Preview the stage plan
and transfer estimate (unknown for concurrency sweeps) without making a network
request:

```bash
python3 benchmarks/snap-store/download_benchmark.py --dry-run
```

Resolve the Store redirect, verify one complete payload, and run the download
stages against the resulting CDN URL:

```bash
python3 benchmarks/snap-store/download_benchmark.py --run --allow-over-budget
```

Store lookup and redirect resolution happen during setup; download load stages
target the resolved URL. This may be a CDN or an enterprise Store serving the
blob directly. `store_headers` are used for the Store info lookup and initial
download-URL request; they are not forwarded when following redirects.
`download_headers` apply to the resolved download requests. Each measured
response must have both the configured HTTP status and exact body size to count
as a successful download.

Concurrency has unknown transfer volume, so `--allow-over-budget` is required;
it does not enforce a runtime byte cap. Rate-based download profiles use wrk2
for fixed-arrival-rate tests. Their estimated volume is
checked against `safety.max_estimated_transfer_mib` unless explicitly overridden.

The Store base URL, snap, channel, and architecture are configurable for
production or enterprise Stores. Update `expected_size_bytes`, expected
statuses, and headers to match the selected snap and Store. CDN hosts may vary
by client location and between runs, so the resolved host is included in both
the CSV and graph title.

If the Store advertises a different size, the runner stops before downloading
the payload or starting load stages. Review the selected snap/channel and
update `target.expected_size_bytes` explicitly; `--allow-over-budget` does not
bypass this check. Update production and enterprise profiles independently.
A size match does not pin a revision or prove identical content; compare the
resolved revisions recorded in run metadata when comparing results.

### Full-snap GIMP connection sweep

[profiles/gimp-download-profile.yaml](profiles/gimp-download-profile.yaml)
requests the entire 147,804,160-byte snap with no `Range` header and expects
HTTP 200. It runs four five-minute stages at 2, 4, 8, and 16 concurrent
downloads using standard **wrk**, with two threads and no rate target.
Each connection starts its next request as soon as the previous response completes.
The [enterprise GIMP profile](profiles/enterprise-gimp-download-profile.yaml)
uses the same load settings but has an independently expected
147,734,528-byte payload. Align revisions and sizes before treating the runs
as identical-payload comparisons. Allow about **20 minutes per run**, plus preflight,
warmup and pauses. The timeout setting is five minutes.

For `https://snapd.enterprise-test.com`, use the
[local enterprise download profile](profiles/local-enterprise-gimp-download-profile.yaml):

```bash
python3 benchmarks/snap-store/download_benchmark.py \
  benchmarks/snap-store/profiles/local-enterprise-gimp-download-profile.yaml \
  --run --allow-over-budget
```

It uses the same load settings and expected size as the production profile.
The local Store's advertised size must match before load starts, and its
certificate must be trusted by the benchmark machine.

```yaml
load:
  concurrent_downloads: [2, 4, 8, 16]
  duration: 5m
  threads: 2
  timeout: 5m
  pause_seconds: 5

wrk:
  binary: wrk
```

`concurrent_downloads` must be a non-empty, strictly increasing list of positive
integers, each divisible by `threads` so wrk opens the requested connection count.
Use either `concurrent_downloads` or `downloads_per_second`, not both; configure
`connections` only for rate-based tests.
Rate profiles use wrk2 with a fixed worker pool. Use `--wrk /path/to/wrk`
to override the concurrency executable, or `--wrk2` for rate profiles. A wrk2
binary is not a substitute for standard wrk: wrk2 requires a rate target.

Preview this profile explicitly (the default is `hello-world`):

```bash
python3 benchmarks/snap-store/download_benchmark.py \
  benchmarks/snap-store/profiles/gimp-download-profile.yaml --dry-run
```

Concurrency alone cannot predict a download count or total transfer volume.
The dry run reports the estimate as **unknown**, not zero, and this mode always
requires explicit `--allow-over-budget`. Neither this flag nor the estimate
settings enforce a runtime transfer cap. Obtain operator authorization before running:

```bash
python3 benchmarks/snap-store/download_benchmark.py \
  benchmarks/snap-store/profiles/gimp-download-profile.yaml \
  --run --allow-over-budget
```

Achieved downloads per second counts only completed responses with the expected
status and exact payload size. Range-profile rates
count completed ranges, not whole snaps. Both wrk and wrk2 stop at the stage deadline rather
than draining active transfers: unfinished downloads do not count as successful,
although their bytes contribute to the separately labelled bytes-read throughput.
Cancelled transfers do not supply a completed-response latency sample. They are
not counted as successful downloads or added to completed-payload throughput.
Longer stages reduce this boundary effect but cannot eliminate it.

`duration` controls when the stage stops. `timeout` does not extend it: standard
wrk uses that value as its latency-recording limit, and the runner also uses it
for preflight/warmup socket timeouts. Neither setting drains active transfers.
The concurrency run measures warm repeated downloads, not cold-cache behaviour
or installation time. Monitor client CPU/memory and network use: validating
full response bodies in Lua adds load to the benchmark client.

## Results

### Download safeguards

Downloads stop after any stage with an unexpected HTTP status, wrong body size,
socket error, or zero successful downloads. The stage log and summary are saved
first; the runner exits with status 1 rather than increasing load. This is a
**between-stage check**, not a live abort or a transfer cap.

Add `--confirm-stages` to a download run to approve each higher-load stage.
Only `y` or `yes` continues; empty input, other answers or EOF stop the run with
status 1 and keep the completed results. No prompt is shown without the flag.

Each download run also writes owner-readable `metadata.json`: sanitized profile,
resolved revision and size, destination origin, client hostname/OS/architecture,
Python/PyYAML/load-generator versions, source fingerprint and run outcome.
Header contents, URL paths/query strings and local profile/output paths are
omitted. Explicit download URLs have an unknown revision, not an inferred one.
Runs remain marked `incomplete` if interrupted before recording a final outcome.
Treat client identity as internal information when sharing results.

Each run creates a UTC timestamped directory under `results/` containing:

- `rate-<rate>.log`: complete wrk2 output for one stage
- `connections-<count>.log`: standard wrk output for a concurrency stage
- `summary.csv`: target and achieved rates, completed requests, HTTP failures,
  socket errors, actual connection and thread counts, exit status, and the
  corresponding log name

Latency summaries additionally record `expected_status_responses`,
`unexpected_http_responses`, `stage_status`, and `failure_reason`. The generator's
`exit_code` is preserved even if its successful exit produced a failed stage.
Latency throughput and percentiles describe all completed responses, not just
successful ones; the error panel and `(failed)` stage labels must be read
alongside them. Latency graphs count all unexpected statuses, including
3xx responses.
Missing metrics remain unavailable rather than being replaced with zero;
an incomplete summary may therefore be rejected by the graph loader.

Download summaries additionally contain expected-status counts, body-size
failures, successful downloads, validated payload MiB/s, bytes-read MiB/s, and the
resolved CDN host. Download run directories start with `download-`.
Download summaries include `load_mode` (`rate` or `concurrency`). Concurrency
rows leave `requests_per_second_target` empty and record the sweep value in
`connections`; achieved DPS remains a measured result, not a target.

For download summaries, `requests_per_second_achieved` is the successful download
count divided by elapsed time, excluding HTTP and body-size failures.
`requests_completed` retains the load generator's raw completed-response count, and the logs
retain its raw `Requests/sec` value.

Run the offline benchmark regression tests (including localhost integration
when wrk and/or wrk2 are installed) with:

```bash
python3 -m unittest discover -s benchmarks/snap-store -p 'test_*.py'
```

## Graph results

Generate graphs for the most recent benchmark run:

```bash
python3 benchmarks/snap-store/graph_results.py
```

Select a specific run directory or its `summary.csv` file:

```bash
python3 benchmarks/snap-store/graph_results.py \
  /path/to/run/summary.csv
```

By default, the script creates `benchmark-graphs.png` in the selected run
directory. Use `--output` to choose another PNG path. The image contains:

- p50, p75, p90, and p99 corrected response latency by target request rate
- target versus achieved requests per second
- non-success HTTP responses and socket errors by target request rate

For download summaries, the image adds aggregate validated-payload and bytes-read
throughput. It also graphs unexpected statuses and body sizes alongside socket
errors, and labels rates as downloads per second.
Concurrency runs instead use concurrent downloads on the x-axis and show
successful completed downloads per second without a target-rate line. Their
latency is **wrk-reported, closed-loop latency**, not wrk2's
schedule-corrected latency. Standard wrk applies its own histogram correction;
do not compare the two as equivalent measurements.
Stages without positive latency samples are explicitly marked, while throughput
and errors are still graphed.

Latency percentiles are read from each stage log; throughput and errors
come from `summary.csv`. The latency graph uses a logarithmic scale so runs with
large latency changes remain readable. The bottom axis also
shows the connections and threads used at each rate as `c/t`.

Use stages long enough to collect meaningful latency samples, especially at
low request rates. Short runs can produce percentiles, but a handful of samples
does not provide a reliable p99 estimate. Calibration and histogram sampling
depend on the load generator and version; inspect its logs rather than assuming
the latency histogram covers the same interval as whole-stage throughput.
