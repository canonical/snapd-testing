"""Load-generator commands, Lua files, workers, and stage lifecycle helpers."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
import shutil
import subprocess
import time

from common.errors import BenchmarkError


def lua_long_string(value: str) -> str:
    """Encode text as a Lua long string without modifying its contents."""
    equals = ""
    while f"]{equals}]" in value:
        equals += "="
    return f"[{equals}[{value}]{equals}]"


def render_response_validation(
    expected_status: int, response_size_bytes: int | None = None,
) -> str:
    """Count exact HTTP statuses and optionally validate response body sizes."""
    check_body = response_size_bytes is not None
    lines = [
        "local threads = {}",
        "expected_status_responses = 0",
        "unexpected_status_responses = 0",
        *(["body_size_errors = 0"] if check_body else []),
        "setup = function(thread)",
        "  table.insert(threads, thread)",
        "end",
        "response = function(status, headers, body)",
        f"  if status == {expected_status} then",
        "    expected_status_responses = expected_status_responses + 1",
        *([
            f"    if #body ~= {response_size_bytes} then",
            "      body_size_errors = body_size_errors + 1",
            "    end",
        ] if check_body else []),
        "  else",
        "    unexpected_status_responses = unexpected_status_responses + 1",
        "  end",
        "end",
        "done = function(summary, latency, requests)",
        "  local expected = 0",
        "  local unexpected = 0",
        *(["  local body_errors = 0"] if check_body else []),
        "  for _, thread in ipairs(threads) do",
        '    expected = expected + (thread:get("expected_status_responses") or 0)',
        '    unexpected = unexpected + (thread:get("unexpected_status_responses") or 0)',
        *(['    body_errors = body_errors + (thread:get("body_size_errors") or 0)'] if check_body else []),
        "  end",
        '  io.write(string.format("Expected HTTP status responses: %d\\n", expected))',
        '  io.write(string.format("Unexpected HTTP status responses: %d\\n", unexpected))',
        *(['  io.write(string.format("Unexpected response body sizes: %d\\n", body_errors))'] if check_body else []),
        "end",
    ]
    return "\n".join(lines) + "\n"


class WorkerLimits(Protocol):
    """Profile attributes that control per-stage worker counts."""

    connections: int
    threads: int
    scale_workers_with_rate: bool


def worker_counts_for_rate(profile: WorkerLimits, rate: int) -> tuple[int, int]:
    """Return a fixed worker pool, independent of the stage's request rate."""
    return profile.connections, profile.threads


def wrk2_command(
    *,
    binary: str,
    script_file: Path,
    url: str,
    rate: int | None,
    duration: str,
    timeout: str,
    connections: int,
    threads: int,
) -> list[str]:
    """Build a wrk2 rate stage, or a standard wrk stage when rate is None."""
    return [
        binary,
        "--threads",
        str(threads),
        "--connections",
        str(connections),
        "--duration",
        duration,
        "--timeout",
        timeout,
        *(["--rate", str(rate)] if rate is not None else []),
        "--latency",
        "--script",
        str(script_file),
        url,
    ]


def require_wrk2(binary: str) -> str:
    """Resolve the wrk2 executable or fail with a clear error."""
    executable = shutil.which(binary)
    if executable is None:
        raise BenchmarkError(f"benchmark executable not found: {binary}")
    return executable


def run_wrk2(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one wrk2 stage, capturing its combined output."""
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        raise BenchmarkError(f"failed to start wrk2: {error}") from error


def write_lua_script(directory: Path, name: str, content: str) -> Path:
    """Write the generated wrk2 script with owner-only permissions."""
    script_file = directory / name
    script_file.write_text(content, encoding="utf-8")
    script_file.chmod(0o600)
    return script_file


def pause_between_stages(index: int, stage_count: int, pause_seconds: float) -> None:
    """Pause between stages so the target can recover."""
    if index < stage_count - 1 and pause_seconds:
        print(f"Pausing {pause_seconds:g} seconds before the next rate.")
        time.sleep(pause_seconds)


def run_warmup(count: int, send: Callable[[], tuple[int, int]]) -> None:
    """Send best-effort requests to warm server-side caches before measuring.

    Failures are reported but never stop the benchmark: a cold cache is
    exactly what the warmup is trying to fix.
    """
    for index in range(1, count + 1):
        start = time.monotonic()
        try:
            status, size = send()
        except BenchmarkError as error:
            print(f"Warmup {index}/{count}: {error}")
            continue
        elapsed = time.monotonic() - start
        print(
            f"Warmup {index}/{count}: HTTP {status}, "
            f"{size:,} bytes in {elapsed:.2f}s"
        )
