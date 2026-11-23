"""Download benchmark orchestration and between-stage safeguards."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import sys
import tempfile

from common.results import create_run_directory
from common.results import parse_wrk2_metrics
from common.results import write_stage_log
from common.results import write_summary
from common.wrk import pause_between_stages
from common.wrk import require_wrk2
from common.wrk import run_warmup
from common.wrk import run_wrk2
from common.wrk import write_lua_script
from download.errors import DownloadBenchmarkError
from download.load import download_command
from download.load import download_worker_counts
from download.load import estimate_transfer_bytes
from download.load import render_lua_script
from download.metadata import generator_version
from download.metadata import run_metadata
from download.metadata import url_origin
from download.metadata import write_metadata
from download.metrics import SUMMARY_FIELDS
from download.metrics import parse_download_metrics
from download.metrics import stage_failure_reason
from download.profile import DownloadProfile
from download.requests import ResolvedDownload
from download.requests import resolve_download_url
from download.requests import resolve_snap_download_url
from download.requests import send_download_once


def confirm_next_stage(stage: int, load_mode: str) -> bool:
    unit = "concurrent downloads" if load_mode == "concurrency" else "downloads/second"
    try:
        answer = input(f"Continue to {stage} {unit}? [y/N] ")
    except EOFError:
        print("No confirmation input available; stopping.", file=sys.stderr)
        return False
    return answer.strip().lower() in {"y", "yes"}


def run_benchmark(
    profile: DownloadProfile,
    *,
    binary: str,
    allow_over_budget: bool,
    confirm_stages: bool = False,
) -> int:
    estimate = estimate_transfer_bytes(profile)
    if estimate is None:
        if not allow_over_budget:
            raise DownloadBenchmarkError(
                "concurrency sweep has unknown transfer volume; obtain operator "
                "authorization and pass --allow-over-budget to proceed without a cap"
            )
        print("Warning: transfer volume is unknown; no runtime transfer cap is enforced.")
    else:
        _, guarded_bytes = estimate
        guarded_mib = guarded_bytes / 1024**2
        if guarded_mib > profile.max_estimated_transfer_mib and not allow_over_budget:
            raise DownloadBenchmarkError(
                f"guarded transfer estimate is {guarded_mib:.2f} MiB, above the "
                f"{profile.max_estimated_transfer_mib:.2f} MiB limit; reduce the load, "
                "use target.byte_range, or pass --allow-over-budget"
            )
    executable = require_wrk2(binary)
    version = generator_version(executable)

    if profile.explicit_url is not None:
        resolved = ResolvedDownload(profile.explicit_url, None, profile.expected_size_bytes)
    else:
        print(
            f"Resolving {profile.snap_name} {profile.channel} "
            f"({profile.architecture}) from {profile.store_url} ..."
        )
        resolved = resolve_snap_download_url(profile)

    print(f"Resolving and preflighting {url_origin(resolved.url)} ...")
    download_url = resolve_download_url(profile, resolved.url)
    download_host = urlparse(download_url).netloc
    print(
        f"CDN preflight returned HTTP {profile.expected_download_status} with "
        f"{profile.response_size_bytes:,} bytes from {download_host}."
    )
    run_directory = create_run_directory(profile.output_directory, "download-")
    metadata_file = run_directory / "metadata.json"
    metadata = run_metadata(
        profile, resolved, download_url, version,
        confirm_stages=confirm_stages, allow_over_budget=allow_over_budget,
    )
    write_metadata(metadata_file, metadata)
    print(f"Results: {run_directory}")
    if profile.warmup_requests:
        print(f"Warming the CDN cache with {profile.warmup_requests} downloads ...")
        run_warmup(
            profile.warmup_requests,
            lambda: send_download_once(profile, download_url),
        )

    summary_file = run_directory / "summary.csv"
    rows: list[dict[str, Any]] = []
    failed = False
    stopped = False

    with tempfile.TemporaryDirectory(
        prefix="wrk-snap-download-", dir=profile.profile_file.parent
    ) as temporary_directory:
        script_file = write_lua_script(
            Path(temporary_directory), "download.lua", render_lua_script(profile)
        )

        for index, stage in enumerate(profile.stages):
            if index and confirm_stages and not confirm_next_stage(stage, profile.load_mode):
                stopped = True
                metadata["status"] = "stopped"
                metadata["stop_reason"] = "operator did not confirm the next stage"
                print("Stopping without increasing load; completed results are saved.")
                break
            connections, threads = download_worker_counts(profile, stage)
            command = download_command(
                profile,
                stage,
                binary=executable,
                script_file=script_file,
                url=download_url,
            )
            stage_label = (
                f"{stage} concurrent downloads"
                if profile.concurrent_downloads
                else f"{stage} downloads/second"
            )
            print(
                f"\nRunning {stage_label} for {profile.duration} "
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
                run_directory, stage, redacted_command, sanitized_output,
                prefix="connections" if profile.concurrent_downloads else "rate",
            )

            if completed.returncode != 0:
                print(
                    f"Load generator exited with status {completed.returncode}; "
                    f"see {log_file}.",
                    file=sys.stderr,
                )
                failed = True
                metrics = parse_wrk2_metrics(completed.stdout)
            else:
                metrics = parse_download_metrics(completed.stdout, profile)
                reason = stage_failure_reason(metrics)
                if reason:
                    failed = True
                    metadata["stop_reason"] = reason
                    print(f"Stage failed: {reason}. No further stages will run.", file=sys.stderr)
            rows.append(
                {
                    "benchmark_kind": "download",
                    "load_mode": profile.load_mode,
                    "requests_per_second_target": "" if profile.concurrent_downloads else stage,
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
            metadata["completed_stages"] = len(rows)
            if failed:
                metadata["status"] = "failed"
                if completed.returncode != 0:
                    metadata["stop_reason"] = f"load generator exited with status {completed.returncode}"
            write_metadata(metadata_file, metadata)

            if failed:
                break
            pause_between_stages(index, len(profile.stages), profile.pause_seconds)

    if not failed and not stopped:
        metadata["status"] = "completed"
    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_metadata(metadata_file, metadata)
    print(f"\nResults: {run_directory}")
    print(f"Summary: {summary_file}")
    return 1 if failed or stopped else 0
