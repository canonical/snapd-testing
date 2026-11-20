"""HTTP transport with explicit TLS and redirect handling."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
import ssl
import urllib.request

from common.errors import BenchmarkError


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from following redirects during preflight checks."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def http_opener(*, verify_tls: bool = True) -> urllib.request.OpenerDirector:
    """Build an opener that never follows redirects.

    With verify_tls false the opener accepts any certificate, for internal
    test Stores whose CA is not trusted by the client.
    """
    handlers: list[Any] = [NoRedirectHandler()]
    if not verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def http_get(
    url: str,
    headers: Sequence[tuple[str, str]],
    timeout: float,
    *,
    verify_tls: bool = True,
) -> tuple[int, Mapping[str, str], bytes]:
    """Send one GET request without following redirects."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    opener = http_opener(verify_tls=verify_tls)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except urllib.error.URLError as error:
        raise BenchmarkError(f"request failed: {error.reason}") from error
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error

    try:
        return response.status, response.headers, response.read()
    except OSError as error:
        raise BenchmarkError(f"request failed: {error}") from error
    finally:
        response.close()
