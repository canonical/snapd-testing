"""Run directories, stage logs, shared wrk metrics, and CSV summaries."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
import csv
import re
import shlex

from common.errors import BenchmarkError


def parse_http_status_counts(output: str) -> tuple[int, int]:
    """Read the exact expected/unexpected status counters emitted by Lua."""
    expected = re.search(r"(?m)^Expected HTTP status responses: ([0-9]+)$", output)
    unexpected = re.search(r"(?m)^Unexpected HTTP status responses: ([0-9]+)$", output)
    if expected is None or unexpected is None:
        raise BenchmarkError(
            "wrk2 output did not include HTTP status counts from the Lua script"
        )
    return int(expected.group(1)), int(unexpected.group(1))


def create_run_directory(output_directory: Path, name_prefix: str = "") -> Path:
    """Create a UTC timestamped results directory for one benchmark run."""
    output_directory.mkdir(parents=True, exist_ok=True)
    run_name = datetime.now(timezone.utc).strftime(f"{name_prefix}%Y_%m_%d_T%H_%M_%SZ")
    run_directory = output_directory / run_name
    run_directory.mkdir()
    return run_directory


def write_stage_log(
    run_directory: Path, rate: int, command: Sequence[str], output: str,
    *, prefix: str = "rate",
) -> Path:
    """Record the command and output of one stage."""
    log_file = run_directory / f"{prefix}-{rate}.log"
    log_file.write_text(f"$ {shlex.join(command)}\n\n{output}", encoding="utf-8")
    return log_file


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
