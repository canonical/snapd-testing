from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from store_load.errors import ConfigurationError
from store_load.models import (
    ClientTemplate,
    LoadProfile,
    RequestTemplate,
    Stage,
    TargetCatalog,
    TargetConfig,
    WorkloadManifest,
)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"invalid JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    return _mapping(value, str(path))


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc
    return _mapping(value, str(path))


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ConfigurationError(f"{where} must be an object")
    return value


def _reject_unknown(
    value: Mapping[str, Any], allowed: set[str], where: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        rendered = ", ".join(repr(key) for key in unknown)
        raise ConfigurationError(f"{where} contains unknown field(s): {rendered}")


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{where} must be an array")
    return value


def _string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{where} must be a string")
    return value


def _integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{where} must be an integer")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{where} must be a number")
    return float(value)


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{where} must be a boolean")
    return value


def _headers(value: object, where: str) -> dict[str, str]:
    mapping = _mapping(value, where)
    return {
        _string(name, f"{where} header name"): _string(
            header_value, f"{where}.{name}"
        )
        for name, header_value in mapping.items()
    }


def load_manifest(path: str | Path) -> WorkloadManifest:
    manifest_path = Path(path)
    data = _read_json(manifest_path)
    where = str(manifest_path)
    _reject_unknown(
        data,
        {"$schema", "schema_version", "name", "default_headers", "clients", "requests"},
        where,
    )

    requests: list[RequestTemplate] = []
    for index, raw_request in enumerate(
        _list(data.get("requests"), f"{where}.requests")
    ):
        request_where = f"{where}.requests[{index}]"
        request = _mapping(raw_request, request_where)
        _reject_unknown(
            request,
            {"name", "method", "path", "weight", "auth", "headers", "body"},
            request_where,
        )
        requests.append(
            RequestTemplate(
                name=_string(request.get("name"), f"{request_where}.name"),
                method=_string(request.get("method"), f"{request_where}.method"),
                path=_string(request.get("path"), f"{request_where}.path"),
                weight=_integer(request.get("weight", 1), f"{request_where}.weight"),
                auth=_string(request.get("auth", "none"), f"{request_where}.auth"),
                headers=_headers(
                    request.get("headers", {}), f"{request_where}.headers"
                ),
                body=request.get("body"),
            )
        )

    clients: list[ClientTemplate] = []
    raw_clients = data.get(
        "clients", [{"name": "default", "weight": 1, "variables": {}}]
    )
    for index, raw_client in enumerate(_list(raw_clients, f"{where}.clients")):
        client_where = f"{where}.clients[{index}]"
        client = _mapping(raw_client, client_where)
        _reject_unknown(client, {"name", "weight", "variables"}, client_where)
        clients.append(
            ClientTemplate(
                name=_string(client.get("name"), f"{client_where}.name"),
                weight=_integer(client.get("weight", 1), f"{client_where}.weight"),
                variables=_mapping(
                    client.get("variables", {}), f"{client_where}.variables"
                ),
            )
        )

    return WorkloadManifest(
        schema_version=_integer(
            data.get("schema_version"), f"{where}.schema_version"
        ),
        name=_string(data.get("name"), f"{where}.name"),
        default_headers=_headers(
            data.get("default_headers", {}), f"{where}.default_headers"
        ),
        requests=tuple(requests),
        clients=tuple(clients),
    )


def load_target_catalog(path: str | Path) -> TargetCatalog:
    catalog_path = Path(path)
    data = _read_toml(catalog_path)
    where = str(catalog_path)
    _reject_unknown(data, {"version", "targets"}, where)
    target_table = _mapping(data.get("targets"), f"{where}.targets")
    targets: dict[str, TargetConfig] = {}

    for name, raw_target in target_table.items():
        target_where = f"{where}.targets.{name}"
        target = _mapping(raw_target, target_where)
        _reject_unknown(
            target,
            {
                "base_url",
                "allowed_hosts",
                "protected",
                "max_clients",
                "max_duration_seconds",
                "max_requests_per_second",
                "proxy_env",
                "credentials_env",
                "probe_path",
                "allow_http",
            },
            target_where,
        )
        allowed_hosts = tuple(
            _string(host, f"{target_where}.allowed_hosts[{index}]")
            for index, host in enumerate(
                _list(target.get("allowed_hosts"), f"{target_where}.allowed_hosts")
            )
        )
        raw_proxy_env = target.get("proxy_env")
        proxy_env = (
            None
            if raw_proxy_env is None
            else _string(raw_proxy_env, f"{target_where}.proxy_env")
        )
        raw_credentials_env = target.get("credentials_env")
        credentials_env = (
            None
            if raw_credentials_env is None
            else _string(raw_credentials_env, f"{target_where}.credentials_env")
        )
        targets[name] = TargetConfig(
            name=name,
            base_url=_string(target.get("base_url"), f"{target_where}.base_url"),
            allowed_hosts=allowed_hosts,
            protected=_boolean(
                target.get("protected", True), f"{target_where}.protected"
            ),
            max_clients=_integer(
                target.get("max_clients"), f"{target_where}.max_clients"
            ),
            max_duration_seconds=_integer(
                target.get("max_duration_seconds"),
                f"{target_where}.max_duration_seconds",
            ),
            max_requests_per_second=_number(
                target.get("max_requests_per_second"),
                f"{target_where}.max_requests_per_second",
            ),
            proxy_env=proxy_env,
            credentials_env=credentials_env,
            probe_path=_string(
                target.get("probe_path", "/"), f"{target_where}.probe_path"
            ),
            allow_http=_boolean(
                target.get("allow_http", False), f"{target_where}.allow_http"
            ),
        )

    return TargetCatalog(
        version=_integer(data.get("version"), f"{where}.version"),
        targets=targets,
    )


def load_profile(path: str | Path) -> LoadProfile:
    profile_path = Path(path)
    data = _read_toml(profile_path)
    where = str(profile_path)
    _reject_unknown(
        data,
        {
            "version",
            "name",
            "description",
            "transactions_per_client_per_minute",
            "jitter_fraction",
            "stages",
        },
        where,
    )
    stages: list[Stage] = []

    for index, raw_stage in enumerate(_list(data.get("stages"), f"{where}.stages")):
        stage_where = f"{where}.stages[{index}]"
        stage = _mapping(raw_stage, stage_where)
        _reject_unknown(
            stage,
            {"name", "clients", "duration_seconds", "spawn_rate"},
            stage_where,
        )
        stages.append(
            Stage(
                name=_string(stage.get("name"), f"{stage_where}.name"),
                clients=_integer(stage.get("clients"), f"{stage_where}.clients"),
                duration_seconds=_integer(
                    stage.get("duration_seconds"), f"{stage_where}.duration_seconds"
                ),
                spawn_rate=_number(
                    stage.get("spawn_rate"), f"{stage_where}.spawn_rate"
                ),
            )
        )

    return LoadProfile(
        version=_integer(data.get("version"), f"{where}.version"),
        name=_string(data.get("name"), f"{where}.name"),
        description=_string(data.get("description", ""), f"{where}.description"),
        transactions_per_client_per_minute=_number(
            data.get("transactions_per_client_per_minute"),
            f"{where}.transactions_per_client_per_minute",
        ),
        jitter_fraction=_number(
            data.get("jitter_fraction", 0), f"{where}.jitter_fraction"
        ),
        stages=tuple(stages),
    )