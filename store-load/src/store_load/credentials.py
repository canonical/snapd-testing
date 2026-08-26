from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field
from pathlib import Path

from store_load.errors import ConfigurationError
from store_load.models import WorkloadManifest


@dataclass(frozen=True, slots=True)
class ClientCredential:
    identifier: str
    device_authorization: str | None = field(default=None, repr=False)
    user_authorization: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ConfigurationError("credential identifier must not be empty")
        for name, value in (
            ("device_authorization", self.device_authorization),
            ("user_authorization", self.user_authorization),
        ):
            if value is not None and (not value or "\r" in value or "\n" in value):
                raise ConfigurationError(
                    f"credential {self.identifier!r} has invalid {name}"
                )


@dataclass(frozen=True, slots=True)
class CredentialPool:
    version: int
    credentials: tuple[ClientCredential, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ConfigurationError(
                f"unsupported credential pool version {self.version}; expected 1"
            )
        identifiers = [credential.identifier for credential in self.credentials]
        if len(identifiers) != len(set(identifiers)):
            raise ConfigurationError("credential identifiers must be unique")


def load_credential_pool(path: str | Path) -> CredentialPool:
    credential_path = Path(path)
    try:
        with credential_path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except OSError as exc:
        raise ConfigurationError("cannot read credential pool") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"invalid credential pool JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigurationError("credential pool must be an object")
    unknown_top_level = sorted(set(data) - {"version", "credentials"})
    if unknown_top_level:
        raise ConfigurationError(
            f"credential pool contains unknown field(s): {unknown_top_level!r}"
        )
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConfigurationError("credential pool version must be an integer")
    raw_credentials = data.get("credentials")
    if not isinstance(raw_credentials, list):
        raise ConfigurationError("credential pool credentials must be an array")

    credentials: list[ClientCredential] = []
    for index, raw_credential in enumerate(raw_credentials):
        if not isinstance(raw_credential, dict):
            raise ConfigurationError(f"credential pool entry {index} must be an object")
        unknown_fields = sorted(
            set(raw_credential)
            - {"id", "device_authorization", "user_authorization"}
        )
        if unknown_fields:
            raise ConfigurationError(
                f"credential pool entry {index} contains unknown field(s): "
                f"{unknown_fields!r}"
            )
        identifier = raw_credential.get("id")
        if not isinstance(identifier, str):
            raise ConfigurationError(f"credential pool entry {index} id must be a string")
        device_authorization = raw_credential.get("device_authorization")
        user_authorization = raw_credential.get("user_authorization")
        if device_authorization is not None and not isinstance(
            device_authorization, str
        ):
            raise ConfigurationError(
                f"credential pool entry {index} device_authorization must be a string"
            )
        if user_authorization is not None and not isinstance(user_authorization, str):
            raise ConfigurationError(
                f"credential pool entry {index} user_authorization must be a string"
            )
        credentials.append(
            ClientCredential(
                identifier=identifier,
                device_authorization=device_authorization,
                user_authorization=user_authorization,
            )
        )

    return CredentialPool(version=version, credentials=tuple(credentials))


def credential_file_is_private(path: str | Path) -> bool:
    try:
        mode = stat.S_IMODE(Path(path).stat().st_mode)
    except OSError:
        return False
    return mode & 0o077 == 0


def validate_credential_capacity(
    pool: CredentialPool, manifest: WorkloadManifest, clients: int
) -> None:
    auth_scopes = {request.auth for request in manifest.requests} - {"none"}
    if not auth_scopes:
        return
    if len(pool.credentials) < clients:
        raise ConfigurationError(
            f"credential pool contains {len(pool.credentials)} client identities; "
            f"the plan requires {clients}"
        )

    for credential in pool.credentials[:clients]:
        if auth_scopes & {"device", "user"} and not credential.device_authorization:
            raise ConfigurationError(
                f"credential {credential.identifier!r} lacks device authorization"
            )
        if "user" in auth_scopes and not credential.user_authorization:
            raise ConfigurationError(
                f"credential {credential.identifier!r} lacks user authorization"
            )
