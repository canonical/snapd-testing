"""Sanitized download run metadata and source provenance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hashlib
import json
import platform
import re
import subprocess
import yaml

from download.errors import DownloadBenchmarkError
from download.profile import DownloadProfile
from download.requests import ResolvedDownload


def generator_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DownloadBenchmarkError(f"cannot read load-generator version: {error}") from error
    # Both wrk variants print a version followed by usage and exit with status 1.
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if re.match(r"^wrk\S*\s+\S+\s+\[", line) and result.returncode in (0, 1):
            return line
    raise DownloadBenchmarkError("load generator did not report a recognizable version")


def url_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def run_metadata(
    profile: DownloadProfile, resolved: ResolvedDownload, download_url: str,
    version: str, *, confirm_stages: bool, allow_over_budget: bool,
) -> dict[str, Any]:
    configuration = asdict(profile)
    for field in ("profile_file", "output_directory", "wrk_binary", "wrk2_binary"):
        del configuration[field]
    for field in ("store_headers", "download_headers"):
        configuration[field] = "[redacted]"
    configuration["store_url"] = url_origin(profile.store_url)
    configuration["explicit_url"] = (
        url_origin(profile.explicit_url) if profile.explicit_url else None
    )
    source_hash = hashlib.sha256()
    try:
        benchmark_directory = Path(__file__).resolve().parents[1]
        source_files = [
            benchmark_directory / "download_benchmark.py",
            *sorted((benchmark_directory / "download").glob("*.py")),
            *sorted((benchmark_directory / "common").glob("*.py")),
        ]
        for source_file in source_files:
            source_hash.update(
                source_file.relative_to(benchmark_directory).as_posix().encode("utf-8")
            )
            source_hash.update(b"\0")
            source_hash.update(source_file.read_bytes())
    except OSError as error:
        raise DownloadBenchmarkError(f"cannot fingerprint benchmark source: {error}") from error
    return {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "incomplete",
        "completed_stages": 0,
        "profile": configuration,
        "resolved_download": {
            "revision": resolved.revision,
            "revision_source": "store-info" if resolved.revision is not None else "unknown-explicit-url",
            "size_bytes": resolved.size_bytes,
            "size_source": "store-info" if resolved.revision is not None else "profile-preflight",
            "download_origin": url_origin(download_url),
        },
        "client": {
            "hostname": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
        },
        "tools": {
            "load_generator": "wrk" if profile.concurrent_downloads else "wrk2",
            "load_generator_version": version,
            "python": platform.python_version(),
            "pyyaml": yaml.__version__,
            "benchmark_source_sha256": source_hash.hexdigest(),
        },
        "options": {
            "confirm_stages": confirm_stages,
            "allow_over_budget": allow_over_budget,
            "stop_policy": "any-stage-error-or-zero-successes",
        },
    }


def write_metadata(path: Path, metadata: Mapping[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8") as stream:
            json.dump(metadata, stream, indent=2)
            stream.write("\n")
        path.chmod(0o600)
    except OSError as error:
        raise DownloadBenchmarkError(f"cannot write run metadata: {error}") from error
