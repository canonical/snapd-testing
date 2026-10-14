#!/usr/bin/env python3
"""Generate graphs from a snap Store wrk2 benchmark run."""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import ScalarFormatter  # noqa: E402


DEFAULT_RESULTS_DIRECTORY = Path(__file__).with_name("results")
PERCENTILES = ("p50", "p75", "p90", "p99")
PERCENTILE_RE = re.compile(
    r"(?m)^\s*(50\.000|75\.000|90\.000|99\.000)%\s+"
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
    "50.000": "p50",
    "75.000": "p75",
    "90.000": "p90",
    "99.000": "p99",
}


class GraphError(Exception):
    """Raised when benchmark results cannot be graphed."""


@dataclass(frozen=True)
class StageResult:
    benchmark_kind: str
    target_rps: float
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
    """Read selected corrected-latency percentiles from a wrk2 log."""
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
            log_name = row.get("log_file", "")
            if not log_name or Path(log_name).name != log_name:
                raise GraphError(
                    f"summary row {row_number} has an invalid log_file: {log_name!r}"
                )
            results.append(
                StageResult(
                    benchmark_kind=row.get("benchmark_kind") or "refresh",
                    target_rps=_required_float(
                        row, "requests_per_second_target", row_number
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
                        row, "non_2xx_or_3xx", row_number
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
    return sorted(results, key=lambda result: result.target_rps)


def _bar_width(target_rates: Sequence[float]) -> float:
    differences = [
        right - left
        for left, right in zip(target_rates, target_rates[1:])
        if right > left
    ]
    return min(differences) * 0.7 if differences else 0.7


def graph_results(
    run_directory: Path,
    results: Sequence[StageResult],
    output_file: Path,
) -> None:
    """Render latency, throughput, and error graphs into one PNG."""
    target_rates = [result.target_rps for result in results]
    is_download = any(result.benchmark_kind == "download" for result in results)
    panel_count = 4 if is_download else 3
    figure, subplot_axes = plt.subplots(
        panel_count,
        1,
        figsize=(11, 4 * panel_count + 1),
        constrained_layout=True,
    )
    axes = list(subplot_axes)
    if is_download:
        download_host = next(
            (result.download_host for result in results if result.download_host),
            "resolved CDN",
        )
        figure.suptitle(
            f"Snap download benchmark: {run_directory.name}\n{download_host}",
            fontsize=16,
        )
    else:
        figure.suptitle(f"Snap Store benchmark: {run_directory.name}", fontsize=16)

    latency_axis = axes[0]
    colors = ("#0077b6", "#2a9d8f", "#f4a261", "#d62828")
    all_latencies: list[float] = []
    for percentile, color in zip(PERCENTILES, colors):
        values = [
            result.latencies_ms.get(percentile, math.nan) for result in results
        ]
        all_latencies.extend(value for value in values if not math.isnan(value))
        latency_axis.plot(
            target_rates,
            values,
            marker="o",
            linewidth=2,
            color=color,
            label=percentile,
        )
    if not all_latencies:
        raise GraphError("no p50, p75, p90, or p99 latency data found in wrk2 logs")
    latency_axis.set_yscale("log")
    latency_axis.yaxis.set_major_formatter(ScalarFormatter())
    latency_axis.set_title("Response latency percentiles")
    latency_axis.set_ylabel("Latency (ms, log scale)")
    latency_axis.legend(ncols=4)
    latency_axis.grid(True, which="both", alpha=0.25)

    next_axis = 1
    if is_download:
        download_axis = axes[next_axis]
        next_axis += 1
        download_axis.plot(
            target_rates,
            [result.payload_mib_per_second for result in results],
            marker="o",
            linewidth=2,
            color="#2a9d8f",
            label="Validated payload",
        )
        download_axis.plot(
            target_rates,
            [result.transfer_mib_per_second for result in results],
            marker="o",
            linewidth=2,
            color="#0077b6",
            label="wrk bytes read",
        )
        download_axis.set_title("Aggregate download throughput")
        download_axis.set_ylabel("MiB per second")
        download_axis.legend()
        download_axis.grid(True, alpha=0.25)

    throughput_axis = axes[next_axis]
    next_axis += 1
    throughput_axis.plot(
        target_rates,
        target_rates,
        linestyle="--",
        linewidth=2,
        color="#6c757d",
        label="Target",
    )
    throughput_axis.plot(
        target_rates,
        [result.achieved_rps for result in results],
        marker="o",
        linewidth=2,
        color="#0077b6",
        label="Achieved",
    )
    throughput_axis.set_title("Target versus achieved throughput")
    throughput_axis.set_ylabel(
        "Downloads per second" if is_download else "Requests per second"
    )
    throughput_axis.legend()
    throughput_axis.grid(True, alpha=0.25)

    error_axis = axes[next_axis]
    http_error_series = (
        (
            "Unexpected HTTP status",
            [result.unexpected_http_responses for result in results],
            "#d62828",
        )
        if is_download
        else (
            "Non-2xx/3xx responses",
            [result.non_success_responses for result in results],
            "#d62828",
        )
    )
    error_series = (
        http_error_series,
        *(
            (
                (
                    "Unexpected body size",
                    [result.body_size_errors for result in results],
                    "#e76f51",
                ),
            )
            if is_download
            else ()
        ),
        (
            "Socket connect",
            [result.socket_connect_errors for result in results],
            "#f4a261",
        ),
        (
            "Socket read",
            [result.socket_read_errors for result in results],
            "#6a4c93",
        ),
        (
            "Socket write",
            [result.socket_write_errors for result in results],
            "#2a9d8f",
        ),
        (
            "Socket timeout",
            [result.socket_timeout_errors for result in results],
            "#495057",
        ),
    )
    bottoms = [0] * len(results)
    for label, values, color in error_series:
        if any(values):
            error_axis.bar(
                target_rates,
                values,
                width=_bar_width(target_rates),
                bottom=bottoms,
                color=color,
                label=label,
            )
            bottoms = [bottom + value for bottom, value in zip(bottoms, values)]
    if any(bottoms):
        error_axis.legend(ncols=2)
    else:
        error_axis.text(
            0.5,
            0.5,
            (
                "No HTTP, body-size, or socket errors recorded"
                if is_download
                else "No HTTP or socket errors recorded"
            ),
            horizontalalignment="center",
            verticalalignment="center",
            transform=error_axis.transAxes,
        )
    error_axis.set_title("Errors by applied load")
    error_axis.set_ylabel("Error count")
    error_axis.grid(True, axis="y", alpha=0.25)

    for axis in axes:
        axis.set_xticks(target_rates)

    if all(
        result.connections is not None and result.threads is not None
        for result in results
    ):
        error_axis.set_xticklabels(
            [
                f"{result.target_rps:g}\n{result.connections}c/{result.threads}t"
                for result in results
            ]
        )
        error_axis.set_xlabel("Target requests per second\nconnections/threads")
        if is_download:
            error_axis.set_xlabel(
                "Target downloads per second\nconnections/threads"
            )
    else:
        error_axis.set_xlabel(
            "Target downloads per second"
            if is_download
            else "Target requests per second"
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(output_file, dpi=180)
    except OSError as error:
        raise GraphError(f"cannot write graph {output_file}: {error}") from error
    finally:
        plt.close(figure)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate graphs from a latency or download benchmark run."
    )
    parser.add_argument(
        "run",
        nargs="?",
        type=Path,
        help="run directory or summary.csv (default: latest run)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output PNG (default: <run>/benchmark-graphs.png)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        run_directory = find_run_directory(arguments.run)
        results = load_results(run_directory)
        output_file = (
            arguments.output.expanduser().resolve()
            if arguments.output
            else run_directory / "benchmark-graphs.png"
        )
        graph_results(run_directory, results, output_file)
    except GraphError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    logs_with_latency = sum(bool(result.latencies_ms) for result in results)
    print(f"Loaded {len(results)} load stages from {run_directory / 'summary.csv'}")
    print(f"Parsed latency percentiles from {logs_with_latency} wrk2 logs")
    print(f"Graph: {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
