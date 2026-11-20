"""Latency stage metric validation and failure detection."""

from __future__ import annotations

import math
from collections.abc import Mapping

from common.errors import BenchmarkError
from common.results import parse_http_status_counts, parse_wrk2_metrics


def parse_latency_metrics(output: str) -> dict[str, int | float | str]:
    metrics = parse_wrk2_metrics(output)
    completed = metrics["requests_completed"]
    achieved = metrics["requests_per_second_achieved"]
    if (
        not isinstance(completed, int)
        or not isinstance(achieved, (int, float))
        or not math.isfinite(achieved)
        or achieved < 0
    ):
        raise BenchmarkError("wrk2 output has missing or invalid request metrics")
    expected, unexpected = parse_http_status_counts(output)
    if expected + unexpected != completed:
        raise BenchmarkError("wrk2 output has inconsistent HTTP status and request counts")
    return {
        **metrics,
        "expected_status_responses": expected,
        "unexpected_http_responses": unexpected,
    }


def stage_failure_reason(metrics: Mapping[str, int | float | str]) -> str | None:
    failures = [
        f"{field}={metrics[field]}"
        for field in (
            "unexpected_http_responses", "socket_connect_errors",
            "socket_read_errors", "socket_write_errors", "socket_timeout_errors",
        )
        if metrics[field] != 0
    ]
    if metrics["expected_status_responses"] == 0:
        failures.append("zero successful requests")
    return "; ".join(failures) or None
