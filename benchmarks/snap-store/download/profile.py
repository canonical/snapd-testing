"""Download profile model and YAML validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re
import yaml

from common.errors import ProfileError
from common.profile import build_rates
from common.profile import parse_channel
from common.profile import require_integer
from common.profile import require_mapping
from common.profile import require_number
from common.profile import require_snap_name
from common.profile import require_string
from common.profile import require_url
from common.profile import require_wrk_time
from common.profile import resolve_profile_path
from common.profile import warmup_request_count
from download.errors import DownloadProfileError


DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "helloworld-download-profile.yaml"


BYTE_RANGE_RE = re.compile(r"^([0-9]+)-([0-9]+)$")


REDIRECT_STATUSES = {301, 302, 303, 307, 308}


MANAGED_HEADERS = {"connection", "content-length", "host", "range", "transfer-encoding"}


@dataclass(frozen=True)
class DownloadProfile:
    profile_file: Path
    store_url: str
    snap_name: str | None
    channel: str
    architecture: str
    explicit_url: str | None
    verify_tls: bool
    expected_size_bytes: int
    byte_range: tuple[int, int] | None
    response_size_bytes: int
    store_headers: tuple[tuple[str, str], ...]
    download_headers: tuple[tuple[str, str], ...]
    rates: tuple[int, ...]
    duration: str
    connections: int
    threads: int
    scale_workers_with_rate: bool
    timeout: str
    pause_seconds: float
    expected_redirect_status: int
    expected_download_status: int
    max_redirects: int
    warmup_requests: int
    estimate_multiplier: float
    max_estimated_transfer_mib: float
    output_directory: Path
    wrk2_binary: str
    concurrent_downloads: tuple[int, ...] = ()
    wrk_binary: str = "wrk"

    @property
    def load_mode(self) -> str:
        return "concurrency" if self.concurrent_downloads else "rate"

    @property
    def stages(self) -> tuple[int, ...]:
        return self.concurrent_downloads or self.rates


def require_headers(value: Any, name: str) -> tuple[tuple[str, str], ...]:
    """Validate a profile header mapping, rejecting transport-managed headers."""
    headers = require_mapping(value, name)
    result: list[tuple[str, str]] = []
    for header_name, header_value in headers.items():
        if not isinstance(header_name, str) or not header_name.strip():
            raise DownloadProfileError(f"{name} contains an invalid header name")
        if header_name.lower() in MANAGED_HEADERS:
            raise DownloadProfileError(
                f"{name} must not set transport-managed header {header_name!r}"
            )
        if not isinstance(header_value, str):
            raise DownloadProfileError(
                f"{name}.{header_name} must be a string; quote YAML numbers"
            )
        result.append((header_name, header_value))
    return tuple(result)


def load_profile(profile_file: Path) -> DownloadProfile:
    """Load and validate a download benchmark profile."""
    profile_file = profile_file.expanduser().resolve()
    try:
        with profile_file.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except OSError as error:
        raise DownloadProfileError(
            f"cannot read profile {profile_file}: {error}"
        ) from error
    except yaml.YAMLError as error:
        raise DownloadProfileError(
            f"invalid YAML in {profile_file}: {error}"
        ) from error

    root = require_mapping(document, "profile")
    target = require_mapping(root.get("target"), "target")
    load = require_mapping(root.get("load"), "load")
    if ("downloads_per_second" in load) == ("concurrent_downloads" in load):
        raise DownloadProfileError(
            "load must set exactly one of downloads_per_second or concurrent_downloads"
        )
    preflight = require_mapping(root.get("preflight", {}), "preflight")
    safety = require_mapping(root.get("safety"), "safety")
    output = require_mapping(root.get("output", {}), "output")
    wrk2 = require_mapping(root.get("wrk2", {}), "wrk2")
    wrk = require_mapping(root.get("wrk", {}), "wrk")

    snap_value = target.get("snap")
    url_value = target.get("url")
    if snap_value is not None and url_value is not None:
        raise DownloadProfileError("target must set either snap or url, not both")
    if snap_value is None and url_value is None:
        raise DownloadProfileError("target must set snap or url")

    store_url = require_url(
        target.get("store_url", "https://api.snapcraft.io"), "target.store_url"
    )
    snap_name: str | None = None
    channel = "latest/stable"
    architecture = "amd64"
    explicit_url: str | None = None
    if url_value is not None:
        explicit_url = require_url(url_value, "target.url")
    else:
        snap_name = require_snap_name(snap_value, "target.snap")
        track, risk, branch = parse_channel(
            require_string(target.get("channel", "latest/stable"), "target.channel")
        )
        channel = "/".join(part for part in (track, risk, branch) if part)
        architecture = require_string(
            target.get("architecture", "amd64"), "target.architecture"
        )

    verify_tls = target.get("verify_tls", True)
    if not isinstance(verify_tls, bool):
        raise DownloadProfileError("target.verify_tls must be true or false")

    expected_size_bytes = require_integer(
        target.get("expected_size_bytes"), "target.expected_size_bytes"
    )
    byte_range_value = target.get("byte_range")
    byte_range: tuple[int, int] | None = None
    response_size_bytes = expected_size_bytes
    if byte_range_value is not None:
        if not isinstance(byte_range_value, str):
            raise DownloadProfileError(
                "target.byte_range must be null or a quoted START-END string"
            )
        match = BYTE_RANGE_RE.fullmatch(byte_range_value)
        if match is None:
            raise DownloadProfileError(
                "target.byte_range must use the format START-END"
            )
        start, end = (int(part) for part in match.groups())
        if end < start or end >= expected_size_bytes:
            raise DownloadProfileError(
                "target.byte_range must be ordered and within expected_size_bytes"
            )
        byte_range = (start, end)
        response_size_bytes = end - start + 1

    concurrent_downloads: tuple[int, ...] = ()
    rates: tuple[int, ...] = ()
    if "concurrent_downloads" in load:
        values = load["concurrent_downloads"]
        if not isinstance(values, list) or not values:
            raise DownloadProfileError("load.concurrent_downloads must be a non-empty list")
        concurrent_downloads = tuple(
            require_integer(value, "load.concurrent_downloads entry") for value in values
        )
        if tuple(sorted(set(concurrent_downloads))) != concurrent_downloads:
            raise DownloadProfileError(
                "load.concurrent_downloads must be strictly increasing"
            )
        if "connections" in load or "scale_workers_with_rate" in load:
            raise DownloadProfileError(
                "concurrent_downloads replaces connections and scale_workers_with_rate"
            )
        connections = max(concurrent_downloads)
    else:
        rates_document = require_mapping(
            load["downloads_per_second"], "load.downloads_per_second"
        )
        try:
            rates = build_rates(
                require_integer(
                    rates_document.get("start"), "load.downloads_per_second.start"
                ),
                require_integer(
                    rates_document.get("end"), "load.downloads_per_second.end"
                ),
                require_integer(
                    rates_document.get("increment"),
                    "load.downloads_per_second.increment",
                ),
            )
        except ProfileError as error:
            raise DownloadProfileError(str(error).replace("requests", "downloads")) from error
        connections = require_integer(load.get("connections"), "load.connections")
    threads = require_integer(load.get("threads"), "load.threads")
    if threads > connections:
        raise DownloadProfileError(
            "load.threads must not be greater than load.connections"
        )
    if any(count % threads for count in concurrent_downloads):
        raise DownloadProfileError(
            "load.threads must divide every concurrent_downloads value evenly"
        )
    scale_workers = load.get("scale_workers_with_rate", False)
    if not isinstance(scale_workers, bool):
        raise DownloadProfileError("load.scale_workers_with_rate must be true or false")
    if scale_workers:
        raise DownloadProfileError("rate sweeps require fixed connections and threads")
    if not concurrent_downloads and connections % threads:
        raise DownloadProfileError("load.threads must divide load.connections evenly")

    pause_seconds = require_number(
        load.get("pause_seconds", 0), "load.pause_seconds", minimum=0
    )
    expected_redirect_status = require_integer(
        preflight.get("expected_redirect_status", 302),
        "preflight.expected_redirect_status",
        minimum=300,
    )
    if expected_redirect_status not in REDIRECT_STATUSES:
        raise DownloadProfileError(
            "preflight.expected_redirect_status must be 301, 302, 303, 307, or 308"
        )
    default_download_status = 206 if byte_range is not None else 200
    expected_download_status = require_integer(
        preflight.get("expected_download_status", default_download_status),
        "preflight.expected_download_status",
        minimum=100,
    )

    return DownloadProfile(
        profile_file=profile_file,
        store_url=store_url,
        snap_name=snap_name,
        channel=channel,
        architecture=architecture,
        explicit_url=explicit_url,
        verify_tls=verify_tls,
        expected_size_bytes=expected_size_bytes,
        byte_range=byte_range,
        response_size_bytes=response_size_bytes,
        store_headers=require_headers(
            target.get("store_headers", {}), "target.store_headers"
        ),
        download_headers=require_headers(
            target.get("download_headers", {}), "target.download_headers"
        ),
        rates=rates,
        duration=require_wrk_time(load.get("duration"), "load.duration"),
        connections=connections,
        threads=threads,
        scale_workers_with_rate=scale_workers,
        timeout=require_wrk_time(load.get("timeout", "10s"), "load.timeout"),
        pause_seconds=pause_seconds,
        expected_redirect_status=expected_redirect_status,
        expected_download_status=expected_download_status,
        max_redirects=require_integer(
            preflight.get("max_redirects", 5),
            "preflight.max_redirects",
        ),
        warmup_requests=warmup_request_count(root),
        estimate_multiplier=require_number(
            safety.get("estimate_multiplier", 2),
            "safety.estimate_multiplier",
            minimum=1,
        ),
        max_estimated_transfer_mib=require_number(
            safety.get("max_estimated_transfer_mib"),
            "safety.max_estimated_transfer_mib",
            minimum=0.001,
        ),
        output_directory=resolve_profile_path(
            profile_file, output.get("directory", "results"), "output.directory"
        ),
        wrk2_binary=require_string(wrk2.get("binary", "wrk2"), "wrk2.binary"),
        concurrent_downloads=concurrent_downloads,
        wrk_binary=require_string(wrk.get("binary", "wrk"), "wrk.binary"),
    )
