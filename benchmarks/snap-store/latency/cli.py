"""Latency benchmark arguments and dry-run presentation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import argparse
import shlex
import sys

from common.errors import BenchmarkError
from common.wrk import worker_counts_for_rate
from common.wrk import wrk2_command
from latency.profile import BenchmarkProfile
from latency.profile import DEFAULT_PROFILE
from latency.profile import load_profile
from latency.requests import CapturedRequest
from latency.requests import build_request
from latency.requests import parse_request_capture
from latency.runner import run_benchmark


def print_plan(
    profile: BenchmarkProfile,
    request: CapturedRequest | None,
    *,
    wrk2_binary: str,
) -> None:
    """Print a redacted execution plan without sending any requests."""
    placeholder_script = Path("<generated-request.lua>")
    print("Dry run: no requests will be sent.")
    print(f"Target: {profile.url}")
    if request is not None:
        print(f"Capture: {profile.request_file}")
        print(f"Captured request: {request.method} {request.path}")
        print(f"Replay headers: {len(request.replay_headers)} (values hidden)")
    else:
        print(
            f"Request: generated at run time for {profile.snap_name} "
            f"{profile.channel} ({profile.architecture})"
        )
        revision_note = (
            f"pretending revision {profile.revision} is installed"
            if profile.revision is not None
            else "current channel revision"
        )
        print(f"Installed revision: {revision_note}")
    print(f"Rates: {', '.join(str(rate) for rate in profile.rates)} requests/second")
    print(f"Duration per rate: {profile.duration}")
    print(f"Fixed connections/threads: {profile.connections}/{profile.threads}")
    print(f"Preflight: {'enabled' if profile.preflight_enabled else 'disabled'}")
    print(f"Warmup requests: {profile.warmup_requests}")
    print("Commands:")
    for rate in profile.rates:
        connections, threads = worker_counts_for_rate(profile, rate)
        command = wrk2_command(
            binary=wrk2_binary,
            script_file=placeholder_script,
            url=profile.url,
            rate=rate,
            duration=profile.duration,
            timeout=profile.timeout,
            connections=connections,
            threads=threads,
        )
        print(f"  {shlex.join(command)}")
    print("Use --run to execute this plan.")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a staged wrk2 benchmark from a snap refresh capture."
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
        help="send the preflight and benchmark requests (default is dry run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_false",
        dest="run",
        help="print the execution plan without sending requests",
    )
    parser.set_defaults(run=False)
    parser.add_argument(
        "--wrk2",
        help="override the wrk2 executable configured in the profile",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        profile = load_profile(arguments.profile)
        wrk2_binary = arguments.wrk2 or profile.wrk2_binary
        if not arguments.run:
            request = (
                parse_request_capture(profile.request_file)
                if profile.request_file is not None
                else None
            )
            print_plan(profile, request, wrk2_binary=wrk2_binary)
            return 0
        return run_benchmark(profile, build_request(profile), wrk2_binary=wrk2_binary)
    except BenchmarkError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nBenchmark interrupted.", file=sys.stderr)
        return 130
