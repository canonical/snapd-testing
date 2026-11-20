"""Refresh request capture, generation, Lua rendering, and preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import urllib.request

from common.errors import BenchmarkError
from common.http import http_opener
from common.profile import wrk_time_seconds
from common.store import fetch_store_info
from common.store import find_channel_map_entry
from common.store import snap_id_from_info
from common.wrk import lua_long_string
from common.wrk import render_response_validation
from common.wrk import run_warmup
from latency.profile import BenchmarkProfile


MANAGED_HEADERS = {"connection", "content-length", "host", "transfer-encoding"}


HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


HTTP_VERSION_RE = re.compile(r"^HTTP/[0-9]+\.[0-9]+$")


SNAPD_USER_AGENT = "snapd/2.76.3 (series 16) ubuntu-core/26 (amd64)"


REFRESH_FIELDS = (
    "architectures",
    "base",
    "confinement",
    "links",
    "contact",
    "created-at",
    "description",
    "download",
    "epoch",
    "license",
    "name",
    "prices",
    "private",
    "publisher",
    "revision",
    "snap-id",
    "snap-yaml",
    "summary",
    "title",
    "type",
    "version",
    "website",
    "store-url",
    "integrity",
    "media",
    "common-ids",
    "categories",
)


class RequestCaptureError(BenchmarkError):
    """Raised when a captured HTTP request cannot be replayed."""


@dataclass(frozen=True)
class CapturedRequest:
    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: str

    @property
    def replay_headers(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, value)
            for name, value in self.headers
            if name.lower() not in MANAGED_HEADERS
        )


def parse_request_capture(request_file: Path) -> CapturedRequest:
    """Parse a raw HTTP request captured in a text file."""
    try:
        with request_file.open("r", encoding="utf-8", newline=None) as stream:
            raw_request = stream.read()
    except OSError as error:
        raise RequestCaptureError(
            f"cannot read request capture {request_file}: {error}"
        ) from error

    header_block, separator, body = raw_request.partition("\n\n")
    if not separator:
        raise RequestCaptureError("request capture has no blank line before its body")

    header_lines = header_block.splitlines()
    if not header_lines:
        raise RequestCaptureError("request capture is empty")

    request_line = header_lines[0].split()
    header_start = 1
    if (
        len(request_line) == 2
        and len(header_lines) > 1
        and HTTP_VERSION_RE.fullmatch(header_lines[1])
    ):
        request_line.append(header_lines[1])
        header_start = 2
    if len(request_line) not in {2, 3}:
        raise RequestCaptureError(
            "request line must contain METHOD PATH and optional HTTP version"
        )
    method, path = request_line[:2]
    if not HTTP_TOKEN_RE.fullmatch(method):
        raise RequestCaptureError(f"invalid HTTP method in request capture: {method!r}")
    if not path.startswith("/"):
        raise RequestCaptureError("captured request path must start with /")
    if len(request_line) == 3 and not HTTP_VERSION_RE.fullmatch(request_line[2]):
        raise RequestCaptureError(
            f"invalid HTTP version in request capture: {request_line[2]!r}"
        )

    headers: list[tuple[str, str]] = []
    seen_headers: set[str] = set()
    for line in header_lines[header_start:]:
        name, separator, value = line.partition(":")
        name = name.strip()
        if not separator or not HTTP_TOKEN_RE.fullmatch(name):
            raise RequestCaptureError(f"invalid HTTP header line: {line!r}")
        normalized_name = name.lower()
        if normalized_name in seen_headers:
            raise RequestCaptureError(
                f"duplicate HTTP header is not supported: {name}"
            )
        seen_headers.add(normalized_name)
        headers.append((name, value.strip()))

    if not body:
        raise RequestCaptureError("request capture has an empty body")

    content_type = next(
        (value for name, value in headers if name.lower() == "content-type"), ""
    )
    if content_type.lower().startswith("application/json"):
        try:
            json.loads(body)
        except json.JSONDecodeError as error:
            raise RequestCaptureError(
                f"captured request body is not valid JSON: {error}"
            ) from error

    return CapturedRequest(
        method=method,
        path=path,
        headers=tuple(headers),
        body=body,
    )


def render_lua_script(request: CapturedRequest, *, expected_status: int) -> str:
    """Render the static wrk2 request script for a captured request."""
    lines = [
        f"wrk.method = {lua_long_string(request.method)}",
        f"wrk.body = {lua_long_string(request.body)}",
    ]
    for name, value in request.replay_headers:
        lines.append(
            f"wrk.headers[ {lua_long_string(name)} ] = {lua_long_string(value)}"
        )
    return "\n".join(lines) + "\n" + render_response_validation(expected_status)


def read_authorization(auth_file: Path) -> str:
    """Read the device authorization header value for a generated request."""
    try:
        token = auth_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BenchmarkError(
            f"cannot read device authorization {auth_file}: {error}"
        ) from error
    if not token.startswith("Macaroon ") or "\n" in token:
        raise BenchmarkError(
            f"{auth_file} must contain a single Macaroon authorization value"
        )
    return token


def generated_headers(profile: BenchmarkProfile) -> tuple[tuple[str, str], ...]:
    """Return the snapd-style headers used for a generated refresh request."""
    return (
        ("Accept", "application/json"),
        ("Content-Type", "application/json"),
        ("Snap-Accept-Delta-Format", "xdelta3"),
        ("Snap-Classic", "false"),
        ("Snap-Device-Architecture", profile.architecture),
        ("Snap-Device-Capabilities", "default-tracks"),
        ("Snap-Device-Series", "16"),
        ("User-Agent", SNAPD_USER_AGENT),
    )


def render_refresh_body(snap_id: str, revision: int, channel: str) -> str:
    """Render a snapd-style refresh request body for one installed snap."""
    refreshed_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "context": [
            {
                "snap-id": snap_id,
                "instance-key": snap_id,
                "revision": revision,
                "tracking-channel": channel,
                "epoch": {"read": [0], "write": [0]},
                "refreshed-date": refreshed_date,
            }
        ],
        "actions": [
            {
                "action": "refresh",
                "instance-key": snap_id,
                "snap-id": snap_id,
                "channel": channel,
                "ignore-validation": False,
            }
        ],
        "fields": list(REFRESH_FIELDS),
    }
    return json.dumps(document, indent=2)


def generate_refresh_request(profile: BenchmarkProfile) -> CapturedRequest:
    """Build a refresh request for the configured snap via the Store info API."""
    if profile.snap_name is None or profile.auth_file is None:
        raise BenchmarkError("profile does not describe a generated request")
    authorization = read_authorization(profile.auth_file)
    base_headers = generated_headers(profile)
    # The info API is public metadata; attaching a device macaroon makes it
    # fail with HTTP 401, so the lookup sends no authorization.
    info_headers = tuple(
        pair for pair in base_headers if pair[0].lower() != "content-type"
    )
    info = fetch_store_info(
        profile.store_url,
        profile.snap_name,
        info_headers,
        wrk_time_seconds(profile.timeout),
        verify_tls=profile.verify_tls,
    )
    snap_id = snap_id_from_info(info)
    revision = profile.revision
    if revision is None:
        entry = find_channel_map_entry(
            info, profile.snap_name, profile.channel, profile.architecture
        )
        revision_value = entry.get("revision")
        if not isinstance(revision_value, int):
            raise BenchmarkError("channel map entry has no revision")
        revision = revision_value
    print(
        f"Resolved {profile.snap_name} {profile.channel} "
        f"({profile.architecture}) to revision {revision}."
    )
    return CapturedRequest(
        method="POST",
        path=urlparse(profile.url).path or "/",
        headers=base_headers + (("Snap-Device-Authorization", authorization),),
        body=render_refresh_body(snap_id, revision, profile.channel),
    )


def build_request(profile: BenchmarkProfile) -> CapturedRequest:
    """Return the captured request, or generate one for the configured snap."""
    if profile.request_file is not None:
        return parse_request_capture(profile.request_file)
    print(
        f"Generating a refresh request for {profile.snap_name} "
        f"{profile.channel} ({profile.architecture}) ..."
    )
    return generate_refresh_request(profile)


def send_request(
    profile: BenchmarkProfile, request: CapturedRequest
) -> tuple[int, int]:
    """Send the captured request once; return its status and body size."""
    urllib_request = urllib.request.Request(
        profile.url,
        data=request.body.encode("utf-8"),
        headers=dict(request.replay_headers),
        method=request.method,
    )
    opener = http_opener(verify_tls=profile.verify_tls)
    try:
        with opener.open(
            urllib_request, timeout=wrk_time_seconds(profile.timeout)
        ) as response:
            return response.status, len(response.read())
    except urllib.error.HTTPError as error:
        return error.code, len(error.read())
    except urllib.error.URLError as error:
        raise BenchmarkError(f"request failed: {error.reason}") from error
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error


def run_preflight(profile: BenchmarkProfile, request: CapturedRequest) -> None:
    """Warm the Store, then send one request and verify its status."""
    if profile.warmup_requests:
        print(f"Warming up with {profile.warmup_requests} requests ...")
        run_warmup(profile.warmup_requests, lambda: send_request(profile, request))

    status, _ = send_request(profile, request)
    if status != profile.expected_status:
        raise BenchmarkError(
            f"preflight returned HTTP {status}; expected {profile.expected_status}"
        )
    print(f"Preflight returned HTTP {status} (redirects disabled).")
