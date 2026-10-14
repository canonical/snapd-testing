#!/usr/bin/env python3
"""Run staged wrk2 benchmarks using a captured snap refresh request."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from wrk2_common import (
    BenchmarkError,
    NoRedirectHandler,
    ProfileError,
    build_rates,
    create_run_directory,
    fetch_store_info,
    find_channel_map_entry,
    lua_long_string,
    parse_channel,
    parse_wrk2_metrics,
    pause_between_stages,
    require_integer,
    require_mapping,
    require_number,
    require_snap_name,
    require_string,
    require_url,
    require_wrk_time,
    require_wrk2,
    resolve_profile_path,
    run_warmup,
    run_wrk2,
    snap_id_from_info,
    warmup_request_count,
    worker_counts_for_rate,
    write_lua_script,
    write_stage_log,
    write_summary,
    wrk_time_seconds,
    wrk2_command,
)


DEFAULT_PROFILE = Path(__file__).parent / "profiles" / "snapd-refresh-profile.yaml"
MANAGED_HEADERS = {"connection", "content-length", "host", "transfer-encoding"}
HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
HTTP_VERSION_RE = re.compile(r"^HTTP/[0-9]+\.[0-9]+$")

SNAPD_USER_AGENT = "snapd/2.76.3 (series 16) ubuntu-core/26 (amd64)"
REFRESH_FIELDS = (
    "architectures",
    "base",
    "confinement",
    "links",
    "contact",
    "created-at",
    "description",
    "download",
    "epoch",
    "license",
    "name",
    "prices",
    "private",
    "publisher",
    "revision",
    "snap-id",
    "snap-yaml",
    "summary",
    "title",
    "type",
    "version",
    "website",
    "store-url",
    "integrity",
    "media",
    "common-ids",
    "categories",
)


class RequestCaptureError(BenchmarkError):
    """Raised when a captured HTTP request cannot be replayed."""


@dataclass(frozen=True)
class CapturedRequest:
    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: str

    @property
    def replay_headers(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, value)
            for name, value in self.headers
            if name.lower() not in MANAGED_HEADERS
        )


@dataclass(frozen=True)
class BenchmarkProfile:
    profile_file: Path
    url: str
    request_file: Path | None
    snap_name: str | None
    channel: str
    architecture: str
    revision: int | None
    store_url: str
    auth_file: Path | None
    rates: tuple[int, ...]
    duration: str
    connections: int
    threads: int
    scale_workers_with_rate: bool
    timeout: str
    pause_seconds: float
    preflight_enabled: bool
    expected_status: int
    warmup_requests: int
    output_directory: Path
    wrk2_binary: str


def load_profile(profile_file: Path) -> BenchmarkProfile:
    """Load and validate a benchmark profile."""
    profile_file = profile_file.expanduser().resolve()
    try:
        with profile_file.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except OSError as error:
        raise ProfileError(f"cannot read profile {profile_file}: {error}") from error
    except yaml.YAMLError as error:
        raise ProfileError(f"invalid YAML in {profile_file}: {error}") from error

    root = require_mapping(document, "profile")
    target = require_mapping(root.get("target"), "target")
    load = require_mapping(root.get("load"), "load")
    rate = require_mapping(
        load.get("requests_per_second"), "load.requests_per_second"
    )
    preflight = require_mapping(root.get("preflight", {}), "preflight")
    output = require_mapping(root.get("output", {}), "output")
    wrk2 = require_mapping(root.get("wrk2", {}), "wrk2")

    url = require_url(target.get("url"), "target.url")

    request_file_value = target.get("request_file")
    snap_value = target.get("snap")
    if request_file_value is not None and snap_value is not None:
        raise ProfileError("target must set either request_file or snap, not both")
    if request_file_value is None and snap_value is None:
        raise ProfileError("target must set request_file or snap")

    request_file: Path | None = None
    snap_name: str | None = None
    channel = "latest/stable"
    architecture = "amd64"
    revision: int | None = None
    store_url = ""
    auth_file: Path | None = None
    if request_file_value is not None:
        request_file = resolve_profile_path(
            profile_file, request_file_value, "target.request_file"
        )
        if not request_file.is_file():
            raise ProfileError(f"target.request_file does not exist: {request_file}")
    else:
        snap_name = require_snap_name(snap_value, "target.snap")
        track, risk, branch = parse_channel(
            require_string(target.get("channel", "latest/stable"), "target.channel")
        )
        channel = "/".join(part for part in (track, risk, branch) if part)
        architecture = require_string(
            target.get("architecture", "amd64"), "target.architecture"
        )
        revision_value = target.get("revision")
        if revision_value is not None:
            revision = require_integer(revision_value, "target.revision")
        parsed_url = urlparse(url)
        store_url = require_url(
            target.get("store_url", f"{parsed_url.scheme}://{parsed_url.netloc}"),
            "target.store_url",
        )
        auth_file = resolve_profile_path(
            profile_file,
            target.get("auth_file", "./device-authorization"),
            "target.auth_file",
        )

    rates = build_rates(
        require_integer(rate.get("start"), "load.requests_per_second.start"),
        require_integer(rate.get("end"), "load.requests_per_second.end"),
        require_integer(
            rate.get("increment"), "load.requests_per_second.increment"
        ),
    )

    connections = require_integer(load.get("connections"), "load.connections")
    threads = require_integer(load.get("threads"), "load.threads")
    if threads > connections:
        raise ProfileError("load.threads must not be greater than load.connections")

    scale_workers_with_rate = load.get("scale_workers_with_rate", False)
    if not isinstance(scale_workers_with_rate, bool):
        raise ProfileError("load.scale_workers_with_rate must be true or false")

    pause_seconds = require_number(
        load.get("pause_seconds", 0), "load.pause_seconds", minimum=0
    )

    preflight_enabled = preflight.get("enabled", True)
    if not isinstance(preflight_enabled, bool):
        raise ProfileError("preflight.enabled must be true or false")

    return BenchmarkProfile(
        profile_file=profile_file,
        url=url,
        request_file=request_file,
        snap_name=snap_name,
        channel=channel,
        architecture=architecture,
        revision=revision,
        store_url=store_url,
        auth_file=auth_file,
        rates=rates,
        duration=require_wrk_time(load.get("duration"), "load.duration"),
        connections=connections,
        threads=threads,
        scale_workers_with_rate=scale_workers_with_rate,
        timeout=require_wrk_time(load.get("timeout", "10s"), "load.timeout"),
        pause_seconds=pause_seconds,
        preflight_enabled=preflight_enabled,
        expected_status=require_integer(
            preflight.get("expected_status", 200),
            "preflight.expected_status",
            minimum=100,
        ),
        warmup_requests=warmup_request_count(root),
        output_directory=resolve_profile_path(
            profile_file, output.get("directory", "results"), "output.directory"
        ),
        wrk2_binary=require_string(wrk2.get("binary", "wrk2"), "wrk2.binary"),
    )


def parse_request_capture(request_file: Path) -> CapturedRequest:
    """Parse a raw HTTP request captured in a text file."""
    try:
        with request_file.open("r", encoding="utf-8", newline=None) as stream:
            raw_request = stream.read()
    except OSError as error:
        raise RequestCaptureError(
            f"cannot read request capture {request_file}: {error}"
        ) from error

    header_block, separator, body = raw_request.partition("\n\n")
    if not separator:
        raise RequestCaptureError("request capture has no blank line before its body")

    header_lines = header_block.splitlines()
    if not header_lines:
        raise RequestCaptureError("request capture is empty")

    request_line = header_lines[0].split()
    header_start = 1
    if (
        len(request_line) == 2
        and len(header_lines) > 1
        and HTTP_VERSION_RE.fullmatch(header_lines[1])
    ):
        request_line.append(header_lines[1])
        header_start = 2
    if len(request_line) not in {2, 3}:
        raise RequestCaptureError(
            "request line must contain METHOD PATH and optional HTTP version"
        )
    method, path = request_line[:2]
    if not HTTP_TOKEN_RE.fullmatch(method):
        raise RequestCaptureError(f"invalid HTTP method in request capture: {method!r}")
    if not path.startswith("/"):
        raise RequestCaptureError("captured request path must start with /")
    if len(request_line) == 3 and not HTTP_VERSION_RE.fullmatch(request_line[2]):
        raise RequestCaptureError(
            f"invalid HTTP version in request capture: {request_line[2]!r}"
        )

    headers: list[tuple[str, str]] = []
    seen_headers: set[str] = set()
    for line in header_lines[header_start:]:
        name, separator, value = line.partition(":")
        name = name.strip()
        if not separator or not HTTP_TOKEN_RE.fullmatch(name):
            raise RequestCaptureError(f"invalid HTTP header line: {line!r}")
        normalized_name = name.lower()
        if normalized_name in seen_headers:
            raise RequestCaptureError(
                f"duplicate HTTP header is not supported: {name}"
            )
        seen_headers.add(normalized_name)
        headers.append((name, value.strip()))

    if not body:
        raise RequestCaptureError("request capture has an empty body")

    content_type = next(
        (value for name, value in headers if name.lower() == "content-type"), ""
    )
    if content_type.lower().startswith("application/json"):
        try:
            json.loads(body)
        except json.JSONDecodeError as error:
            raise RequestCaptureError(
                f"captured request body is not valid JSON: {error}"
            ) from error

    return CapturedRequest(
        method=method,
        path=path,
        headers=tuple(headers),
        body=body,
    )


def render_lua_script(request: CapturedRequest) -> str:
    """Render the static wrk2 request script for a captured request."""
    lines = [
        f"wrk.method = {lua_long_string(request.method)}",
        f"wrk.body = {lua_long_string(request.body)}",
    ]
    for name, value in request.replay_headers:
        lines.append(
            f"wrk.headers[ {lua_long_string(name)} ] = {lua_long_string(value)}"
        )
    return "\n".join(lines) + "\n"


def read_authorization(auth_file: Path) -> str:
    """Read the device authorization header value for a generated request."""
    try:
        token = auth_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BenchmarkError(
            f"cannot read device authorization {auth_file}: {error}"
        ) from error
    if not token.startswith("Macaroon ") or "\n" in token:
        raise BenchmarkError(
            f"{auth_file} must contain a single Macaroon authorization value"
        )
    return token


def generated_headers(profile: BenchmarkProfile) -> tuple[tuple[str, str], ...]:
    """Return the snapd-style headers used for a generated refresh request."""
    return (
        ("Accept", "application/json"),
        ("Content-Type", "application/json"),
        ("Snap-Accept-Delta-Format", "xdelta3"),
        ("Snap-Classic", "false"),
        ("Snap-Device-Architecture", profile.architecture),
        ("Snap-Device-Capabilities", "default-tracks"),
        ("Snap-Device-Series", "16"),
        ("User-Agent", SNAPD_USER_AGENT),
    )


def render_refresh_body(snap_id: str, revision: int, channel: str) -> str:
    """Render a snapd-style refresh request body for one installed snap."""
    refreshed_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "context": [
            {
                "snap-id": snap_id,
                "instance-key": snap_id,
                "revision": revision,
                "tracking-channel": channel,
                "epoch": {"read": [0], "write": [0]},
                "refreshed-date": refreshed_date,
            }
        ],
        "actions": [
            {
                "action": "refresh",
                "instance-key": snap_id,
                "snap-id": snap_id,
                "channel": channel,
                "ignore-validation": False,
            }
        ],
        "fields": list(REFRESH_FIELDS),
    }
    return json.dumps(document, indent=2)


def generate_refresh_request(profile: BenchmarkProfile) -> CapturedRequest:
    """Build a refresh request for the configured snap via the Store info API."""
    if profile.snap_name is None or profile.auth_file is None:
        raise BenchmarkError("profile does not describe a generated request")
    authorization = read_authorization(profile.auth_file)
    base_headers = generated_headers(profile)
    # The info API is public metadata; attaching a device macaroon makes it
    # fail with HTTP 401, so the lookup sends no authorization.
    info_headers = tuple(
        pair for pair in base_headers if pair[0].lower() != "content-type"
    )
    info = fetch_store_info(
        profile.store_url,
        profile.snap_name,
        info_headers,
        wrk_time_seconds(profile.timeout),
    )
    snap_id = snap_id_from_info(info)
    revision = profile.revision
    if revision is None:
        entry = find_channel_map_entry(
            info, profile.snap_name, profile.channel, profile.architecture
        )
        revision_value = entry.get("revision")
        if not isinstance(revision_value, int):
            raise BenchmarkError("channel map entry has no revision")
        revision = revision_value
    print(
        f"Resolved {profile.snap_name} {profile.channel} "
        f"({profile.architecture}) to revision {revision}."
    )
    return CapturedRequest(
        method="POST",
        path=urlparse(profile.url).path or "/",
        headers=base_headers + (("Snap-Device-Authorization", authorization),),
        body=render_refresh_body(snap_id, revision, profile.channel),
    )


def build_request(profile: BenchmarkProfile) -> CapturedRequest:
    """Return the captured request, or generate one for the configured snap."""
    if profile.request_file is not None:
        return parse_request_capture(profile.request_file)
    print(
        f"Generating a refresh request for {profile.snap_name} "
        f"{profile.channel} ({profile.architecture}) ..."
    )
    return generate_refresh_request(profile)


def send_request(
    profile: BenchmarkProfile, request: CapturedRequest
) -> tuple[int, int]:
    """Send the captured request once; return its status and body size."""
    urllib_request = urllib.request.Request(
        profile.url,
        data=request.body.encode("utf-8"),
        headers=dict(request.replay_headers),
        method=request.method,
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(
            urllib_request, timeout=wrk_time_seconds(profile.timeout)
        ) as response:
            return response.status, len(response.read())
    except urllib.error.HTTPError as error:
        return error.code, len(error.read())
    except urllib.error.URLError as error:
        raise BenchmarkError(f"request failed: {error.reason}") from error
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error


def run_preflight(profile: BenchmarkProfile, request: CapturedRequest) -> None:
    """Warm the Store, then send one request and verify its status."""
    if profile.warmup_requests:
        print(f"Warming up with {profile.warmup_requests} requests ...")
        run_warmup(profile.warmup_requests, lambda: send_request(profile, request))

    status, _ = send_request(profile, request)
    if status != profile.expected_status:
        raise BenchmarkError(
            f"preflight returned HTTP {status}; expected {profile.expected_status}"
        )
    print(f"Preflight returned HTTP {status} (redirects disabled).")


SUMMARY_FIELDS = (
    "requests_per_second_target",
    "requests_per_second_achieved",
    "connections",
    "threads",
    "requests_completed",
    "non_2xx_or_3xx",
    "socket_connect_errors",
    "socket_read_errors",
    "socket_write_errors",
    "socket_timeout_errors",
    "exit_code",
    "log_file",
)


def print_plan(
    profile: BenchmarkProfile,
    request: CapturedRequest | None,
    *,
    wrk2_binary: str,
) -> None:
    """Print a redacted execution plan without sending any requests."""
    placeholder_script = Path("<generated-request.lua>")
    print("Dry run: no requests will be sent.")
    print(f"Target: {profile.url}")
    if request is not None:
        print(f"Capture: {profile.request_file}")
        print(f"Captured request: {request.method} {request.path}")
        print(f"Replay headers: {len(request.replay_headers)} (values hidden)")
    else:
        print(
            f"Request: generated at run time for {profile.snap_name} "
            f"{profile.channel} ({profile.architecture})"
        )
        revision_note = (
            f"pretending revision {profile.revision} is installed"
            if profile.revision is not None
            else "current channel revision"
        )
        print(f"Installed revision: {revision_note}")
    print(f"Rates: {', '.join(str(rate) for rate in profile.rates)} requests/second")
    print(f"Duration per rate: {profile.duration}")
    if profile.scale_workers_with_rate:
        print(
            "Stage workers: "
            + ", ".join(
                f"{rate} RPS={connections}c/{threads}t"
                for rate in profile.rates
                for connections, threads in [worker_counts_for_rate(profile, rate)]
            )
        )
        print(
            f"Worker maximums: {profile.connections} connections, "
            f"{profile.threads} threads"
        )
    else:
        print(f"Connections/threads: {profile.connections}/{profile.threads}")
    print(f"Preflight: {'enabled' if profile.preflight_enabled else 'disabled'}")
    print(f"Warmup requests: {profile.warmup_requests}")
    print("Commands:")
    for rate in profile.rates:
        connections, threads = worker_counts_for_rate(profile, rate)
        command = wrk2_command(
            binary=wrk2_binary,
            script_file=placeholder_script,
            url=profile.url,
            rate=rate,
            duration=profile.duration,
            timeout=profile.timeout,
            connections=connections,
            threads=threads,
        )
        print(f"  {shlex.join(command)}")
    print("Use --run to execute this plan.")


def run_benchmark(
    profile: BenchmarkProfile,
    request: CapturedRequest,
    *,
    wrk2_binary: str,
) -> int:
    """Execute every configured request-rate stage."""
    executable = require_wrk2(wrk2_binary)

    if profile.preflight_enabled:
        print(f"Preflighting {profile.url} ...")
        run_preflight(profile, request)

    run_directory = create_run_directory(profile.output_directory, "latency")
    summary_file = run_directory / "summary.csv"
    rows: list[dict[str, Any]] = []
    failed = False

    with tempfile.TemporaryDirectory(
        prefix="wrk2-snap-store-", dir=profile.profile_file.parent
    ) as temporary_directory:
        script_file = write_lua_script(
            Path(temporary_directory), "request.lua", render_lua_script(request)
        )

        for index, rate in enumerate(profile.rates):
            connections, threads = worker_counts_for_rate(profile, rate)
            command = wrk2_command(
                binary=executable,
                script_file=script_file,
                url=profile.url,
                rate=rate,
                duration=profile.duration,
                timeout=profile.timeout,
                connections=connections,
                threads=threads,
            )
            print(
                f"\nRunning {rate} requests/second for {profile.duration} "
                f"({connections} connections, {threads} threads) ...",
                flush=True,
            )
            completed = run_wrk2(command)

            output = completed.stdout
            print(output, end="" if output.endswith("\n") else "\n")
            log_file = write_stage_log(run_directory, rate, command, output)

            rows.append(
                {
                    "requests_per_second_target": rate,
                    **parse_wrk2_metrics(output),
                    "connections": connections,
                    "threads": threads,
                    "exit_code": completed.returncode,
                    "log_file": log_file.name,
                }
            )
            write_summary(summary_file, SUMMARY_FIELDS, rows)

            if completed.returncode != 0:
                failed = True
                print(f"wrk2 exited with status {completed.returncode}; stopping.")
                break

            pause_between_stages(index, len(profile.rates), profile.pause_seconds)

    print(f"\nResults: {run_directory}")
    print(f"Summary: {summary_file}")
    return 1 if failed else 0


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a staged wrk2 benchmark from a snap refresh capture."
    )
    parser.add_argument(
        "profile",
        nargs="?",
        type=Path,
        default=DEFAULT_PROFILE,
        help=f"YAML profile (default: {DEFAULT_PROFILE})",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--run",
        action="store_true",
        help="send the preflight and benchmark requests (default is dry run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_false",
        dest="run",
        help="print the execution plan without sending requests",
    )
    parser.set_defaults(run=False)
    parser.add_argument(
        "--wrk2",
        help="override the wrk2 executable configured in the profile",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        profile = load_profile(arguments.profile)
        wrk2_binary = arguments.wrk2 or profile.wrk2_binary
        if not arguments.run:
            request = (
                parse_request_capture(profile.request_file)
                if profile.request_file is not None
                else None
            )
            print_plan(profile, request, wrk2_binary=wrk2_binary)
            return 0
        return run_benchmark(profile, build_request(profile), wrk2_binary=wrk2_binary)
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nBenchmark interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
