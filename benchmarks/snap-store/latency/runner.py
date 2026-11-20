"""Latency benchmark stage orchestration and summary schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import sys
import tempfile

from common.errors import BenchmarkError
from common.results import create_run_directory
from common.results import parse_wrk2_metrics
from common.results import write_stage_log
from common.results import write_summary
from common.wrk import pause_between_stages
from common.wrk import require_wrk2
from common.wrk import run_wrk2
from common.wrk import worker_counts_for_rate
from common.wrk import write_lua_script
from common.wrk import wrk2_command
from latency.metrics import parse_latency_metrics, stage_failure_reason
from latency.profile import BenchmarkProfile
from latency.requests import CapturedRequest
from latency.requests import render_lua_script
from latency.requests import run_preflight


SUMMARY_FIELDS = (
    "requests_per_second_target",
    "requests_per_second_achieved",
    "connections",
    "threads",
    "requests_completed",
    "expected_status_responses",
    "unexpected_http_responses",
    "non_2xx_or_3xx",
    "socket_connect_errors",
    "socket_read_errors",
    "socket_write_errors",
    "socket_timeout_errors",
    "exit_code",
    "stage_status",
    "failure_reason",
    "log_file",
)


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

    run_directory = create_run_directory(profile.output_directory, "latency-")
    summary_file = run_directory / "summary.csv"
    rows: list[dict[str, Any]] = []
    failed = False

    with tempfile.TemporaryDirectory(
        prefix="wrk2-snap-store-", dir=profile.profile_file.parent
    ) as temporary_directory:
        script_file = write_lua_script(
            Path(temporary_directory), "request.lua",
            render_lua_script(request, expected_status=profile.expected_status),
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

            reason = ""
            if completed.returncode != 0:
                metrics = parse_wrk2_metrics(output)
                reason = f"wrk2 exited with status {completed.returncode}"
            else:
                try:
                    metrics = parse_latency_metrics(output)
                except BenchmarkError as error:
                    metrics = parse_wrk2_metrics(output)
                    reason = str(error)
                else:
                    reason = stage_failure_reason(metrics) or ""
            failed = bool(reason)
            rows.append(
                {
                    "requests_per_second_target": rate,
                    **metrics,
                    "connections": connections,
                    "threads": threads,
                    "exit_code": completed.returncode,
                    "stage_status": "failed" if failed else "passed",
                    "failure_reason": reason,
                    "log_file": log_file.name,
                }
            )
            write_summary(summary_file, SUMMARY_FIELDS, rows)

            if failed:
                print(f"Stage failed: {reason}. No further stages will run.", file=sys.stderr)
                break

            pause_between_stages(index, len(profile.rates), profile.pause_seconds)

    print(f"\nResults: {run_directory}")
    print(f"Summary: {summary_file}")
    return 1 if failed else 0
