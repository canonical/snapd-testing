#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Machine charm for the spread-test-selector service."""

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

from ops.charm import ActionEvent, CharmBase, SecretChangedEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from refresh_spread_tests_execution import FEATURES_SUBDIR, fetch_snapd_features

logger = logging.getLogger(__name__)


class SpreadTestSelectorCharm(CharmBase):
    """Machine charm that exposes API for test selection and map refresh."""

    SERVICE_NAME = "spread-test-selector-api"
    SYSTEMD_DIR = Path("/etc/systemd/system")
    STATE_DIR = Path("/var/lib/spread-test-selector")
    SERVICE_CONFIG_PATH = STATE_DIR / "service-config.json"
    MONGODB_CREDENTIALS_PATH = STATE_DIR / "mongodb-credentials.json"
    SERVICE_STATE_MARKER_PATH = STATE_DIR / "service-state.marker"
    # sparse checkout of snapd with only the features folder
    SNAPD_DIR = STATE_DIR / "snapd"
    FEATURES_SUBDIR = FEATURES_SUBDIR
    # virtualenv the refresh helper and query_features.py run in
    VENV_DIR = STATE_DIR / "venv"
    APT_PACKAGES = ("git", "python3-venv")
    # query_features.py only needs pymongo (requirements.txt there also lists
    # the dashboard's packages); it uses Cursor.to_list(), added in pymongo 4.9
    VENV_PACKAGES = ("pymongo>=4.9",)
    PYMONGO_MIN_VERSION = (4, 9)
    # extra time, beyond the refresh timeout, to wait for the service to answer
    REFRESH_RESPONSE_MARGIN_SECONDS = 60
    ETC_ENVIRONMENT_PATH = Path("/etc/environment")
    # always reached directly: the local API service, and the snapd
    # infrastructure such as mongodb.snapd.canonical.com
    ALWAYS_NO_PROXY = ("127.0.0.1", "localhost", ".snapd.canonical.com")

    def __init__(self, *args):
        super().__init__(*args)
        # every hook and action, and the commands they run (apt, pip, git),
        # use the configured proxy
        self._set_runtime_proxy_env()
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)

        self.framework.observe(self.on.calculate_tests_action, self._on_calculate_tests_action)
        self.framework.observe(self.on.mongodb_updated_action, self._on_mongodb_updated_action)
        self.framework.observe(self.on.reconcile_action, self._on_reconcile_action)
        self.framework.observe(self.on.service_status_action, self._on_service_status_action)

    @property
    def app_root(self) -> Path:
        return Path(self.charm_dir)

    @property
    def service_script(self) -> Path:
        return self.app_root / "src" / "service_api.py"

    @property
    def refresh_helper_script(self) -> Path:
        return self.app_root / "src" / "refresh_spread_tests_execution.py"

    @property
    def execution_data_dir(self) -> Path:
        configured = str(self.config.get("execution-data-dir", "")).strip()
        if configured:
            return Path(configured)
        return self.STATE_DIR

    @property
    def spread_tests_execution_path(self) -> Path:
        return self.execution_data_dir / "spread-execution-map.json"

    @property
    def base_tests_path(self) -> Path:
        return self.execution_data_dir / "base-executions.json"

    @property
    def venv_python(self) -> Path:
        return self.VENV_DIR / "bin" / "python3"

    @property
    def refresh_timeout_seconds(self) -> int:
        return int(self.config.get("refresh-timeout-seconds", 300))

    @property
    def listen_host(self) -> str:
        return str(self.config.get("listen-host") or "0.0.0.0")

    @property
    def listen_port(self) -> int:
        return int(self.config.get("listen-port") or 8181)

    def _on_install(self, _event):
        self._reconcile_in_hook("installing spread-test-selector")

    def _on_start(self, _event):
        self._reconcile_in_hook("starting spread-test-selector")

    def _on_config_changed(self, _event):
        self._reconcile_in_hook("applying updated configuration")

    def _on_upgrade_charm(self, _event):
        # the service runs the charm's code, so restart it to pick up the new version
        self._reconcile_in_hook("upgrading charm configuration", force_restart=True)

    def _on_secret_changed(self, _event: SecretChangedEvent):
        self._reconcile_in_hook("processing secret update")

    def _reconcile_in_hook(self, maintenance_msg: str, force_restart: bool = False) -> None:
        """
        Reconcile from a hook. Failures leave the unit blocked or waiting with
        the reason (set by _reconcile) rather than failing the hook, which
        would put the unit in error; the reconcile action retries.
        """
        try:
            self._reconcile(maintenance_msg, force_restart=force_restart)
        except RuntimeError as exc:
            logger.warning("reconcile failed: %s", exc)

    def _on_reconcile_action(self, event: ActionEvent):
        try:
            self._reconcile("reconciling service")
            event.set_results({"result": "reconcile completed"})
        except Exception as exc:
            event.fail(str(exc))

    def _on_service_status_action(self, event: ActionEvent):
        proc = self._run(["systemctl", "is-active", self.SERVICE_NAME], check=False)
        status = (proc.stdout or "").strip() or "unknown"
        event.set_results({"service": self.SERVICE_NAME, "status": status})

    def _on_calculate_tests_action(self, event: ActionEvent):
        changed_files_json = str(event.params.get("changed-files-json") or "").strip()
        if not changed_files_json:
            event.fail("changed-files-json is required and must be a json object with modified/added/deleted lists")
            return

        try:
            parsed = json.loads(changed_files_json)
        except json.JSONDecodeError as exc:
            event.fail(f"changed-files-json must be valid json: {exc}")
            return

        if not isinstance(parsed, dict):
            event.fail("changed-files-json must be a json object with modified/added/deleted lists")
            return

        changed_files = {
            key: [str(item) for item in parsed.get(key, [])]
            for key in ("modified", "added", "deleted")
        }

        tests = self._parse_csv_or_lines(str(event.params.get("tests") or ""))
        if not tests:
            event.fail("tests must contain at least one allowed test")
            return

        backend_systems = self._parse_csv_or_lines(str(event.params.get("backend-systems") or ""))
        if not backend_systems:
            event.fail("backend-systems must contain at least one backend:system")
            return

        try:
            response = self._call_service(
                endpoint="/calculate-tests",
                payload={"changed_files": changed_files, "backend_systems": backend_systems, "tests": tests},
            )
        except RuntimeError as exc:
            event.fail(str(exc))
            return
        event.set_results(
            {
                "tests": "\n".join(response.get("tests", [])),
                "count": str(response.get("count", 0)),
            }
        )

    def _on_mongodb_updated_action(self, event: ActionEvent):
        try:
            # the refresh command updates the features checkout itself
            response = self._call_service(
                endpoint="/mongodb-updated",
                payload={},
                timeout=self.refresh_timeout_seconds + self.REFRESH_RESPONSE_MARGIN_SECONDS,
            )
        except subprocess.CalledProcessError as exc:
            event.fail(self._command_error(exc))
            return
        except (RuntimeError, OSError) as exc:
            event.fail(str(exc))
            return
        event.set_results({"result": response.get("result", "ok")})

    def _reconcile(self, maintenance_msg: str, force_restart: bool = False):
        """Bring the unit to the configured state; raises RuntimeError on failure."""
        self.unit.status = MaintenanceStatus(maintenance_msg)
        try:
            self._ensure_state_dirs()
            self._update_etc_environment(self._proxy_config())
            self._install_packages()
            self._ensure_venv()
            self._fetch_snapd_features()
            self._write_mongodb_credentials_file()
            service_state_changed = self._write_service_config()
            if service_state_changed or force_restart:
                self._configure_systemd_service()
            self._sync_open_port()
            self.unit.status = ActiveStatus("api ready")
        except (OSError, RuntimeError) as exc:
            self.unit.status = BlockedStatus(str(exc))
            raise RuntimeError(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            msg = self._command_error(exc)
            self.unit.status = WaitingStatus(msg)
            raise RuntimeError(msg) from exc

    @staticmethod
    def _command_error(exc: subprocess.CalledProcessError) -> str:
        stderr = (exc.stderr or "").strip()
        cmd = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
        msg = f"command failed: {cmd}"
        if stderr:
            msg = f"{msg}: {stderr}"
        return msg

    def _ensure_state_dirs(self) -> None:
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.execution_data_dir.mkdir(parents=True, exist_ok=True)

    def _write_service_config(self) -> bool:
        config = {
            "spread_tests_execution_path": str(self.spread_tests_execution_path),
            "base_tests_path": str(self.base_tests_path),
            "credentials_path": str(self.MONGODB_CREDENTIALS_PATH),
            "query_features_path": str(self._query_features_path()),
            "refresh_command": self._refresh_command(),
            "refresh_timeout_seconds": self.refresh_timeout_seconds,
        }
        config_json = json.dumps(config, indent=2, sort_keys=True)
        service_state = self._service_state_marker_value(config)
        previous_state = self.SERVICE_STATE_MARKER_PATH.read_text(encoding="utf-8").strip() if self.SERVICE_STATE_MARKER_PATH.exists() else ""

        if self.SERVICE_CONFIG_PATH.exists() and previous_state == service_state:
            return False

        self.SERVICE_CONFIG_PATH.write_text(config_json, encoding="utf-8")
        self.SERVICE_STATE_MARKER_PATH.write_text(service_state, encoding="utf-8")
        return True

    @staticmethod
    def _write_private_file(path: Path, content: str) -> None:
        """Write a file only root can read, created with that mode and replaced atomically."""
        tmp = path.with_name(path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)



    def _install_packages(self) -> None:
        """Install the apt packages needed to fetch snapd and create the venv."""
        missing = [
            package for package in self.APT_PACKAGES
            if self._run(["dpkg-query", "-W", "-f=${Status}", package], check=False).stdout.strip()
            != "install ok installed"
        ]
        if not missing:
            return
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        self._run(["apt-get", "update"], check=True, env=env)
        self._run(["apt-get", "install", "-y", "--no-install-recommends", *missing], check=True, env=env)

    def _ensure_venv(self) -> None:
        """Create the venv query_features.py runs in, with a recent enough pymongo."""
        if not self.venv_python.exists():
            self._run(["python3", "-m", "venv", str(self.VENV_DIR)], check=True)
        version_check = (
            "import sys, pymongo; "
            f"sys.exit(0 if pymongo.version_tuple[:2] >= {self.PYMONGO_MIN_VERSION} else 1)"
        )
        if self._run([str(self.venv_python), "-c", version_check], check=False).returncode == 0:
            return
        self._run(
            [
                str(self.venv_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--upgrade",
                *self.VENV_PACKAGES,
            ],
            check=True,
        )

    def _snapd_source(self) -> tuple[str, str] | None:
        """The repository url and ref to fetch the features folder from, or None to use query-features-path."""
        if str(self.config.get("query-features-path") or "").strip():
            return None
        url = str(self.config.get("snapd-repo-url") or "").strip()
        ref = str(self.config.get("snapd-ref") or "").strip()
        if not url or not ref:
            raise RuntimeError("snapd-repo-url and snapd-ref must be set, or query-features-path")
        return url, ref

    def _fetch_snapd_features(self) -> None:
        """
        Check out tests/utils/features (query_features.py and its modules) from
        snapd-repo-url at snapd-ref, so it is there before the first refresh;
        every refresh updates it again. Skipped when query-features-path is set.
        """
        source = self._snapd_source()
        if source is None:
            return
        fetch_snapd_features(self.SNAPD_DIR, *source, run=lambda cmd: self._run(cmd, check=True))

    def _service_state_marker_value(self, config: dict[str, object]) -> str:
        relevant_state = {
            "listen-host": self.listen_host,
            "listen-port": self.listen_port,
            "mongodb-secret-id": str(self.config.get("mongodb-secret-id") or "").strip(),
            "query-features-path": str(self._query_features_path()),
            "execution-data-dir": str(self.execution_data_dir),
            "spread-execution-map-path": str(self.spread_tests_execution_path),
            "base-executions-path": str(self.base_tests_path),
            "base-tests-max-minutes": int(self.config.get("base-tests-max-minutes", 15)),
            "refresh-helper-command": str(self.config.get("refresh-helper-command") or "").strip(),
            "refresh-timeout-seconds": self.refresh_timeout_seconds,
            "refresh-command": config["refresh_command"],
            # the service and its refreshes run with the proxy environment
            "proxy": self._proxy_config(),
        }
        return json.dumps(relevant_state, sort_keys=True)

    def _proxy_config(self) -> dict[str, str]:
        """The proxy environment: the configured values, with ALWAYS_NO_PROXY added to NO_PROXY."""
        no_proxy: list[str] = []
        for host in [*str(self.config.get("no_proxy") or "").split(","), *self.ALWAYS_NO_PROXY]:
            host = host.strip()
            if host and host not in no_proxy:
                no_proxy.append(host)
        return {
            "HTTP_PROXY": str(self.config.get("http_proxy") or "").strip(),
            "HTTPS_PROXY": str(self.config.get("https_proxy") or "").strip(),
            "NO_PROXY": ",".join(no_proxy),
        }

    def _proxy_environment(self) -> dict[str, str]:
        """The proxy variables to set, in upper and lower case; empty ones are left out."""
        env = {}
        for key, value in self._proxy_config().items():
            if value:
                env[key] = value
                env[key.lower()] = value
        return env

    def _set_runtime_proxy_env(self) -> None:
        env = self._proxy_environment()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            for name in (key, key.lower()):
                if name in env:
                    os.environ[name] = env[name]
                else:
                    os.environ.pop(name, None)

    @staticmethod
    def _quote_env_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _update_etc_environment(self, proxy_cfg: dict[str, str]) -> None:
        """Keep the proxy variables in /etc/environment in sync with the config, leaving other lines alone."""
        values = {}
        for key, value in proxy_cfg.items():
            values[key] = value
            values[key.lower()] = value

        lines = []
        if self.ETC_ENVIRONMENT_PATH.exists():
            lines = self.ETC_ENVIRONMENT_PATH.read_text(encoding="utf-8").splitlines()
        new_lines, seen = [], set()
        for line in lines:
            key = line.partition("=")[0].strip()
            if line.strip().startswith("#") or "=" not in line or key not in values:
                new_lines.append(line)
                continue
            seen.add(key)
            if values[key]:
                new_lines.append(f"{key}={self._quote_env_value(values[key])}")
        for key in sorted(set(values) - seen):
            if values[key]:
                new_lines.append(f"{key}={self._quote_env_value(values[key])}")
        content = "\n".join(new_lines).rstrip() + "\n"
        if not self.ETC_ENVIRONMENT_PATH.exists() or self.ETC_ENVIRONMENT_PATH.read_text(encoding="utf-8") != content:
            self.ETC_ENVIRONMENT_PATH.write_text(content, encoding="utf-8")

    def _query_features_path(self) -> Path:
        configured = str(self.config.get("query-features-path") or "").strip()
        if configured:
            return Path(configured)
        return self.SNAPD_DIR / self.FEATURES_SUBDIR / "query_features.py"

    def _refresh_command(self) -> list[str]:
        configured = str(self.config.get("refresh-helper-command") or "").strip()
        if configured:
            return shlex.split(configured)
        command = [
            str(self.venv_python),
            str(self.refresh_helper_script),
            "--query-features-path",
            str(self._query_features_path()),
            "--credentials-path",
            str(self.MONGODB_CREDENTIALS_PATH),
            "--output-dir",
            str(self.execution_data_dir),
            "--base-tests-max-minutes",
            str(int(self.config.get("base-tests-max-minutes", 15))),
            "--jobs",
            str(int(self.config.get("refresh-jobs", 2))),
        ]
        source = self._snapd_source()
        if source is not None:
            command += ["--snapd-dir", str(self.SNAPD_DIR), "--snapd-repo-url", source[0], "--snapd-ref", source[1]]
        return command

    def _write_mongodb_credentials_file(self) -> None:
        secret_id = str(self.config.get("mongodb-secret-id") or "").strip()
        if not secret_id:
            raise RuntimeError("mongodb-secret-id must be configured")

        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
        except Exception as exc:
            raise RuntimeError(f"could not access mongodb secret {secret_id}: {exc}") from exc

        credentials = self._extract_credentials_json(content)
        self._write_private_file(self.MONGODB_CREDENTIALS_PATH, json.dumps(credentials, indent=2))

    @staticmethod
    def _extract_credentials_json(secret_content: dict[str, str]) -> dict:
        string_keys = (
            "mongodb-credentials-json",
            "mongodb_credentials_json",
            "credentials-json",
            "credentials_json",
            "json",
        )

        for key in string_keys:
            raw = secret_content.get(key)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"secret key {key} does not contain valid json: {exc}") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError(f"secret key {key} must contain a json object")
            SpreadTestSelectorCharm._validate_mongodb_creds(parsed)
            return parsed

        direct = {k: secret_content.get(k) for k in ("host", "port", "user", "password") if secret_content.get(k)}
        if len(direct) == 4:
            if isinstance(direct.get("port"), str) and direct["port"].isdigit():
                direct["port"] = int(direct["port"])
            SpreadTestSelectorCharm._validate_mongodb_creds(direct)
            return direct

        raise RuntimeError(
            "mongodb secret must provide a json string in one of: "
            "mongodb-credentials-json, mongodb_credentials_json, credentials-json, credentials_json, json"
        )

    @staticmethod
    def _validate_mongodb_creds(creds: dict) -> None:
        missing = [k for k in ("host", "port", "user", "password") if k not in creds]
        if missing:
            raise RuntimeError(f"mongodb credentials are missing keys: {', '.join(missing)}")

    def _render_systemd_unit(self) -> str:
        host = self.listen_host
        port = self.listen_port
        # the service runs the refreshes, which fetch snapd from git
        environment = "".join(
            f"Environment={shlex.quote(f'{key}={value}')}\n" for key, value in sorted(self._proxy_environment().items())
        )
        return f"""[Unit]
Description=spread-test-selector api service
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory={self.app_root}
{environment}ExecStart=/usr/bin/python3 {self.service_script} --config {self.SERVICE_CONFIG_PATH} --host {host} --port {port}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

    def _configure_systemd_service(self) -> None:
        if not self.service_script.exists():
            raise FileNotFoundError(f"service script not found: {self.service_script}")
        if not self.refresh_helper_script.exists():
            raise FileNotFoundError(f"refresh helper script not found: {self.refresh_helper_script}")

        unit_path = self.SYSTEMD_DIR / f"{self.SERVICE_NAME}.service"
        unit_path.write_text(self._render_systemd_unit(), encoding="utf-8")
        self._run(["systemctl", "daemon-reload"], check=True)
        self._run(["systemctl", "enable", "--now", self.SERVICE_NAME], check=True)
        self._run(["systemctl", "restart", self.SERVICE_NAME], check=True)

    def _sync_open_port(self) -> None:
        port = self.listen_port
        self.unit.open_port("tcp", port)
        for opened in self.unit.opened_ports():
            if opened.protocol == "tcp" and opened.port != port:
                self.unit.close_port("tcp", opened.port)

    @staticmethod
    def _parse_csv_or_lines(raw: str) -> list[str]:
        if not raw:
            return []
        items: list[str] = []
        for chunk in raw.replace("\r", "\n").split("\n"):
            for item in chunk.split(","):
                item = item.strip()
                if item:
                    items.append(item)
        return items

    def _call_service(self, endpoint: str, payload: dict, timeout: int = 30) -> dict:
        host = self.listen_host
        if host == "0.0.0.0":
            # listening on all interfaces; reach it locally through loopback
            host = "127.0.0.1"
        url = f"http://{host}:{self.listen_port}{endpoint}"
        req = urllib_request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # the service is local: never go through a proxy, whatever the environment says
        opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            details = exc.read().decode("utf-8")
            raise RuntimeError(f"service request failed ({exc.code}): {details}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"service request failed: {exc}") from exc
        return json.loads(body)

    def _run(self, cmd: list[str], check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
        logger.info("Running command: %s", shlex.join(cmd))
        return subprocess.run(cmd, check=check, capture_output=True, text=True, env=env)


if __name__ == "__main__":
    main(SpreadTestSelectorCharm)
