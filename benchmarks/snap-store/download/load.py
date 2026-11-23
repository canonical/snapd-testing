"""Download load planning, transfer estimates, and Lua generation."""

from __future__ import annotations

from pathlib import Path

from common.profile import wrk_time_seconds
from common.wrk import lua_long_string
from common.wrk import render_response_validation
from common.wrk import worker_counts_for_rate
from common.wrk import wrk2_command
from download.profile import DownloadProfile
from download.requests import download_request_headers


def estimate_transfer_bytes(profile: DownloadProfile) -> tuple[int, int] | None:
    """Return scheduled/guarded bytes, or None for an unpaced sweep."""
    if profile.concurrent_downloads:
        return None
    scheduled_downloads = wrk_time_seconds(profile.duration) * sum(profile.rates)
    scheduled_bytes = round(scheduled_downloads * profile.response_size_bytes)
    guarded_bytes = round(scheduled_bytes * profile.estimate_multiplier)
    return scheduled_bytes, guarded_bytes


def download_worker_counts(profile: DownloadProfile, stage: int) -> tuple[int, int]:
    if profile.concurrent_downloads:
        return stage, profile.threads
    return worker_counts_for_rate(profile, stage)


def download_command(
    profile: DownloadProfile, stage: int, *, binary: str, script_file: Path, url: str
) -> list[str]:
    connections, threads = download_worker_counts(profile, stage)
    return wrk2_command(
        binary=binary,
        script_file=script_file,
        url=url,
        rate=None if profile.concurrent_downloads else stage,
        duration=profile.duration,
        timeout=profile.timeout,
        connections=connections,
        threads=threads,
    )


def render_lua_script(profile: DownloadProfile) -> str:
    """Render a GET request script with exact HTTP status accounting."""
    lines = ["wrk.method = [[GET]]"]
    for name, value in download_request_headers(profile):
        lines.append(
            "wrk.headers[ "
            f"{lua_long_string(name)} ] = "
            f"{lua_long_string(value)}"
        )
    return "\n".join(lines) + "\n" + render_response_validation(
        profile.expected_download_status, profile.response_size_bytes,
    )
