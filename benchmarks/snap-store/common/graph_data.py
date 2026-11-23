"""Benchmark summary loading and latency percentile parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import math
import re


DEFAULT_RESULTS_DIRECTORY = Path(__file__).resolve().parents[1] / "results"


PERCENTILES = ("p50", "p75", "p90", "p99")


PERCENTILE_RE = re.compile(
    r"(?m)^\s*(50|75|90|99)(?:\.000)?%\s+"
    r"([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)\s*$"
)


MILLISECONDS_PER_UNIT = {
    "ns": 0.000001,
    "us": 0.001,
    "ms": 1.0,
    "s": 1_000.0,
    "m": 60_000.0,
    "h": 3_600_000.0,
}


PERCENTILE_NAMES = {
    "50": "p50",
    "75": "p75",
    "90": "p90",
    "99": "p99",
}


class GraphError(Exception):
    """Raised when benchmark results cannot be graphed."""


@dataclass(frozen=True)
class StageResult:
    benchmark_kind: str
    target_rps: float | None
    achieved_rps: float
    connections: int | None
    threads: int | None
    payload_mib_per_second: float | None
    transfer_mib_per_second: float | None
    unexpected_http_responses: int
    body_size_errors: int
    download_host: str | None
    requests_completed: int
    non_success_responses: int
    socket_connect_errors: int
    socket_read_errors: int
    socket_write_errors: int
    socket_timeout_errors: int
    latencies_ms: dict[str, float]
    load_mode: str = "rate"
    stage_status: str | None = None

    @property
    def load_value(self) -> float:
        value = self.connections if self.load_mode == "concurrency" else self.target_rps
        if value is None or value <= 0 or not math.isfinite(value):
            raise GraphError(f"invalid {self.load_mode} load value: {value!r}")
        return value


def find_run_directory(path: Path | None) -> Path:
    """Resolve a run directory, choosing the latest run when omitted."""
    if path is not None:
        candidate = path.expanduser().resolve()
        if candidate.is_file() and candidate.name == "summary.csv":
            candidate = candidate.parent
        if not (candidate / "summary.csv").is_file():
            raise GraphError(f"benchmark summary not found in {candidate}")
        return candidate

    if not DEFAULT_RESULTS_DIRECTORY.is_dir():
        raise GraphError(
            f"results directory does not exist: {DEFAULT_RESULTS_DIRECTORY}"
        )
    candidates = [
        candidate
        for candidate in DEFAULT_RESULTS_DIRECTORY.iterdir()
        if candidate.is_dir() and (candidate / "summary.csv").is_file()
    ]
    if not candidates:
        raise GraphError(
            f"no benchmark runs found under {DEFAULT_RESULTS_DIRECTORY}"
        )
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def parse_latency_percentiles(log_file: Path) -> dict[str, float]:
    """Read selected percentiles from a wrk or wrk2 log."""
    try:
        output = log_file.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphError(f"cannot read wrk2 log {log_file}: {error}") from error

    percentiles: dict[str, float] = {}
    for percentile, value, unit in PERCENTILE_RE.findall(output):
        percentiles[PERCENTILE_NAMES[percentile]] = (
            float(value) * MILLISECONDS_PER_UNIT[unit]
        )
    return percentiles


def _required_float(row: dict[str, str], field: str, row_number: int) -> float:
    value = row.get(field, "")
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise GraphError(
            f"summary row {row_number} has an invalid {field!r} value: {value!r}"
        ) from error


def _integer(row: dict[str, str], field: str, row_number: int) -> int:
    value = row.get(field, "0")
    try:
        return int(value or 0)
    except (TypeError, ValueError) as error:
        raise GraphError(
            f"summary row {row_number} has an invalid {field!r} value: {value!r}"
        ) from error


def _optional_integer(
    row: dict[str, str], field: str, row_number: int
) -> int | None:
    value = row.get(field, "")
    if not value:
        return None
    return _integer(row, field, row_number)


def _optional_float(
    row: dict[str, str], field: str, row_number: int
) -> float | None:
    value = row.get(field, "")
    if not value:
        return None
    return _required_float(row, field, row_number)


def load_results(run_directory: Path) -> list[StageResult]:
    """Load stage summaries and their corresponding latency logs."""
    summary_file = run_directory / "summary.csv"
    try:
        stream = summary_file.open("r", encoding="utf-8", newline="")
    except OSError as error:
        raise GraphError(f"cannot read benchmark summary {summary_file}: {error}") from error

    results: list[StageResult] = []
    with stream:
        reader = csv.DictReader(stream)
        required_fields = {
            "requests_per_second_target",
            "requests_per_second_achieved",
            "requests_completed",
            "non_2xx_or_3xx",
            "socket_connect_errors",
            "socket_read_errors",
            "socket_write_errors",
            "socket_timeout_errors",
            "log_file",
        }
        missing_fields = required_fields.difference(reader.fieldnames or ())
        if missing_fields:
            raise GraphError(
                "summary is missing required columns: "
                + ", ".join(sorted(missing_fields))
            )

        for row_number, row in enumerate(reader, start=2):
            load_mode = row.get("load_mode") or "rate"
            if load_mode not in {"rate", "concurrency"}:
                raise GraphError(f"summary row {row_number} has invalid load_mode: {load_mode!r}")
            if load_mode == "concurrency":
                if row.get("benchmark_kind") != "download":
                    raise GraphError("concurrency summaries must be download benchmarks")
                if row.get("requests_per_second_target"):
                    raise GraphError("concurrency summaries must not specify a target rate")
            log_name = row.get("log_file", "")
            if not log_name or Path(log_name).name != log_name:
                raise GraphError(
                    f"summary row {row_number} has an invalid log_file: {log_name!r}"
                )
            results.append(
                StageResult(
                    benchmark_kind=row.get("benchmark_kind") or "refresh",
                    load_mode=load_mode,
                    stage_status=row.get("stage_status") or None,
                    target_rps=(
                        None if load_mode == "concurrency" else _required_float(
                            row, "requests_per_second_target", row_number
                        )
                    ),
                    achieved_rps=_required_float(
                        row, "requests_per_second_achieved", row_number
                    ),
                    connections=_optional_integer(row, "connections", row_number),
                    threads=_optional_integer(row, "threads", row_number),
                    payload_mib_per_second=_optional_float(
                        row, "payload_mib_per_second", row_number
                    ),
                    transfer_mib_per_second=_optional_float(
                        row, "transfer_mib_per_second", row_number
                    ),
                    unexpected_http_responses=_integer(
                        row, "unexpected_http_responses", row_number
                    ),
                    body_size_errors=_integer(row, "body_size_errors", row_number),
                    download_host=row.get("download_host") or None,
                    requests_completed=_integer(
                        row, "requests_completed", row_number
                    ),
                    non_success_responses=_integer(
                        row,
                        "unexpected_http_responses"
                        if row.get("benchmark_kind") != "download" and row.get("unexpected_http_responses")
                        else "non_2xx_or_3xx",
                        row_number,
                    ),
                    socket_connect_errors=_integer(
                        row, "socket_connect_errors", row_number
                    ),
                    socket_read_errors=_integer(
                        row, "socket_read_errors", row_number
                    ),
                    socket_write_errors=_integer(
                        row, "socket_write_errors", row_number
                    ),
                    socket_timeout_errors=_integer(
                        row, "socket_timeout_errors", row_number
                    ),
                    latencies_ms=parse_latency_percentiles(
                        run_directory / log_name
                    ),
                )
            )

    if not results:
        raise GraphError(f"benchmark summary has no data rows: {summary_file}")
    if len({result.load_mode for result in results}) != 1:
        raise GraphError("cannot combine rate and concurrency stages in one graph")
    return sorted(results, key=lambda result: result.load_value)
