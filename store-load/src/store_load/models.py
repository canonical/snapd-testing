from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias, Union
from urllib.parse import urlsplit

from store_load.errors import ConfigurationError

JsonValue: TypeAlias = Union[
    None,
    bool,
    int,
    float,
    str,
    list["JsonValue"],
    dict[str, "JsonValue"],
]

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "snap-device-authorization",
        "x-device-authorization",
    }
)
SUPPORTED_AUTH_SCOPES = frozenset({"none", "device", "user"})
SUPPORTED_METHODS = frozenset({"GET", "HEAD", "POST"})
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VARIABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _validated_headers(headers: Mapping[str, str], where: str) -> Mapping[str, str]:
    copied: dict[str, str] = {}
    for name, value in headers.items():
        normalized_name = name.strip()
        if not normalized_name:
            raise ConfigurationError(f"{where} contains an empty header name")
        if normalized_name.lower() in SENSITIVE_HEADERS:
            raise ConfigurationError(
                f"{where} must not contain sensitive header {normalized_name!r}"
            )
        if "\r" in value or "\n" in value:
            raise ConfigurationError(
                f"{where}.{normalized_name} contains a newline"
            )
        copied[normalized_name] = value
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ClientTemplate:
    name: str
    weight: int = 1
    variables: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("client template name must not be empty")
        if self.weight < 1:
            raise ConfigurationError(f"client {self.name!r} weight must be at least 1")
        copied_variables: dict[str, JsonValue] = {}
        for name, value in self.variables.items():
            if not _VARIABLE_NAME.fullmatch(name):
                raise ConfigurationError(
                    f"client {self.name!r} has invalid variable name {name!r}"
                )
            copied_variables[name] = value
        object.__setattr__(self, "variables", MappingProxyType(copied_variables))


@dataclass(frozen=True, slots=True)
class RequestTemplate:
    name: str
    method: str
    path: str
    weight: int = 1
    auth: str = "none"
    headers: Mapping[str, str] = field(default_factory=dict)
    body: JsonValue = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("request template name must not be empty")

        method = self.method.upper()
        if method not in SUPPORTED_METHODS:
            raise ConfigurationError(
                f"request {self.name!r} uses unsupported method {self.method!r}"
            )
        object.__setattr__(self, "method", method)

        parsed_path = urlsplit(self.path)
        if (
            not self.path.startswith("/")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.fragment
            or ".." in parsed_path.path.split("/")
        ):
            raise ConfigurationError(
                f"request {self.name!r} path must be a safe relative URL path"
            )
        if self.weight < 1:
            raise ConfigurationError(
                f"request {self.name!r} weight must be at least 1"
            )
        if self.auth not in SUPPORTED_AUTH_SCOPES:
            raise ConfigurationError(
                f"request {self.name!r} has unsupported auth scope {self.auth!r}"
            )
        if method in {"GET", "HEAD"} and self.body is not None:
            raise ConfigurationError(
                f"request {self.name!r} must not define a body for {method}"
            )
        object.__setattr__(
            self,
            "headers",
            _validated_headers(self.headers, f"request {self.name!r} headers"),
        )


@dataclass(frozen=True, slots=True)
class WorkloadManifest:
    schema_version: int
    name: str
    requests: tuple[RequestTemplate, ...]
    clients: tuple[ClientTemplate, ...]
    default_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigurationError(
                f"unsupported workload schema version {self.schema_version}; expected 1"
            )
        if not self.name.strip():
            raise ConfigurationError("workload name must not be empty")
        if not self.requests:
            raise ConfigurationError("workload must define at least one request")
        if not self.clients:
            raise ConfigurationError("workload must define at least one client template")

        request_names = [request.name for request in self.requests]
        if len(request_names) != len(set(request_names)):
            raise ConfigurationError("workload request names must be unique")
        client_names = [client.name for client in self.clients]
        if len(client_names) != len(set(client_names)):
            raise ConfigurationError("workload client names must be unique")
        object.__setattr__(
            self,
            "default_headers",
            _validated_headers(self.default_headers, "default_headers"),
        )


@dataclass(frozen=True, slots=True)
class TargetConfig:
    name: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    protected: bool
    max_clients: int
    max_duration_seconds: int
    max_requests_per_second: float
    proxy_env: str | None = None
    credentials_env: str | None = None
    probe_path: str = "/"
    allow_http: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("target name must not be empty")
        parsed = urlsplit(self.base_url)
        allowed_schemes = {"https", "http"} if self.allow_http else {"https"}
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ConfigurationError(
                f"target {self.name!r} must use an allowed absolute URL"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigurationError(
                f"target {self.name!r} base_url must not contain credentials, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise ConfigurationError(
                f"target {self.name!r} base_url must not contain a path"
            )

        normalized_hosts = tuple(host.lower().rstrip(".") for host in self.allowed_hosts)
        if not normalized_hosts:
            raise ConfigurationError(
                f"target {self.name!r} must define at least one allowed host"
            )
        if parsed.hostname.lower().rstrip(".") not in normalized_hosts:
            raise ConfigurationError(
                f"target {self.name!r} host is not present in allowed_hosts"
            )
        if self.max_clients < 1:
            raise ConfigurationError(
                f"target {self.name!r} max_clients must be at least 1"
            )
        if self.max_duration_seconds < 1:
            raise ConfigurationError(
                f"target {self.name!r} max_duration_seconds must be at least 1"
            )
        if self.max_requests_per_second <= 0:
            raise ConfigurationError(
                f"target {self.name!r} max_requests_per_second must be positive"
            )
        if self.proxy_env is not None and not _ENVIRONMENT_NAME.fullmatch(self.proxy_env):
            raise ConfigurationError(
                f"target {self.name!r} proxy_env is not a valid environment variable"
            )
        if self.credentials_env is not None and not _ENVIRONMENT_NAME.fullmatch(
            self.credentials_env
        ):
            raise ConfigurationError(
                f"target {self.name!r} credentials_env is not a valid environment variable"
            )
        probe = urlsplit(self.probe_path)
        if (
            not self.probe_path.startswith("/")
            or probe.scheme
            or probe.netloc
            or probe.fragment
            or ".." in probe.path.split("/")
        ):
            raise ConfigurationError(
                f"target {self.name!r} probe_path must be a safe relative URL path"
            )

        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "allowed_hosts", normalized_hosts)


@dataclass(frozen=True, slots=True)
class TargetCatalog:
    version: int
    targets: Mapping[str, TargetConfig]

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ConfigurationError(
                f"unsupported target catalog version {self.version}; expected 1"
            )
        if not self.targets:
            raise ConfigurationError("target catalog must define at least one target")
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))

    def get(self, name: str) -> TargetConfig:
        try:
            return self.targets[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.targets))
            raise ConfigurationError(
                f"unknown target {name!r}; available targets: {available}"
            ) from exc


@dataclass(frozen=True, slots=True)
class Stage:
    name: str
    clients: int
    duration_seconds: int
    spawn_rate: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigurationError("stage name must not be empty")
        if self.clients < 1:
            raise ConfigurationError(f"stage {self.name!r} clients must be at least 1")
        if self.duration_seconds < 1:
            raise ConfigurationError(
                f"stage {self.name!r} duration_seconds must be at least 1"
            )
        if self.spawn_rate <= 0:
            raise ConfigurationError(
                f"stage {self.name!r} spawn_rate must be positive"
            )


@dataclass(frozen=True, slots=True)
class LoadProfile:
    version: int
    name: str
    transactions_per_client_per_minute: float
    jitter_fraction: float
    stages: tuple[Stage, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ConfigurationError(
                f"unsupported profile version {self.version}; expected 1"
            )
        if not self.name.strip():
            raise ConfigurationError("profile name must not be empty")
        if self.transactions_per_client_per_minute <= 0:
            raise ConfigurationError(
                "transactions_per_client_per_minute must be positive"
            )
        if not 0 <= self.jitter_fraction < 1:
            raise ConfigurationError("jitter_fraction must be at least 0 and less than 1")
        if not self.stages:
            raise ConfigurationError("profile must define at least one stage")
        stage_names = [stage.name for stage in self.stages]
        if len(stage_names) != len(set(stage_names)):
            raise ConfigurationError("profile stage names must be unique")

    @property
    def peak_clients(self) -> int:
        return max(stage.clients for stage in self.stages)

    @property
    def total_duration_seconds(self) -> int:
        return sum(stage.duration_seconds for stage in self.stages)

    @property
    def estimated_peak_requests_per_second(self) -> float:
        return (
            self.peak_clients * self.transactions_per_client_per_minute / 60.0
        )