"""Command-line interface for benchmark result graphs."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import argparse
import sys

from common.graph_data import GraphError
from common.graph_data import find_run_directory
from common.graph_data import load_results
from common.graphs import graph_results


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
    print(f"Parsed latency percentiles from {logs_with_latency} load-generator logs")
    missing_latency = sum(
        not any(value > 0 for value in result.latencies_ms.values()) for result in results
    )
    if missing_latency:
        print(
            f"Warning: {missing_latency} stages have no positive latency samples.",
            file=sys.stderr,
        )
    print(f"Graph: {output_file}")
    return 0
