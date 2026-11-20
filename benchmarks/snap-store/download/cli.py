"""Download benchmark arguments and dry-run presentation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import argparse
import shlex
import sys

from common.errors import BenchmarkError
from download.errors import DownloadProfileError
from download.load import download_command
from download.load import estimate_transfer_bytes
from download.profile import DEFAULT_PROFILE
from download.profile import DownloadProfile
from download.profile import load_profile
from download.runner import run_benchmark


def print_plan(profile: DownloadProfile, *, binary: str) -> None:
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
    print(f"Load mode: {profile.load_mode}")
    print(f"Duration per stage: {profile.duration}")
    if profile.concurrent_downloads:
        print(
            "Concurrent downloads: "
            + ", ".join(str(count) for count in profile.concurrent_downloads)
        )
        print(f"Threads: {profile.threads}; no requests-per-second target.")
    else:
        print(f"Fixed connections/threads: {profile.connections}/{profile.threads}")
    estimate = estimate_transfer_bytes(profile)
    if estimate is None:
        print(
            "Transfer estimate: unknown (concurrency does not bound download count).\n"
            "Requires --allow-over-budget; no runtime transfer cap is enforced."
        )
    else:
        scheduled_bytes, guarded_bytes = estimate
        print(f"Scheduled payload: {scheduled_bytes / 1024**2:.2f} MiB")
        print(
            f"Guarded estimate: {guarded_bytes / 1024**2:.2f} MiB "
            f"({profile.estimate_multiplier:g}x multiplier)"
        )
    print(f"Configured limit: {profile.max_estimated_transfer_mib:.2f} MiB")
    print(f"Warmup requests: {profile.warmup_requests}")
    print("Commands (CDN URL is resolved only with --run):")
    placeholder_script = Path("<generated-download.lua>")
    for stage in profile.stages:
        command = download_command(
            profile,
            stage,
            binary=binary,
            script_file=placeholder_script,
            url="<resolved-cdn-url>",
        )
        print(f"  {shlex.join(command)}")
    print("Use --run to resolve the CDN URL and execute this plan.")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a wrk concurrency or wrk2 rate snap download benchmark."
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
    parser.add_argument("--wrk", help="override the standard wrk executable for concurrency mode")
    parser.add_argument(
        "--confirm-stages", action="store_true",
        help="ask before each higher-load stage; only y/yes continues",
    )
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help="run when transfer volume is unknown or exceeds the configured estimate limit",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        profile = load_profile(arguments.profile)
        if profile.concurrent_downloads:
            if arguments.wrk2:
                raise DownloadProfileError("concurrency mode requires --wrk, not --wrk2")
            binary = arguments.wrk or profile.wrk_binary
        else:
            if arguments.wrk:
                raise DownloadProfileError("rate mode requires --wrk2, not --wrk")
            binary = arguments.wrk2 or profile.wrk2_binary
        if not arguments.run:
            print_plan(profile, binary=binary)
            print(f"Confirm higher-load stages: {arguments.confirm_stages}")
            print("Stop policy: any stage error or zero successful downloads.")
            return 0
        return run_benchmark(
            profile,
            binary=binary,
            allow_over_budget=arguments.allow_over_budget,
            confirm_stages=arguments.confirm_stages,
        )
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDownload benchmark interrupted.", file=sys.stderr)
        return 130
