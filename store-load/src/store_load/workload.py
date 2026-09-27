from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from store_load.errors import ConfigurationError
from store_load.models import (
    ClientTemplate,
    JsonValue,
    LoadProfile,
    RequestTemplate,
    TargetCatalog,
    TargetConfig,
    WorkloadManifest,
)
from store_load.safety import validate_load_limits

_PLACEHOLDER = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class RenderedRequest:
    name: str
    method: str
    url: str
    auth: str
    headers: Mapping[str, str]
    body: JsonValue


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    target: TargetConfig
    profile: LoadProfile
    manifest: WorkloadManifest

    def as_dict(self) -> dict[str, Any]:
        request_weight = sum(request.weight for request in self.manifest.requests)
        client_weight = sum(client.weight for client in self.manifest.clients)
        return {
            "target": {
                "name": self.target.name,
                "base_url": self.target.base_url,
                "protected": self.target.protected,
            },
            "workload": self.manifest.name,
            "profile": self.profile.name,
            "peak_clients": self.profile.peak_clients,
            "total_duration_seconds": self.profile.total_duration_seconds,
            "estimated_peak_requests_per_second": round(
                self.profile.estimated_peak_requests_per_second, 3
            ),
            "stages": [
                {
                    "name": stage.name,
                    "clients": stage.clients,
                    "duration_seconds": stage.duration_seconds,
                    "spawn_rate": stage.spawn_rate,
                }
                for stage in self.profile.stages
            ],
            "requests": [
                {
                    "name": request.name,
                    "method": request.method,
                    "path": request.path,
                    "auth": request.auth,
                    "weight": request.weight,
                    "share": round(request.weight / request_weight, 6),
                }
                for request in self.manifest.requests
            ],
            "client_templates": [
                {
                    "name": client.name,
                    "weight": client.weight,
                    "share": round(client.weight / client_weight, 6),
                }
                for client in self.manifest.clients
            ],
        }


def _lookup_variable(name: str, variables: Mapping[str, JsonValue], where: str) -> JsonValue:
    try:
        return copy.deepcopy(variables[name])
    except KeyError as exc:
        raise ConfigurationError(f"{where} references unknown variable {name!r}") from exc


def _render_string(
    value: str, variables: Mapping[str, JsonValue], where: str
) -> JsonValue:
    exact_match = _PLACEHOLDER.fullmatch(value)
    if exact_match:
        return _lookup_variable(exact_match.group(1), variables, where)

    def replace(match: re.Match[str]) -> str:
        rendered = _lookup_variable(match.group(1), variables, where)
        if rendered is None or isinstance(rendered, (dict, list)):
            raise ConfigurationError(
                f"{where} cannot embed non-scalar variable {match.group(1)!r} in text"
            )
        return str(rendered)

    return _PLACEHOLDER.sub(replace, value)


def _render_json(
    value: JsonValue, variables: Mapping[str, JsonValue], where: str
) -> JsonValue:
    if isinstance(value, str):
        return _render_string(value, variables, where)
    if isinstance(value, list):
        return [
            _render_json(item, variables, f"{where}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _render_json(item, variables, f"{where}.{key}")
            for key, item in value.items()
        }
    return value


def _render_text(
    value: str, variables: Mapping[str, JsonValue], where: str
) -> str:
    rendered = _render_string(value, variables, where)
    if isinstance(rendered, bool):
        return str(rendered).lower()
    if not isinstance(rendered, (str, int, float)):
        raise ConfigurationError(f"{where} must render to a scalar value")
    return str(rendered)


def _build_url(target: TargetConfig, path: str, where: str) -> str:
    parsed_path = urlsplit(path)
    if (
        not path.startswith("/")
        or parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.fragment
        or ".." in parsed_path.path.split("/")
    ):
        raise ConfigurationError(f"{where} rendered to an unsafe relative URL path")

    url = f"{target.base_url}{path}"
    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname.lower().rstrip(".") if parsed_url.hostname else ""
    if hostname not in target.allowed_hosts:
        raise ConfigurationError(f"{where} rendered outside the target allowlist")
    return url


def render_request(
    target: TargetConfig,
    manifest: WorkloadManifest,
    request: RequestTemplate,
    client: ClientTemplate,
) -> RenderedRequest:
    where = f"request {request.name!r} for client {client.name!r}"
    path = _render_text(request.path, client.variables, f"{where} path")
    headers = {
        name: _render_text(value, client.variables, f"{where} header {name!r}")
        for name, value in {**manifest.default_headers, **request.headers}.items()
    }
    body = _render_json(request.body, client.variables, f"{where} body")
    return RenderedRequest(
        name=request.name,
        method=request.method,
        url=_build_url(target, path, where),
        auth=request.auth,
        headers=headers,
        body=body,
    )


def compile_execution_plan(
    catalog: TargetCatalog,
    target_name: str,
    profile: LoadProfile,
    manifest: WorkloadManifest,
) -> ExecutionPlan:
    target = catalog.get(target_name)
    validate_load_limits(target, profile)

    for client in manifest.clients:
        for request in manifest.requests:
            render_request(target, manifest, request, client)

    return ExecutionPlan(target=target, profile=profile, manifest=manifest)