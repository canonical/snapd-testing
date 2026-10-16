# Snap Store refresh benchmark

This benchmark replays a captured snapd `POST /v2/snaps/refresh` request with
[wrk2](https://github.com/giltene/wrk2). A Python runner reads the YAML profile,
generates a temporary Lua request script, and runs each configured request rate.

wrk2 controls throughput rather than an exact total request count. The profile's
`start`, `end`, and `increment` values are aggregate requests per second. Each
rate runs for `duration`, so a 30-second stage at 10 requests per second aims to
send about 300 requests.

## Prerequisites

- Python 3.10 or newer
- wrk2 available as `wrk2`
- PyYAML and Matplotlib

Install the Python dependency:

```bash
python3 -m venv .venv
.venv/bin/pip install -r benchmarks/snap-store/requirements.txt
```

The snap package installs wrk2 at `/snap/bin/wrk2`. The default profile uses
`wrk2`, but `wrk2.binary` or the command-line `--wrk2` option can select another
path.

## Configure

Edit [profiles/refresh-profile.yaml](profiles/refresh-profile.yaml):

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
  scale_workers_with_rate: true
  timeout: 10s
  pause_seconds: 5

warmup:
  requests: 5
```

`target.url` is the complete endpoint used for DNS, TLS, the HTTP `Host` header,
and the request path. Change it to the complete enterprise Store refresh URL to
benchmark that Store. The captured `Host` and `Content-Length` values are not
replayed; wrk2 derives them from this URL and body.

The refresh request comes from one of two mutually exclusive sources:

- `target.request_file`: a captured snapd request, replayed as-is apart from
  `Host` and `Content-Length`. Treat captures as credentials.
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

With `scale_workers_with_rate: true`, `connections` and `threads` are maximums.
For each stage the runner uses at most one connection and one thread per target
request per second, up to those maximums. The default profile therefore runs
1 RPS with 1 connection and 1 thread, 2 RPS with 2 connections and 2 threads,
and 10 RPS with 10 connections and 2 threads. This keeps low-rate connections
active instead of allowing a fixed pool of ten connections to idle past the
Store's keep-alive timeout. Set the option to `false` to use the configured
connection and thread counts unchanged at every stage.

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
python3 benchmarks/snap-store/latency_benchmark.py profiles/enterprise.yaml --run
```

The runner sends one preflight request by default, requires the configured HTTP
status, and does not follow redirects. Disable it with `preflight.enabled: false`
only when the target has been verified separately.

With `warmup.requests` set, the runner sends that many best-effort requests
between the preflight and the first stage so Store-side caches are warm. Warmup
uses `load.timeout`; failures are logged but never stop the run, since a cold
cache is exactly what the warmup is meant to fix. Downloads warm the resolved
CDN URL the same way.

Only run a benchmark with authorization from the Store operator. Start with a
low rate, watch Store health, and stop the process with `Ctrl+C` if errors or
latency rise unexpectedly.

## Benchmark downloads

The separate [profiles/download-profile.yaml](profiles/download-profile.yaml)
benchmarks the `hello-world` snap on `latest/stable` for amd64. With `--run`,
the runner asks
the Store info API for the channel's current revision, checks that the
advertised size matches `target.expected_size_bytes`, and benchmarks the
resulting download URL. Set `target.url` instead of `target.snap` to benchmark
an explicit download URL, for example a pinned revision. Preview the stage plan
and guarded transfer estimate without making a network request:

```bash
python3 benchmarks/snap-store/download_benchmark.py --dry-run
```

Resolve the Store redirect, verify one complete payload, and run the download
stages against the resulting CDN URL:

```bash
python3 benchmarks/snap-store/download_benchmark.py --run
```

The Store endpoint and final CDN URL are measured separately. `store_headers`
are used only for the Store info lookup and the Store redirect; they are never
forwarded to the CDN. `download_headers` apply to the CDN request. Each
measured response must have both the configured HTTP status and exact body size
to count as a successful download.

The profile calculates the expected payload from response size, duration, and
all configured rates. `safety.estimate_multiplier` covers wrk2 calibration and
scheduling variation. The run is refused when that guarded estimate exceeds
`safety.max_estimated_transfer_mib`. Reduce the load or set `target.byte_range`
to a quoted inclusive range such as `"0-1048575"` for a larger snap. An explicit
`--allow-over-budget` bypass is available for an intentional larger run.

The Store base URL, snap, channel, and architecture are configurable for
production or enterprise Stores. Update `expected_size_bytes`, expected
statuses, and headers to match the selected snap and Store. CDN hosts may vary
by client location and between runs, so the resolved host is included in both
the CSV and graph title.

## Results

Each run creates a UTC timestamped directory under `results/` containing:

- `rate-<rate>.log`: complete wrk2 output for one stage
- `summary.csv`: target and achieved rates, completed requests, HTTP failures,
  socket errors, actual connection and thread counts, exit status, and the
  corresponding log name

Download summaries additionally contain expected-status counts, body-size
failures, successful downloads, validated payload MiB/s, wire MiB/s, and the
resolved CDN host. Download run directories start with `download-`.

## Graph results

Generate graphs for the most recent benchmark run:

```bash
python3 benchmarks/snap-store/graph_results.py
```

Select a specific run directory or its `summary.csv` file:

```bash
python3 benchmarks/snap-store/graph_results.py \
  benchmarks/snap-store/results/20260924T183536534393Z
```

By default, the script creates `benchmark-graphs.png` in the selected run
directory. Use `--output` to choose another PNG path. The image contains:

- p50, p75, p90, and p99 corrected response latency by target request rate
- target versus achieved requests per second
- non-success HTTP responses and socket errors by target request rate

For download summaries, the image adds aggregate validated-payload and wire
throughput. It also graphs unexpected statuses and body sizes alongside socket
errors, and labels rates as downloads per second.

Latency percentiles are read from each `rate-<rate>.log`; throughput and errors
come from `summary.csv`. The latency graph uses a logarithmic scale so runs with
large latency changes remain readable. For new summaries, the bottom axis also
shows the connections and threads used at each rate as `c/t`.

wrk2 treats roughly the first 10 seconds of every stage as a calibration period
and then resets its latency histograms, so a stage's graphed latency covers only
the time after calibration and the discarded samples are unrecoverable. Stages
of 10 seconds or less produce no latency data at all. Keep `load.duration` above
10 seconds (the profiles use 30s) when latency graphs matter; throughput,
request counts, and error counts are measured for the full stage either way.
