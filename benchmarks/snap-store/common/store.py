"""Store info lookup and channel revision selection."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
import json

from common.errors import BenchmarkError
from common.http import http_get
from common.profile import parse_channel


def fetch_store_info(
    store_url: str,
    snap_name: str,
    headers: Sequence[tuple[str, str]],
    timeout: float,
    *,
    verify_tls: bool = True,
) -> Mapping[str, Any]:
    """Fetch and parse the Store info document for a snap."""
    status, _, body = http_get(
        f"{store_url}/v2/snaps/info/{snap_name}", headers, timeout,
        verify_tls=verify_tls,
    )
    if status != 200:
        raise BenchmarkError(
            f"Store info request for {snap_name} returned HTTP {status}"
        )
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise BenchmarkError(
            f"Store info response is not valid JSON: {error}"
        ) from error
    if not isinstance(document, Mapping):
        raise BenchmarkError("Store info response is not a JSON object")
    return document


def snap_id_from_info(info: Mapping[str, Any]) -> str:
    """Extract the snap-id from a Store info document."""
    snap_id = info.get("snap-id")
    snap = info.get("snap")
    if not isinstance(snap_id, str) and isinstance(snap, Mapping):
        snap_id = snap.get("snap-id") or snap.get("id")
    if not isinstance(snap_id, str) or not snap_id:
        raise BenchmarkError("Store info response has no snap-id")
    return snap_id


def find_channel_map_entry(
    info: Mapping[str, Any], snap_name: str, channel: str, architecture: str
) -> Mapping[str, Any]:
    """Return the channel-map entry matching channel and architecture."""
    channel_map = info.get("channel-map")
    if not isinstance(channel_map, list):
        raise BenchmarkError("Store info response has no channel map")
    track, risk, branch = parse_channel(channel)
    matches = []
    available = []
    for entry in channel_map:
        entry_channel = entry.get("channel")
        if not isinstance(entry_channel, Mapping):
            continue
        available.append(
            f"{entry_channel.get('name', '?')} ({entry_channel.get('architecture', '?')})"
        )
        if (
            entry_channel.get("track", "latest") == track
            and entry_channel.get("risk") == risk
            and entry_channel.get("branch") == branch
            and entry_channel.get("architecture") == architecture
        ):
            matches.append(entry)
    if not matches:
        raise BenchmarkError(
            f"no {channel} revision for {snap_name} on {architecture}; "
            f"available: {', '.join(sorted(available))}"
        )
    return matches[0]
