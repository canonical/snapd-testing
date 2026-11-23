"""Shared YAML value validation, channel parsing, and load configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import re

from common.errors import ProfileError


WRK_TIME_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[smh]$")


SNAP_NAME_RE = re.compile(r"^[a-z0-9](?:-?[a-z0-9])*$")


RISKS = ("stable", "candidate", "beta", "edge")


_TIME_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600}


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{name} must be a mapping")
    return value


def require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{name} must be a non-empty string")
    return value.strip()


def require_integer(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError(
            f"{name} must be an integer greater than or equal to {minimum}"
        )
    return value


def require_number(value: Any, name: str, *, minimum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < minimum
    ):
        raise ProfileError(
            f"{name} must be a number greater than or equal to {minimum:g}"
        )
    return float(value)


def require_wrk_time(value: Any, name: str) -> str:
    value = require_string(value, name)
    if not WRK_TIME_RE.fullmatch(value) or float(value[:-1]) <= 0:
        raise ProfileError(
            f"{name} must be a positive wrk2 duration such as 30s or 2m"
        )
    return value


def require_url(value: Any, name: str) -> str:
    url = require_string(value, name)
    parsed = urlparse(url)
    try:
        parsed.port
    except ValueError as error:
        raise ProfileError(f"{name} has an invalid port: {error}") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProfileError(f"{name} must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ProfileError(f"{name} must not contain credentials")
    if parsed.fragment:
        raise ProfileError(f"{name} must not contain a fragment")
    return url


def require_snap_name(value: Any, name: str) -> str:
    snap_name = require_string(value, name)
    if not SNAP_NAME_RE.fullmatch(snap_name):
        raise ProfileError(f"{name} is not a valid snap name: {snap_name!r}")
    return snap_name


def parse_channel(value: str) -> tuple[str, str, str | None]:
    """Split a channel name into track, risk, and optional branch."""
    parts = value.split("/")
    if len(parts) == 1:
        parts.insert(0, "latest")
    if len(parts) not in {2, 3} or not all(parts):
        raise ProfileError(f"target.channel is not a valid channel: {value!r}")
    track, risk, *branch = parts
    if risk not in RISKS:
        raise ProfileError(
            f"target.channel risk must be one of {', '.join(RISKS)}: {value!r}"
        )
    return track, risk, branch[0] if branch else None


def resolve_profile_path(profile_file: Path, value: Any, name: str) -> Path:
    """Resolve a profile path entry relative to the profile's directory."""
    path = Path(require_string(value, name)).expanduser()
    if not path.is_absolute():
        path = profile_file.parent / path
    return path.resolve()


def build_rates(start: int, end: int, increment: int) -> tuple[int, ...]:
    """Build an inclusive, evenly spaced request-rate sequence."""
    if start <= 0:
        raise ProfileError("load.requests_per_second.start must be greater than zero")
    if end < start:
        raise ProfileError(
            "load.requests_per_second.end must be greater than or equal to start"
        )
    if increment <= 0:
        raise ProfileError(
            "load.requests_per_second.increment must be greater than zero"
        )
    if (end - start) % increment != 0:
        raise ProfileError(
            "load.requests_per_second.end must be reachable from start using increment"
        )
    return tuple(range(start, end + 1, increment))


def wrk_time_seconds(value: str) -> float:
    """Convert a wrk2 duration such as 30s or 2m into seconds."""
    return float(value[:-1]) * _TIME_MULTIPLIERS[value[-1]]


def warmup_request_count(root: Mapping[str, Any]) -> int:
    """Return the configured warmup request count; 0 disables warmup."""
    warmup = require_mapping(root.get("warmup", {}), "warmup")
    return require_integer(warmup.get("requests", 0), "warmup.requests", minimum=0)
