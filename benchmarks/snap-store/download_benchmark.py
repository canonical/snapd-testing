#!/usr/bin/env python3
"""Benchmark snap downloads from the CDN resolved by the Store."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml

from wrk2_common import (
    BenchmarkError,
    ProfileError,
    build_rates,
    create_run_directory,
    fetch_store_info,
    find_channel_map_entry,
    http_get,
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
    warmup_request_count,
    worker_counts_for_rate,
    write_lua_script,
    write_stage_log,
    write_summary,
    wrk_time_seconds,
    wrk2_command,
)


DEFAULT_PROFILE = Path(__file__).parent / "profiles" / "helloworld-download-profile.yaml"
BYTE_RANGE_RE = re.compile(r"^([0-9]+)-([0-9]+)$")
ELAPSED_RE = re.compile(
    r"(?m)^\s*[0-9]+ requests in ([0-9]+(?:\.[0-9]+)?)(ms|s|m|h),"
)
TRANSFER_RE = re.compile(
    r"(?m)^Transfer/sec:\s+([0-9]+(?:\.[0-9]+)?)(B|KB|MB|GB|TB)$"
)
EXPECTED_STATUS_RE = re.compile(r"(?m)^Expected HTTP status responses: ([0-9]+)$")
UNEXPECTED_STATUS_RE = re.compile(
    r"(?m)^Unexpected HTTP status responses: ([0-9]+)$"
)
BODY_SIZE_ERROR_RE = re.compile(r"(?m)^Unexpected response body sizes: ([0-9]+)$")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MANAGED_HEADERS = {"connection", "content-length", "host", "range", "transfer-encoding"}
BYTE_UNITS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


class DownloadBenchmarkError(BenchmarkError):
    """Raised when download benchmark setup or execution fails."""


class DownloadProfileError(ProfileError):
    """Raised when the download profile is invalid."""


@dataclass(frozen=True)
class DownloadProfile:
    profile_file: Path
    store_url: str
    snap_name: str | None
    channel: str
    architecture: str
    explicit_url: str | None
    expected_size_bytes: int
    byte_range: tuple[int, int] | None
    response_size_bytes: int
    store_headers: tuple[tuple[str, str], ...]
    download_headers: tuple[tuple[str, str], ...]
    rates: tuple[int, ...]
    duration: str
    connections: int
    threads: int
    scale_workers_with_rate: bool
    timeout: str
    pause_seconds: float
    expected_redirect_status: int
    expected_download_status: int
    max_redirects: int
    warmup_requests: int
    estimate_multiplier: float
    max_estimated_transfer_mib: float
    output_directory: Path
    wrk2_binary: str


def require_headers(value: Any, name: str) -> tuple[tuple[str, str], ...]:
    """Validate a profile header mapping, rejecting transport-managed headers."""
    headers = require_mapping(value, name)
    result: list[tuple[str, str]] = []
    for header_name, header_value in headers.items():
        if not isinstance(header_name, str) or not header_name.strip():
            raise DownloadProfileError(f"{name} contains an invalid header name")
        if header_name.lower() in MANAGED_HEADERS:
            raise DownloadProfileError(
                f"{name} must not set transport-managed header {header_name!r}"
            )
        if not isinstance(header_value, str):
            raise DownloadProfileError(
                f"{name}.{header_name} must be a string; quote YAML numbers"
            )
        result.append((header_name, header_value))
    return tuple(result)


def load_profile(profile_file: Path) -> DownloadProfile:
    """Load and validate a download benchmark profile."""
    profile_file = profile_file.expanduser().resolve()
    try:
        with profile_file.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except OSError as error:
        raise DownloadProfileError(
            f"cannot read profile {profile_file}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise DownloadProfileError(
            f"invalid YAML in {profile_file}: {error}"
        ) from error

    root = require_mapping(document, "profile")
    target = require_mapping(root.get("target"), "target")
    load = require_mapping(root.get("load"), "load")
    rates_document = require_mapping(
        load.get("downloads_per_second"), "load.downloads_per_second"
    )
    preflight = require_mapping(root.get("preflight", {}), "preflight")
    safety = require_mapping(root.get("safety"), "safety")
    output = require_mapping(root.get("output", {}), "output")
    wrk2 = require_mapping(root.get("wrk2", {}), "wrk2")

    snap_value = target.get("snap")
    url_value = target.get("url")
    if snap_value is not None and url_value is not None:
        raise DownloadProfileError("target must set either snap or url, not both")
    if snap_value is None and url_value is None:
        raise DownloadProfileError("target must set snap or url")

    store_url = require_url(
        target.get("store_url", "https://api.snapcraft.io"), "target.store_url"
    )
    snap_name: str | None = None
    channel = "latest/stable"
    architecture = "amd64"
    explicit_url: str | None = None
    if url_value is not None:
        explicit_url = require_url(url_value, "target.url")
    else:
        snap_name = require_snap_name(snap_value, "target.snap")
        track, risk, branch = parse_channel(
            require_string(target.get("channel", "latest/stable"), "target.channel")
        )
        channel = "/".join(part for part in (track, risk, branch) if part)
        architecture = require_string(
            target.get("architecture", "amd64"), "target.architecture"
        )

    expected_size_bytes = require_integer(
        target.get("expected_size_bytes"), "target.expected_size_bytes"
    )
    byte_range_value = target.get("byte_range")
    byte_range: tuple[int, int] | None = None
    response_size_bytes = expected_size_bytes
    if byte_range_value is not None:
        if not isinstance(byte_range_value, str):
            raise DownloadProfileError(
                "target.byte_range must be null or a quoted START-END string"
            )
        match = BYTE_RANGE_RE.fullmatch(byte_range_value)
        if match is None:
            raise DownloadProfileError(
                "target.byte_range must use the format START-END"
            )
        start, end = (int(part) for part in match.groups())
        if end < start or end >= expected_size_bytes:
            raise DownloadProfileError(
                "target.byte_range must be ordered and within expected_size_bytes"
            )
        byte_range = (start, end)
        response_size_bytes = end - start + 1

    try:
        rates = build_rates(
            require_integer(
                rates_document.get("start"), "load.downloads_per_second.start"
            ),
            require_integer(
                rates_document.get("end"), "load.downloads_per_second.end"
            ),
            require_integer(
                rates_document.get("increment"),
                "load.downloads_per_second.increment",
            ),
        )
    except ProfileError as error:
        raise DownloadProfileError(str(error).replace("requests", "downloads")) from error

    connections = require_integer(load.get("connections"), "load.connections")
    threads = require_integer(load.get("threads"), "load.threads")
    if threads > connections:
        raise DownloadProfileError(
            "load.threads must not be greater than load.connections"
        )
    scale_workers = load.get("scale_workers_with_rate", False)
    if not isinstance(scale_workers, bool):
        raise DownloadProfileError("load.scale_workers_with_rate must be true or false")

    pause_seconds = require_number(
        load.get("pause_seconds", 0), "load.pause_seconds", minimum=0
    )
    expected_redirect_status = require_integer(
        preflight.get("expected_redirect_status", 302),
        "preflight.expected_redirect_status",
        minimum=300,
    )
    if expected_redirect_status not in REDIRECT_STATUSES:
        raise DownloadProfileError(
            "preflight.expected_redirect_status must be 301, 302, 303, 307, or 308"
        )
    default_download_status = 206 if byte_range is not None else 200
    expected_download_status = require_integer(
        preflight.get("expected_download_status", default_download_status),
        "preflight.expected_download_status",
        minimum=100,
    )

    return DownloadProfile(
        profile_file=profile_file,
        store_url=store_url,
        snap_name=snap_name,
        channel=channel,
        architecture=architecture,
        explicit_url=explicit_url,
        expected_size_bytes=expected_size_bytes,
        byte_range=byte_range,
        response_size_bytes=response_size_bytes,
        store_headers=require_headers(
            target.get("store_headers", {}), "target.store_headers"
        ),
        download_headers=require_headers(
            target.get("download_headers", {}), "target.download_headers"
        ),
        rates=rates,
        duration=require_wrk_time(load.get("duration"), "load.duration"),
        connections=connections,
        threads=threads,
        scale_workers_with_rate=scale_workers,
        timeout=require_wrk_time(load.get("timeout", "10s"), "load.timeout"),
        pause_seconds=pause_seconds,
        expected_redirect_status=expected_redirect_status,
        expected_download_status=expected_download_status,
        max_redirects=require_integer(
            preflight.get("max_redirects", 5),
            "preflight.max_redirects",
        ),
        warmup_requests=warmup_request_count(root),
        estimate_multiplier=require_number(
            safety.get("estimate_multiplier", 2),
            "safety.estimate_multiplier",
            minimum=1,
        ),
        max_estimated_transfer_mib=require_number(
            safety.get("max_estimated_transfer_mib"),
            "safety.max_estimated_transfer_mib",
            minimum=0.001,
        ),
        output_directory=resolve_profile_path(
            profile_file, output.get("directory", "results"), "output.directory"
        ),
        wrk2_binary=require_string(wrk2.get("binary", "wrk2"), "wrk2.binary"),
    )


def estimate_transfer_bytes(profile: DownloadProfile) -> tuple[int, int]:
    """Return the scheduled payload and guarded payload estimate."""
    scheduled_downloads = wrk_time_seconds(profile.duration) * sum(profile.rates)
    scheduled_bytes = round(scheduled_downloads * profile.response_size_bytes)
    guarded_bytes = round(scheduled_bytes * profile.estimate_multiplier)
    return scheduled_bytes, guarded_bytes


def download_request_headers(profile: DownloadProfile) -> tuple[tuple[str, str], ...]:
    headers = list(profile.download_headers)
    if profile.byte_range is not None:
        start, end = profile.byte_range
        headers.append(("Range", f"bytes={start}-{end}"))
    return tuple(headers)


def resolve_download_url(profile: DownloadProfile, start_url: str) -> str:
    """Resolve Store and CDN redirects while keeping Store headers off the CDN."""
    timeout = wrk_time_seconds(profile.timeout)
    status, headers, _ = http_get(
        start_url, profile.store_headers, timeout
    )
    if status != profile.expected_redirect_status:
        raise DownloadBenchmarkError(
            f"Store returned HTTP {status}; expected redirect "
            f"{profile.expected_redirect_status}"
        )
    location = headers.get("Location")
    if not location:
        raise DownloadBenchmarkError("Store redirect did not include a Location header")

    current_url = urljoin(start_url, location)
    request_headers = download_request_headers(profile)
    for _ in range(profile.max_redirects):
        status, headers, body = http_get(current_url, request_headers, timeout)
        if status in REDIRECT_STATUSES:
            location = headers.get("Location")
            if not location:
                raise DownloadBenchmarkError(
                    f"CDN returned HTTP {status} without a Location header"
                )
            current_url = urljoin(current_url, location)
            continue

        if status != profile.expected_download_status:
            raise DownloadBenchmarkError(
                f"CDN returned HTTP {status}; expected "
                f"{profile.expected_download_status}"
            )
        if len(body) != profile.response_size_bytes:
            raise DownloadBenchmarkError(
                f"CDN returned {len(body)} bytes; expected "
                f"{profile.response_size_bytes}"
            )
        return current_url

    raise DownloadBenchmarkError(
        f"download exceeded the limit of {profile.max_redirects} CDN redirects"
    )


def resolve_snap_download_url(profile: DownloadProfile) -> str:
    """Find the snap's current download URL through the Store info API."""
    info = fetch_store_info(
        profile.store_url,
        profile.snap_name,
        profile.store_headers,
        wrk_time_seconds(profile.timeout),
    )
    entry = find_channel_map_entry(
        info, profile.snap_name, profile.channel, profile.architecture
    )
    download = entry.get("download")
    if not isinstance(download, Mapping):
        raise DownloadBenchmarkError("channel map entry has no download section")
    url = download.get("url")
    size = download.get("size")
    if not isinstance(url, str) or not url.strip():
        raise DownloadBenchmarkError("channel map entry has no download URL")
    if size != profile.expected_size_bytes:
        raise DownloadBenchmarkError(
            f"Store advertises {size} bytes for {profile.snap_name} "
            f"{profile.channel} ({profile.architecture}), but "
            f"target.expected_size_bytes is {profile.expected_size_bytes}; "
            "update the profile"
        )
    return urljoin(profile.store_url, url)


def send_download_once(profile: DownloadProfile, url: str) -> tuple[int, int]:
    """Download the payload once; return its status and body size."""
    status, _, body = http_get(
        url, download_request_headers(profile), wrk_time_seconds(profile.timeout)
    )
    return status, len(body)


def render_lua_script(profile: DownloadProfile) -> str:
    """Render a GET request script with exact HTTP status accounting."""
    lines = ["wrk.method = [[GET]]"]
    for name, value in download_request_headers(profile):
        lines.append(
            "wrk.headers[ "
            f"{lua_long_string(name)} ] = "
            f"{lua_long_string(value)}"
        )
    lines.extend(
        [
            "local threads = {}",
            "expected_status_responses = 0",
            "unexpected_status_responses = 0",
            "body_size_errors = 0",
            "setup = function(thread)",
            "  table.insert(threads, thread)",
            "end",
            "response = function(status, headers, body)",
            f"  if status == {profile.expected_download_status} then",
            "    expected_status_responses = expected_status_responses + 1",
            f"    if #body ~= {profile.response_size_bytes} then",
            "      body_size_errors = body_size_errors + 1",
            "    end",
            "  else",
            "    unexpected_status_responses = unexpected_status_responses + 1",
            "  end",
            "end",
            "done = function(summary, latency, requests)",
            "  local expected = 0",
            "  local unexpected = 0",
            "  local body_errors = 0",
            "  for _, thread in ipairs(threads) do",
            "    expected = expected + (thread:get(\"expected_status_responses\") or 0)",
            "    unexpected = unexpected + (thread:get(\"unexpected_status_responses\") or 0)",
            "    body_errors = body_errors + (thread:get(\"body_size_errors\") or 0)",
            "  end",
            "  io.write(string.format(\"Expected HTTP status responses: %d\\n\", expected))",
            "  io.write(string.format(\"Unexpected HTTP status responses: %d\\n\", unexpected))",
            "  io.write(string.format(\"Unexpected response body sizes: %d\\n\", body_errors))",
            "end",
        ]
    )
    return "\n".join(lines) + "\n"


def _elapsed_seconds(output: str) -> float:
    match = ELAPSED_RE.search(output)
    if match is None:
        raise DownloadBenchmarkError("wrk2 output did not include elapsed time")
    value, unit = match.groups()
    multipliers = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return float(value) * multipliers[unit]


def _transfer_mib_per_second(output: str) -> float:
    match = TRANSFER_RE.search(output)
    if match is None:
        raise DownloadBenchmarkError("wrk2 output did not include transfer rate")
    value, unit = match.groups()
    return float(value) * BYTE_UNITS[unit] / 1024**2


def parse_download_metrics(
    output: str, profile: DownloadProfile
) -> dict[str, int | float | str]:
    """Extract request, status, latency-supporting, and transfer metrics."""
    metrics = parse_wrk2_metrics(output)
    expected_match = EXPECTED_STATUS_RE.search(output)
    unexpected_match = UNEXPECTED_STATUS_RE.search(output)
    body_size_error_match = BODY_SIZE_ERROR_RE.search(output)
    if (
        expected_match is None
        or unexpected_match is None
        or body_size_error_match is None
    ):
        raise DownloadBenchmarkError(
            "wrk2 output did not include HTTP status counts from the Lua script"
        )

    observed_expected_responses = int(expected_match.group(1))
    unexpected_responses = int(unexpected_match.group(1))
    body_size_errors = int(body_size_error_match.group(1))
    requests_completed = metrics["requests_completed"]
    if not isinstance(requests_completed, int):
        raise DownloadBenchmarkError("wrk2 output did not include completed requests")
    successful_downloads = max(
        0, requests_completed - unexpected_responses - body_size_errors
    )
    elapsed_seconds = _elapsed_seconds(output)
    payload_mib_per_second = (
        successful_downloads * profile.response_size_bytes / elapsed_seconds / 1024**2
    )
    return {
        **metrics,
        "observed_expected_status_responses": observed_expected_responses,
        "successful_downloads": successful_downloads,
        "unexpected_http_responses": unexpected_responses,
        "body_size_errors": body_size_errors,
        "payload_mib_per_second": round(payload_mib_per_second, 6),
        "transfer_mib_per_second": round(
            _transfer_mib_per_second(output), 6
        ),
    }


SUMMARY_FIELDS = (
    "benchmark_kind",
    "requests_per_second_target",
    "requests_per_second_achieved",
    "connections",
    "threads",
    "response_size_bytes",
    "observed_expected_status_responses",
    "successful_downloads",
    "requests_completed",
    "payload_mib_per_second",
    "transfer_mib_per_second",
    "unexpected_http_responses",
    "body_size_errors",
    "non_2xx_or_3xx",
    "socket_connect_errors",
    "socket_read_errors",
    "socket_write_errors",
    "socket_timeout_errors",
    "exit_code",
    "download_host",
    "log_file",
)


def print_plan(profile: DownloadProfile, *, wrk2_binary: str) -> None:
    scheduled_bytes, guarded_bytes = estimate_transfer_bytes(profile)
    print("Dry run: no Store or CDN requests will be sent.")
    if profile.snap_name is not None:
        print(
            f"Snap: {profile.snap_name} {profile.channel} "
            f"({profile.architecture}) from {profile.store_url}"
        )
    else:
        print(f"Download URL: {profile.explicit_url}")
    print(f"Response size: {profile.response_size_bytes:,} bytes")
    if profile.byte_range is not None:
        print(f"Byte range: {profile.byte_range[0]}-{profile.byte_range[1]}")
    print(f"Rates: {', '.join(str(rate) for rate in profile.rates)} downloads/second")
    print(f"Duration per rate: {profile.duration}")
    if profile.scale_workers_with_rate:
        print(
            "Stage workers: "
            + ", ".join(
                f"{rate} DPS={connections}c/{threads}t"
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
    print(f"Scheduled payload: {scheduled_bytes / 1024**2:.2f} MiB")
    print(
        f"Guarded estimate: {guarded_bytes / 1024**2:.2f} MiB "
        f"({profile.estimate_multiplier:g}x multiplier)"
    )
    print(f"Configured limit: {profile.max_estimated_transfer_mib:.2f} MiB")
    print(f"Warmup requests: {profile.warmup_requests}")
    print("Commands (CDN URL is resolved only with --run):")
    placeholder_script = Path("<generated-download.lua>")
    for rate in profile.rates:
        connections, threads = worker_counts_for_rate(profile, rate)
        command = wrk2_command(
            binary=wrk2_binary,
            script_file=placeholder_script,
            url="<resolved-cdn-url>",
            rate=rate,
            duration=profile.duration,
            timeout=profile.timeout,
            connections=connections,
            threads=threads,
        )
        print(f"  {shlex.join(command)}")
    print("Use --run to resolve the CDN URL and execute this plan.")


def run_benchmark(
    profile: DownloadProfile,
    *,
    wrk2_binary: str,
    allow_over_budget: bool,
) -> int:
    executable = require_wrk2(wrk2_binary)

    _, guarded_bytes = estimate_transfer_bytes(profile)
    guarded_mib = guarded_bytes / 1024**2
    if guarded_mib > profile.max_estimated_transfer_mib and not allow_over_budget:
        raise DownloadBenchmarkError(
            f"guarded transfer estimate is {guarded_mib:.2f} MiB, above the "
            f"{profile.max_estimated_transfer_mib:.2f} MiB limit; reduce the load, "
            "use target.byte_range, or pass --allow-over-budget"
        )

    if profile.explicit_url is not None:
        store_download_url = profile.explicit_url
    else:
        print(
            f"Resolving {profile.snap_name} {profile.channel} "
            f"({profile.architecture}) from {profile.store_url} ..."
        )
        store_download_url = resolve_snap_download_url(profile)

    print(f"Resolving and preflighting {store_download_url} ...")
    download_url = resolve_download_url(profile, store_download_url)
    download_host = urlparse(download_url).netloc
    print(
        f"CDN preflight returned HTTP {profile.expected_download_status} with "
        f"{profile.response_size_bytes:,} bytes from {download_host}."
    )
    if profile.warmup_requests:
        print(f"Warming the CDN cache with {profile.warmup_requests} downloads ...")
        run_warmup(
            profile.warmup_requests,
            lambda: send_download_once(profile, download_url),
        )

    run_directory = create_run_directory(profile.output_directory, "download-")
    summary_file = run_directory / "summary.csv"
    rows: list[dict[str, Any]] = []
    failed = False

    with tempfile.TemporaryDirectory(
        prefix="wrk2-snap-download-", dir=profile.profile_file.parent
    ) as temporary_directory:
        script_file = write_lua_script(
            Path(temporary_directory), "download.lua", render_lua_script(profile)
        )

        for index, rate in enumerate(profile.rates):
            connections, threads = worker_counts_for_rate(profile, rate)
            command = wrk2_command(
                binary=executable,
                script_file=script_file,
                url=download_url,
                rate=rate,
                duration=profile.duration,
                timeout=profile.timeout,
                connections=connections,
                threads=threads,
            )
            print(
                f"\nRunning {rate} downloads/second for {profile.duration} "
                f"({connections} connections, {threads} threads) ...",
                flush=True,
            )
            completed = run_wrk2(command)

            sanitized_output = completed.stdout.replace(
                download_url, "<resolved-cdn-url>"
            )
            print(
                sanitized_output,
                end="" if sanitized_output.endswith("\n") else "\n",
            )
            redacted_command = [
                "<resolved-cdn-url>" if value == download_url else value
                for value in command
            ]
            log_file = write_stage_log(
                run_directory, rate, redacted_command, sanitized_output
            )

            metrics = parse_download_metrics(completed.stdout, profile)
            rows.append(
                {
                    "benchmark_kind": "download",
                    "requests_per_second_target": rate,
                    **metrics,
                    "connections": connections,
                    "threads": threads,
                    "response_size_bytes": profile.response_size_bytes,
                    "exit_code": completed.returncode,
                    "download_host": download_host,
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
        description="Run a staged wrk2 snap download benchmark."
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
        help="resolve and benchmark the download (default is dry run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_false",
        dest="run",
        help="print the execution and transfer plan without network requests",
    )
    parser.set_defaults(run=False)
    parser.add_argument("--wrk2", help="override the configured wrk2 executable")
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help="run even when the guarded estimate exceeds the configured limit",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        profile = load_profile(arguments.profile)
        wrk2_binary = arguments.wrk2 or profile.wrk2_binary
        if not arguments.run:
            print_plan(profile, wrk2_binary=wrk2_binary)
            return 0
        return run_benchmark(
            profile,
            wrk2_binary=wrk2_binary,
            allow_over_budget=arguments.allow_over_budget,
        )
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDownload benchmark interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
