"""Latency profile model and YAML validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
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


DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "snapd-refresh-profile.yaml"


@dataclass(frozen=True)
class BenchmarkProfile:
    profile_file: Path
    url: str
    request_file: Path | None
    snap_name: str | None
    channel: str
    architecture: str
    revision: int | None
    store_url: str
    auth_file: Path | None
    verify_tls: bool
    rates: tuple[int, ...]
    duration: str
    connections: int
    threads: int
    scale_workers_with_rate: bool
    timeout: str
    pause_seconds: float
    preflight_enabled: bool
    expected_status: int
    warmup_requests: int
    output_directory: Path
    wrk2_binary: str


def load_profile(profile_file: Path) -> BenchmarkProfile:
    """Load and validate a benchmark profile."""
    profile_file = profile_file.expanduser().resolve()
    try:
        with profile_file.open("r", encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except OSError as error:
        raise ProfileError(f"cannot read profile {profile_file}: {error}") from error
    except yaml.YAMLError as error:
        raise ProfileError(f"invalid YAML in {profile_file}: {error}") from error

    root = require_mapping(document, "profile")
    target = require_mapping(root.get("target"), "target")
    load = require_mapping(root.get("load"), "load")
    rate = require_mapping(
        load.get("requests_per_second"), "load.requests_per_second"
    )
    preflight = require_mapping(root.get("preflight", {}), "preflight")
    output = require_mapping(root.get("output", {}), "output")
    wrk2 = require_mapping(root.get("wrk2", {}), "wrk2")

    url = require_url(target.get("url"), "target.url")

    verify_tls = target.get("verify_tls", True)
    if not isinstance(verify_tls, bool):
        raise ProfileError("target.verify_tls must be true or false")

    request_file_value = target.get("request_file")
    snap_value = target.get("snap")
    if request_file_value is not None and snap_value is not None:
        raise ProfileError("target must set either request_file or snap, not both")
    if request_file_value is None and snap_value is None:
        raise ProfileError("target must set request_file or snap")

    request_file: Path | None = None
    snap_name: str | None = None
    channel = "latest/stable"
    architecture = "amd64"
    revision: int | None = None
    store_url = ""
    auth_file: Path | None = None
    if request_file_value is not None:
        request_file = resolve_profile_path(
            profile_file, request_file_value, "target.request_file"
        )
        if not request_file.is_file():
            raise ProfileError(f"target.request_file does not exist: {request_file}")
    else:
        snap_name = require_snap_name(snap_value, "target.snap")
        track, risk, branch = parse_channel(
            require_string(target.get("channel", "latest/stable"), "target.channel")
        )
        channel = "/".join(part for part in (track, risk, branch) if part)
        architecture = require_string(
            target.get("architecture", "amd64"), "target.architecture"
        )
        revision_value = target.get("revision")
        if revision_value is not None:
            revision = require_integer(revision_value, "target.revision")
        parsed_url = urlparse(url)
        store_url = require_url(
            target.get("store_url", f"{parsed_url.scheme}://{parsed_url.netloc}"),
            "target.store_url",
        )
        auth_file = resolve_profile_path(
            profile_file,
            target.get("auth_file", "./device-authorization"),
            "target.auth_file",
        )

    rates = build_rates(
        require_integer(rate.get("start"), "load.requests_per_second.start"),
        require_integer(rate.get("end"), "load.requests_per_second.end"),
        require_integer(
            rate.get("increment"), "load.requests_per_second.increment"
        ),
    )

    connections = require_integer(load.get("connections"), "load.connections")
    threads = require_integer(load.get("threads"), "load.threads")
    if threads > connections:
        raise ProfileError("load.threads must not be greater than load.connections")

    scale_workers_with_rate = load.get("scale_workers_with_rate", False)
    if not isinstance(scale_workers_with_rate, bool):
        raise ProfileError("load.scale_workers_with_rate must be true or false")
    if scale_workers_with_rate:
        raise ProfileError("rate sweeps require fixed connections and threads")
    if connections % threads:
        raise ProfileError("load.threads must divide load.connections evenly")

    pause_seconds = require_number(
        load.get("pause_seconds", 0), "load.pause_seconds", minimum=0
    )

    preflight_enabled = preflight.get("enabled", True)
    if not isinstance(preflight_enabled, bool):
        raise ProfileError("preflight.enabled must be true or false")

    return BenchmarkProfile(
        profile_file=profile_file,
        url=url,
        request_file=request_file,
        snap_name=snap_name,
        channel=channel,
        architecture=architecture,
        revision=revision,
        store_url=store_url,
        auth_file=auth_file,
        verify_tls=verify_tls,
        rates=rates,
        duration=require_wrk_time(load.get("duration"), "load.duration"),
        connections=connections,
        threads=threads,
        scale_workers_with_rate=scale_workers_with_rate,
        timeout=require_wrk_time(load.get("timeout", "10s"), "load.timeout"),
        pause_seconds=pause_seconds,
        preflight_enabled=preflight_enabled,
        expected_status=require_integer(
            preflight.get("expected_status", 200),
            "preflight.expected_status",
            minimum=100,
        ),
        warmup_requests=warmup_request_count(root),
        output_directory=resolve_profile_path(
            profile_file, output.get("directory", "results"), "output.directory"
        ),
        wrk2_binary=require_string(wrk2.get("binary", "wrk2"), "wrk2.binary"),
    )
