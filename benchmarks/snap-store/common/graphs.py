"""Latency, throughput, and error graph rendering for both benchmarks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

import matplotlib

from common.graph_data import GraphError, PERCENTILES, StageResult

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import ScalarFormatter  # noqa: E402


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
    target_rates = [result.load_value for result in results]
    is_concurrency = results[0].load_mode == "concurrency"
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
        values = [value if value > 0 else math.nan for value in values]
        all_latencies.extend(value for value in values if math.isfinite(value))
        latency_axis.plot(
            target_rates,
            values,
            marker="o",
            linewidth=2,
            color=color,
            label=percentile,
        )
    if all_latencies:
        latency_axis.set_yscale("log")
        latency_axis.yaxis.set_major_formatter(ScalarFormatter())
        latency_axis.set_ylabel("Latency (ms, log scale)")
        latency_axis.legend(ncols=4)
    else:
        latency_axis.text(
            0.5, 0.5, "No positive latency samples recorded",
            horizontalalignment="center", verticalalignment="center",
            transform=latency_axis.transAxes,
        )
    latency_axis.set_title(
        "Response latency percentiles (wrk, closed-loop)"
        if is_concurrency else "Response latency percentiles (schedule-corrected)"
    )
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
            label="Completed, validated payload",
        )
        download_axis.plot(
            target_rates,
            [result.transfer_mib_per_second for result in results],
            marker="o",
            linewidth=2,
            color="#0077b6",
            label="Bytes read (includes incomplete transfers)",
        )
        download_axis.set_title("Aggregate download throughput")
        download_axis.set_ylabel("MiB per second")
        download_axis.legend()
        download_axis.grid(True, alpha=0.25)

    throughput_axis = axes[next_axis]
    next_axis += 1
    if not is_concurrency:
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
    throughput_axis.set_title(
        "Successful completed downloads" if is_concurrency
        else "Target versus achieved throughput"
    )
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
            "HTTP errors",
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

    load_labels = [
        f"{result.load_value:g}" + (" (failed)" if result.stage_status == "failed" else "")
        for result in results
    ]
    if all(
        result.connections is not None and result.threads is not None
        for result in results
    ):
        error_axis.set_xticklabels(
            [
                f"{label}\n{result.connections}c/{result.threads}t"
                for result, label in zip(results, load_labels)
            ]
        )
        error_axis.set_xlabel("Target requests per second\nconnections/threads")
        if is_download:
            error_axis.set_xlabel(
                "Target downloads per second\nconnections/threads"
            )
    else:
        error_axis.set_xticklabels(load_labels)
        error_axis.set_xlabel(
            "Target downloads per second"
            if is_download
            else "Target requests per second"
        )
    if is_concurrency:
        error_axis.set_xlabel("Concurrent downloads\nconnections/threads")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(output_file, dpi=180)
    except OSError as error:
        raise GraphError(f"cannot write graph {output_file}: {error}") from error
    finally:
        plt.close(figure)
