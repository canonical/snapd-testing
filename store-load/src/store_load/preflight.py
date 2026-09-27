from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from store_load.credentials import (
    credential_file_is_private,
    load_credential_pool,
    validate_credential_capacity,
)
from store_load.errors import ConfigurationError
from store_load.workload import ExecutionPlan, render_request


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, str | bool]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PreflightReport:
    target: str
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "ok": self.ok,
            "checks": [check.as_dict() for check in self.checks],
        }


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _proxy_from_environment(
    plan: ExecutionPlan, environment: Mapping[str, str]
) -> tuple[str | None, CheckResult]:
    proxy_env = plan.target.proxy_env
    if proxy_env is None:
        return None, CheckResult("proxy", True, "direct connection configured")

    proxy_url = environment.get(proxy_env)
    if not proxy_url:
        return None, CheckResult(
            "proxy", False, f"required environment variable {proxy_env} is not set"
        )
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, CheckResult(
            "proxy", False, f"{proxy_env} does not contain a valid HTTP proxy URL"
        )
    return proxy_url, CheckResult("proxy", True, f"configured by {proxy_env}")


def _credential_path(
    plan: ExecutionPlan,
    explicit_path: Path | None,
    environment: Mapping[str, str],
) -> Path | None:
    if explicit_path is not None:
        return explicit_path
    if plan.target.credentials_env is None:
        return None
    value = environment.get(plan.target.credentials_env)
    return Path(value) if value else None


def _check_credentials(
    plan: ExecutionPlan,
    explicit_path: Path | None,
    environment: Mapping[str, str],
) -> CheckResult:
    auth_scopes = {request.auth for request in plan.manifest.requests} - {"none"}
    if not auth_scopes:
        return CheckResult("credentials", True, "workload does not require credentials")

    path = _credential_path(plan, explicit_path, environment)
    if path is None:
        source = plan.target.credentials_env or "--credentials"
        return CheckResult("credentials", False, f"credential path is not set by {source}")
    if not credential_file_is_private(path):
        return CheckResult(
            "credentials",
            False,
            "credential file is missing or accessible by group/other users",
        )
    try:
        pool = load_credential_pool(path)
        validate_credential_capacity(
            pool, plan.manifest, plan.profile.peak_clients
        )
    except ConfigurationError as exc:
        return CheckResult("credentials", False, str(exc))
    return CheckResult(
        "credentials",
        True,
        f"{len(pool.credentials)} distinct client identities available",
    )


def _check_templates(plan: ExecutionPlan) -> CheckResult:
    rendered = 0
    for client in plan.manifest.clients:
        for request in plan.manifest.requests:
            render_request(plan.target, plan.manifest, request, client)
            rendered += 1
    return CheckResult(
        "templates", True, f"rendered {rendered} request/client combinations"
    )


def _probe_target(
    plan: ExecutionPlan, proxy_url: str | None, timeout_seconds: float
) -> CheckResult:
    proxy_map = {} if proxy_url is None else {"http": proxy_url, "https": proxy_url}
    opener = build_opener(ProxyHandler(proxy_map), _NoRedirectHandler())
    request = Request(
        f"{plan.target.base_url}{plan.target.probe_path}",
        method="HEAD",
        headers={"User-Agent": "store-load-preflight/0.1"},
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (OSError, URLError) as exc:
        return CheckResult(
            "network",
            False,
            f"connection failed ({type(exc).__name__})",
        )
    return CheckResult("network", True, f"target responded with HTTP {status}")


def run_preflight(
    plan: ExecutionPlan,
    *,
    credentials_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    check_network: bool = True,
    timeout_seconds: float = 5.0,
) -> PreflightReport:
    if timeout_seconds <= 0 or timeout_seconds > 30:
        raise ConfigurationError("preflight timeout must be greater than 0 and at most 30")

    runtime_environment = os.environ if environment is None else environment
    checks = [_check_templates(plan)]
    proxy_url, proxy_check = _proxy_from_environment(plan, runtime_environment)
    checks.append(proxy_check)
    checks.append(_check_credentials(plan, credentials_path, runtime_environment))
    if check_network:
        if proxy_check.ok:
            checks.append(_probe_target(plan, proxy_url, timeout_seconds))
        else:
            checks.append(
                CheckResult("network", False, "not attempted because proxy check failed")
            )
    else:
        checks.append(CheckResult("network", True, "network probe skipped"))

    return PreflightReport(target=plan.target.name, checks=tuple(checks))