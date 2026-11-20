"""Download URL resolution, headers, preflight, and warmup requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin

from common.http import http_get
from common.profile import wrk_time_seconds
from common.store import fetch_store_info
from common.store import find_channel_map_entry
from download.errors import DownloadBenchmarkError
from download.profile import DownloadProfile
from download.profile import REDIRECT_STATUSES


def download_request_headers(profile: DownloadProfile) -> tuple[tuple[str, str], ...]:
    headers = list(profile.download_headers)
    if profile.byte_range is not None:
        start, end = profile.byte_range
        headers.append(("Range", f"bytes={start}-{end}"))
    return tuple(headers)


def resolve_download_url(profile: DownloadProfile, start_url: str) -> str:
    """Resolve Store and CDN redirects while keeping Store headers off the CDN."""
    timeout = wrk_time_seconds(profile.timeout)
    status, headers, _ = http_get(
        start_url, profile.store_headers, timeout, verify_tls=profile.verify_tls
    )
    if status in REDIRECT_STATUSES:
        if status != profile.expected_redirect_status:
            raise DownloadBenchmarkError(
                f"Store returned HTTP {status}; expected redirect "
                f"{profile.expected_redirect_status}"
            )
        location = headers.get("Location")
        if not location:
            raise DownloadBenchmarkError(
                "Store redirect did not include a Location header"
            )
        current_url = urljoin(start_url, location)
    elif status == 200:
        # The Store serves the blob itself (for example an enterprise Store);
        # there is no CDN redirect to follow. The loop below re-fetches with
        # the download headers and verifies status and body size.
        current_url = start_url
    else:
        raise DownloadBenchmarkError(
            f"Store returned HTTP {status}; expected redirect "
            f"{profile.expected_redirect_status} or direct download 200"
        )

    request_headers = download_request_headers(profile)
    for _ in range(profile.max_redirects):
        status, headers, body = http_get(
            current_url, request_headers, timeout, verify_tls=profile.verify_tls
        )
        if status in REDIRECT_STATUSES:
            location = headers.get("Location")
            if not location:
                raise DownloadBenchmarkError(
                    f"CDN returned HTTP {status} without a Location header"
                )
            current_url = urljoin(current_url, location)
            continue

        if status != profile.expected_download_status:
            raise DownloadBenchmarkError(
                f"CDN returned HTTP {status}; expected "
                f"{profile.expected_download_status}"
            )
        if len(body) != profile.response_size_bytes:
            raise DownloadBenchmarkError(
                f"CDN returned {len(body)} bytes; expected "
                f"{profile.response_size_bytes}"
            )
        return current_url

    raise DownloadBenchmarkError(
        f"download exceeded the limit of {profile.max_redirects} CDN redirects"
    )


@dataclass(frozen=True)
class ResolvedDownload:
    url: str
    revision: int | None
    size_bytes: int


def resolve_snap_download_url(profile: DownloadProfile) -> ResolvedDownload:
    """Find the snap's current download URL through the Store info API."""
    info = fetch_store_info(
        profile.store_url,
        profile.snap_name,
        profile.store_headers,
        wrk_time_seconds(profile.timeout),
        verify_tls=profile.verify_tls,
    )
    entry = find_channel_map_entry(
        info, profile.snap_name, profile.channel, profile.architecture
    )
    download = entry.get("download")
    if not isinstance(download, Mapping):
        raise DownloadBenchmarkError("channel map entry has no download section")
    url = download.get("url")
    size = download.get("size")
    if not isinstance(url, str) or not url.strip():
        raise DownloadBenchmarkError("channel map entry has no download URL")
    if size != profile.expected_size_bytes:
        raise DownloadBenchmarkError(
            f"Store advertises {size} bytes for {profile.snap_name} "
            f"{profile.channel} ({profile.architecture}), but "
            f"target.expected_size_bytes is {profile.expected_size_bytes}; "
            "update the profile"
        )
    revision = entry.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise DownloadBenchmarkError("channel map entry has no valid revision")
    return ResolvedDownload(urljoin(profile.store_url, url), revision, profile.expected_size_bytes)


def send_download_once(profile: DownloadProfile, url: str) -> tuple[int, int]:
    """Download the payload once; return its status and body size."""
    status, _, body = http_get(
        url,
        download_request_headers(profile),
        wrk_time_seconds(profile.timeout),
        verify_tls=profile.verify_tls,
    )
    return status, len(body)
