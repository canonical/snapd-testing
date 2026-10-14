#!/usr/bin/env python3
"""Shared helpers for the wrk2-based snap Store benchmark runners."""

from __future__ import annotations

import csv
import json
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse


WRK_TIME_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[smh]$")
SNAP_NAME_RE = re.compile(r"^[a-z0-9](?:-?[a-z0-9])*$")
RISKS = ("stable", "candidate", "beta", "edge")

_TIME_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600}


class BenchmarkError(Exception):
    """Base class for benchmark setup and execution errors."""


class ProfileError(BenchmarkError):
    """Raised when a benchmark profile is invalid."""


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{name} must be a mapping")
    return value


def require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{name} must be a non-empty string")
    return value.strip()


def require_integer(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(
            f"{name} must be an integer greater than or equal to {minimum}"
        )
    return value


def require_number(value: Any, name: str, *, minimum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < minimum
    ):
        raise ProfileError(
            f"{name} must be a number greater than or equal to {minimum:g}"
        )
    return float(value)


def require_wrk_time(value: Any, name: str) -> str:
    value = require_string(value, name)
    if not WRK_TIME_RE.fullmatch(value) or float(value[:-1]) <= 0:
        raise ProfileError(
            f"{name} must be a positive wrk2 duration such as 30s or 2m"
        )
    return value


def require_url(value: Any, name: str) -> str:
    url = require_string(value, name)
    parsed = urlparse(url)
    try:
        parsed.port
    except ValueError as error:
        raise ProfileError(f"{name} has an invalid port: {error}") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProfileError(f"{name} must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ProfileError(f"{name} must not contain credentials")
    if parsed.fragment:
        raise ProfileError(f"{name} must not contain a fragment")
    return url


def require_snap_name(value: Any, name: str) -> str:
    snap_name = require_string(value, name)
    if not SNAP_NAME_RE.fullmatch(snap_name):
        raise ProfileError(f"{name} is not a valid snap name: {snap_name!r}")
    return snap_name


def parse_channel(value: str) -> tuple[str, str, str | None]:
    """Split a channel name into track, risk, and optional branch."""
    parts = value.split("/")
    if len(parts) == 1:
        parts.insert(0, "latest")
    if len(parts) not in {2, 3} or not all(parts):
        raise ProfileError(f"target.channel is not a valid channel: {value!r}")
    track, risk, *branch = parts
    if risk not in RISKS:
        raise ProfileError(
            f"target.channel risk must be one of {', '.join(RISKS)}: {value!r}"
        )
    return track, risk, branch[0] if branch else None


def resolve_profile_path(profile_file: Path, value: Any, name: str) -> Path:
    """Resolve a profile path entry relative to the profile's directory."""
    path = Path(require_string(value, name)).expanduser()
    if not path.is_absolute():
        path = profile_file.parent / path
    return path.resolve()


def build_rates(start: int, end: int, increment: int) -> tuple[int, ...]:
    """Build an inclusive, evenly spaced request-rate sequence."""
    if start <= 0:
        raise ProfileError("load.requests_per_second.start must be greater than zero")
    if end < start:
        raise ProfileError(
            "load.requests_per_second.end must be greater than or equal to start"
        )
    if increment <= 0:
        raise ProfileError(
            "load.requests_per_second.increment must be greater than zero"
        )
    if (end - start) % increment != 0:
        raise ProfileError(
            "load.requests_per_second.end must be reachable from start using increment"
        )
    return tuple(range(start, end + 1, increment))


def wrk_time_seconds(value: str) -> float:
    """Convert a wrk2 duration such as 30s or 2m into seconds."""
    return float(value[:-1]) * _TIME_MULTIPLIERS[value[-1]]


def lua_long_string(value: str) -> str:
    """Encode text as a Lua long string without modifying its contents."""
    equals = ""
    while f"]{equals}]" in value:
        equals += "="
    return f"[{equals}[{value}]{equals}]"


class WorkerLimits(Protocol):
    """Profile attributes that control per-stage worker counts."""

    connections: int
    threads: int
    scale_workers_with_rate: bool


def worker_counts_for_rate(profile: WorkerLimits, rate: int) -> tuple[int, int]:
    """Return the connections and threads used for one request-rate stage."""
    if not profile.scale_workers_with_rate:
        return profile.connections, profile.threads

    connections = min(profile.connections, rate)
    threads = min(profile.threads, connections, rate)
    return connections, threads


def wrk2_command(
    *,
    binary: str,
    script_file: Path,
    url: str,
    rate: int,
    duration: str,
    timeout: str,
    connections: int,
    threads: int,
) -> list[str]:
    """Build one wrk2 stage command."""
    return [
        binary,
        "--threads",
        str(threads),
        "--connections",
        str(connections),
        "--duration",
        duration,
        "--timeout",
        timeout,
        "--rate",
        str(rate),
        "--latency",
        "--script",
        str(script_file),
        url,
    ]


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects during preflight checks."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def http_get(
    url: str, headers: Sequence[tuple[str, str]], timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    """Send one GET request without following redirects."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except urllib.error.URLError as error:
        raise BenchmarkError(f"request failed: {error.reason}") from error
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error

    try:
        return response.status, response.headers, response.read()
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error
    finally:
        response.close()


def fetch_store_info(
    store_url: str,
    snap_name: str,
    headers: Sequence[tuple[str, str]],
    timeout: float,
) -> Mapping[str, Any]:
    """Fetch and parse the Store info document for a snap."""
    status, _, body = http_get(
        f"{store_url}/v2/snaps/info/{snap_name}", headers, timeout
    )
    if status != 200:
        raise BenchmarkError(
            f"Store info request for {snap_name} returned HTTP {status}"
        )
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise BenchmarkError(
            f"Store info response is not valid JSON: {error}"
        ) from error
    if not isinstance(document, Mapping):
        raise BenchmarkError("Store info response is not a JSON object")
    return document


def snap_id_from_info(info: Mapping[str, Any]) -> str:
    """Extract the snap-id from a Store info document."""
    snap_id = info.get("snap-id")
    snap = info.get("snap")
    if not isinstance(snap_id, str) and isinstance(snap, Mapping):
        snap_id = snap.get("snap-id") or snap.get("id")
    if not isinstance(snap_id, str) or not snap_id:
        raise BenchmarkError("Store info response has no snap-id")
    return snap_id


def find_channel_map_entry(
    info: Mapping[str, Any], snap_name: str, channel: str, architecture: str
) -> Mapping[str, Any]:
    """Return the channel-map entry matching channel and architecture."""
    channel_map = info.get("channel-map")
    if not isinstance(channel_map, list):
        raise BenchmarkError("Store info response has no channel map")
    track, risk, branch = parse_channel(channel)
    matches = []
    available = []
    for entry in channel_map:
        entry_channel = entry.get("channel")
        if not isinstance(entry_channel, Mapping):
            continue
        available.append(
            f"{entry_channel.get('name', '?')} ({entry_channel.get('architecture', '?')})"
        )
        if (
            entry_channel.get("track", "latest") == track
            and entry_channel.get("risk") == risk
            and entry_channel.get("branch") == branch
            and entry_channel.get("architecture") == architecture
        ):
            matches.append(entry)
    if not matches:
        raise BenchmarkError(
            f"no {channel} revision for {snap_name} on {architecture}; "
            f"available: {', '.join(sorted(available))}"
        )
    return matches[0]


def require_wrk2(binary: str) -> str:
    """Resolve the wrk2 executable or fail with a clear error."""
    executable = shutil.which(binary)
    if executable is None:
        raise BenchmarkError(f"wrk2 executable not found: {binary}")
    return executable


def run_wrk2(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one wrk2 stage, capturing its combined output."""
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        raise BenchmarkError(f"failed to start wrk2: {error}") from error


def create_run_directory(output_directory: Path, name_prefix: str = "") -> Path:
    """Create a UTC timestamped results directory for one benchmark run."""
    output_directory.mkdir(parents=True, exist_ok=True)
    run_name = datetime.now(timezone.utc).strftime(f"{name_prefix}%Y_%m_%d_T%H_%M_%SZ")
    run_directory = output_directory / run_name
    run_directory.mkdir()
    return run_directory


def write_lua_script(directory: Path, name: str, content: str) -> Path:
    """Write the generated wrk2 script with owner-only permissions."""
    script_file = directory / name
    script_file.write_text(content, encoding="utf-8")
    script_file.chmod(0o600)
    return script_file


def write_stage_log(
    run_directory: Path, rate: int, command: Sequence[str], output: str
) -> Path:
    """Record the command and output of one stage."""
    log_file = run_directory / f"rate-{rate}.log"
    log_file.write_text(f"$ {shlex.join(command)}\n\n{output}", encoding="utf-8")
    return log_file


def pause_between_stages(index: int, stage_count: int, pause_seconds: float) -> None:
    """Pause between stages so the target can recover."""
    if index < stage_count - 1 and pause_seconds:
        print(f"Pausing {pause_seconds:g} seconds before the next rate.")
        time.sleep(pause_seconds)


def warmup_request_count(root: Mapping[str, Any]) -> int:
    """Return the configured warmup request count; 0 disables warmup."""
    warmup = require_mapping(root.get("warmup", {}), "warmup")
    return require_integer(warmup.get("requests", 0), "warmup.requests", minimum=0)


def run_warmup(count: int, send: Callable[[], tuple[int, int]]) -> None:
    """Send best-effort requests to warm server-side caches before measuring.

    Failures are reported but never stop the benchmark: a cold cache is
    exactly what the warmup is trying to fix.
    """
    for index in range(1, count + 1):
        start = time.monotonic()
        try:
            status, size = send()
        except BenchmarkError as error:
            print(f"Warmup {index}/{count}: {error}")
            continue
        elapsed = time.monotonic() - start
        print(
            f"Warmup {index}/{count}: HTTP {status}, "
            f"{size:,} bytes in {elapsed:.2f}s"
        )


def parse_wrk2_metrics(output: str) -> dict[str, int | float | str]:
    """Extract stable summary fields from wrk2 output."""
    metrics: dict[str, int | float | str] = {
        "requests_completed": "",
        "requests_per_second_achieved": "",
        "non_2xx_or_3xx": 0,
        "socket_connect_errors": 0,
        "socket_read_errors": 0,
        "socket_write_errors": 0,
        "socket_timeout_errors": 0,
    }

    total_match = re.search(r"(?m)^\s*([0-9]+) requests in ", output)
    if total_match:
        metrics["requests_completed"] = int(total_match.group(1))

    rate_match = re.search(r"(?m)^Requests/sec:\s+([0-9]+(?:\.[0-9]+)?)", output)
    if rate_match:
        metrics["requests_per_second_achieved"] = float(rate_match.group(1))

    status_match = re.search(
        r"(?m)^\s*Non-2xx or 3xx responses:\s+([0-9]+)", output
    )
    if status_match:
        metrics["non_2xx_or_3xx"] = int(status_match.group(1))

    socket_match = re.search(
        r"(?m)^\s*Socket errors: connect ([0-9]+), read ([0-9]+), "
        r"write ([0-9]+), timeout ([0-9]+)",
        output,
    )
    if socket_match:
        (
            metrics["socket_connect_errors"],
            metrics["socket_read_errors"],
            metrics["socket_write_errors"],
            metrics["socket_timeout_errors"],
        ) = (int(value) for value in socket_match.groups())

    return metrics


def write_summary(
    summary_file: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Rewrite the run summary so it is current after every stage."""
    with summary_file.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
