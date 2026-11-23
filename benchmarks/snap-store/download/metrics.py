"""Download metric parsing, summary columns, and stage failure detection."""

from __future__ import annotations

from collections.abc import Mapping
import re

from common.errors import BenchmarkError
from common.results import parse_http_status_counts
from common.results import parse_wrk2_metrics
from download.errors import DownloadBenchmarkError
from download.profile import DownloadProfile


ELAPSED_RE = re.compile(
    r"(?m)^\s*[0-9]+ requests in ([0-9]+(?:\.[0-9]+)?)(ms|s|m|h),"
)


TRANSFER_RE = re.compile(
    r"(?m)^Transfer/sec:\s+([0-9]+(?:\.[0-9]+)?)(B|KB|MB|GB|TB)$"
)


BODY_SIZE_ERROR_RE = re.compile(r"(?m)^Unexpected response body sizes: ([0-9]+)$")


BYTE_UNITS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


def stage_failure_reason(metrics: Mapping[str, int | float | str]) -> str | None:
    failures = []
    for field in (
        "unexpected_http_responses", "body_size_errors", "non_2xx_or_3xx",
        "socket_connect_errors", "socket_read_errors", "socket_write_errors",
        "socket_timeout_errors",
    ):
        if metrics[field] != 0:
            failures.append(f"{field}={metrics[field]}")
    if metrics["successful_downloads"] == 0:
        failures.append("zero successful downloads")
    return "; ".join(failures) or None


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
    try:
        observed_expected_responses, unexpected_responses = parse_http_status_counts(output)
    except BenchmarkError as error:
        raise DownloadBenchmarkError(str(error)) from error
    body_size_error_match = BODY_SIZE_ERROR_RE.search(output)
    if body_size_error_match is None:
        raise DownloadBenchmarkError(
            "wrk2 output did not include HTTP status counts from the Lua script"
        )

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
        "requests_per_second_achieved": round(
            successful_downloads / elapsed_seconds, 6
        ),
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
    "load_mode",
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
