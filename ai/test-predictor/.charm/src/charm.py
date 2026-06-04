#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Machine charm for test-predictor API."""

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus

logger = logging.getLogger(__name__)


class TestPredictorCharm(CharmBase):
    """Deploy and manage the test predictor API service."""

    SERVICE_NAMES = (
        "test-predictor",
        "test-predictor-api",
        "test-predictor-trainer",
        "test-predictor-cleaner",
        "test-predictor-dependency",
    )
    STATE_DIR = Path("/var/lib/test-predictor")
    VENV_DIR = STATE_DIR / ".venv"
    SYSTEMD_DIR = Path("/etc/systemd/system")
    ETC_ENV_PATH = Path("/etc/environment")
    CORE_APT_PACKAGES = (
        "python3-apscheduler",
        "python3-flask",
        "python3-gunicorn",
        "python3-networkx",
        "python3-numpy",
        "python3-pandas",
        "python3-requests",
        "python3-sklearn",
        "python3-statsmodels",
    )
    PIP_COMMON_ARGS = (
        "--disable-pip-version-check",
        "--no-input",
        "--retries",
        "3",
        "--timeout",
        "30",
    )

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.start, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.upgrade_charm, self._reconcile)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.restart_services_action, self._on_restart_services)
        self.framework.observe(self.on.start_services_action, self._on_start_services)
        self.framework.observe(self.on.stop_services_action, self._on_stop_services)
        self.framework.observe(self.on.service_status_action, self._on_service_status)

    @property
    def app_root(self) -> Path:
        return Path(self.charm_dir)

    @property
    def app_requirements(self) -> Path:
        return self.app_root / "requirements-app.txt"

    @property
    def ml_requirements(self) -> Path:
        return self.app_root / "requirements-ml.txt"

    def _run(self, cmd: list[str]) -> None:
        logger.info("Running command: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, text=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            if exc.stdout:
                logger.error("Command stdout: %s", exc.stdout.strip())
            if exc.stderr:
                logger.error("Command stderr: %s", exc.stderr.strip())
            raise

    def _install_apt_packages(self, packages: tuple[str, ...]) -> None:
        if not packages:
            return
        self._run(["apt-get", "update"])
        self._run(["apt-get", "install", "-y", "--no-install-recommends", *packages])

    def _create_venv(self) -> None:
        if self.VENV_DIR.exists():
            shutil.rmtree(self.VENV_DIR)
        self._run(["python3", "-m", "venv", "--system-site-packages", str(self.VENV_DIR)])

    def _proxy_config(self) -> dict[str, str]:
        cfg = self.model.config
        return {
            "HTTP_PROXY": str(cfg.get("http_proxy", "")).strip(),
            "HTTPS_PROXY": str(cfg.get("https_proxy", "")).strip(),
            "NO_PROXY": str(cfg.get("no_proxy", "")).strip(),
        }

    def _set_runtime_proxy_env(self) -> dict[str, str]:
        proxy_cfg = self._proxy_config()
        for key, value in proxy_cfg.items():
            lower_key = key.lower()
            if value:
                os.environ[key] = value
                os.environ[lower_key] = value
            else:
                os.environ.pop(key, None)
                os.environ.pop(lower_key, None)
        return proxy_cfg

    @staticmethod
    def _quote_env_value(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _update_etc_environment(self, proxy_cfg: dict[str, str]) -> None:
        managed_keys = {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        values = {
            "HTTP_PROXY": proxy_cfg["HTTP_PROXY"],
            "HTTPS_PROXY": proxy_cfg["HTTPS_PROXY"],
            "NO_PROXY": proxy_cfg["NO_PROXY"],
            "http_proxy": proxy_cfg["HTTP_PROXY"],
            "https_proxy": proxy_cfg["HTTPS_PROXY"],
            "no_proxy": proxy_cfg["NO_PROXY"],
        }

        current_lines: list[str] = []
        if self.ETC_ENV_PATH.exists():
            current_lines = self.ETC_ENV_PATH.read_text(encoding="utf-8").splitlines()

        new_lines: list[str] = []
        seen_keys: set[str] = set()
        for line in current_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                new_lines.append(line)
                continue

            key, _sep, _value = line.partition("=")
            key = key.strip()
            if key not in managed_keys:
                new_lines.append(line)
                continue

            seen_keys.add(key)
            value = values[key]
            if value:
                new_lines.append(f"{key}={self._quote_env_value(value)}")

        for key in sorted(managed_keys - seen_keys):
            value = values[key]
            if value:
                new_lines.append(f"{key}={self._quote_env_value(value)}")

        content = "\n".join(new_lines).rstrip() + "\n"
        self.ETC_ENV_PATH.write_text(content, encoding="utf-8")

    def _render_unit(self, service_name: str) -> str:
        cfg = self.model.config
        port = int(cfg["port"])
        workers = int(cfg["workers"])
        threads = int(cfg["threads"])
        timeout = int(cfg["timeout"])
        proxy_cfg = self._proxy_config()

        base_env = [
            "Environment=PYTHONUNBUFFERED=1",
            f"Environment=PYTHONPATH={self.app_root / 'src/common'}:{self.app_root / 'src'}",
        ]
        for key, value in proxy_cfg.items():
            if value:
                base_env.append(f"Environment={key}={value}")
                base_env.append(f"Environment={key.lower()}={value}")

        service_cmds = {
            "test-predictor-api": (
                [
                    f"{self.VENV_DIR / 'bin/gunicorn'}",
                    "--worker-class gthread",
                    f"--workers {workers}",
                    f"--threads {threads}",
                    f"--timeout {timeout}",
                    "--preload",
                    f"--bind 0.0.0.0:{port}",
                    "src.services.api.main:app",
                ],
                [],
            ),
            "test-predictor": (
                [f"{self.VENV_DIR / 'bin/python3'} src/services/jobs/predictor.py"],
                [
                    "Environment=CUDA_VISIBLE_DEVICES=-1",
                    "Environment=TF_CPP_MIN_LOG_LEVEL=2",
                ],
            ),
            "test-predictor-trainer": (
                [f"{self.VENV_DIR / 'bin/python3'} src/services/jobs/trainer.py"],
                [
                    "Environment=CUDA_VISIBLE_DEVICES=-1",
                    "Environment=TF_CPP_MIN_LOG_LEVEL=2",
                ],
            ),
            "test-predictor-cleaner": (
                [f"{self.VENV_DIR / 'bin/python3'} src/services/jobs/cleaner.py"],
                [],
            ),
            "test-predictor-dependency": (
                [f"{self.VENV_DIR / 'bin/python3'} src/services/jobs/dependency.py"],
                [],
            ),
        }

        exec_cmd_lines, extra_env = service_cmds[service_name]
        exec_start = " \\\n    ".join(exec_cmd_lines)
        all_env = base_env + extra_env
        env_block = "\n".join(all_env)

        return f"""[Unit]
Description={service_name} for test-predictor
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory={self.app_root}
{env_block}
ExecStart={exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

    def _ensure_runtime(self) -> None:
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)

        venv_python_path = self.VENV_DIR / "bin/python3"

        # Ensure venv support is present before creating a venv to avoid noisy expected failures.
        self._install_apt_packages(("python3-venv",))

        # Recover from partial/failed previous venv creation.
        if not venv_python_path.exists():
            self._create_venv()

        venv_python_cmd = str(self.VENV_DIR / "bin/python3")
        try:
            self._run([venv_python_cmd, "-m", "ensurepip", "--upgrade"])
        except subprocess.CalledProcessError:
            logger.warning("ensurepip failed inside venv; reinstalling python3-venv and recreating venv")
            self._install_apt_packages(("python3-venv",))
            self._create_venv()
            venv_python_cmd = str(self.VENV_DIR / "bin/python3")
            self._run([venv_python_cmd, "-m", "ensurepip", "--upgrade"])
        self._run(
            [
                venv_python_cmd,
                "-m",
                "pip",
                "install",
                *self.PIP_COMMON_ARGS,
                "--upgrade",
                "pip",
                "setuptools",
                "wheel",
            ]
        )

        try:
            self._run(
                [
                    venv_python_cmd,
                    "-m",
                    "pip",
                    "install",
                    *self.PIP_COMMON_ARGS,
                    "-r",
                    str(self.app_requirements),
                ]
            )
        except subprocess.CalledProcessError:
            logger.warning(
                "Unable to install app dependencies from pip; falling back to apt packages for core dependencies"
            )
            self._install_apt_packages(self.CORE_APT_PACKAGES)

        # ML dependencies are required for predictor/trainer services.
        if self.ml_requirements.exists():
            self._run(
                [
                    venv_python_cmd,
                    "-m",
                    "pip",
                    "install",
                    *self.PIP_COMMON_ARGS,
                    "-r",
                    str(self.ml_requirements),
                ]
            )

    def _configure_services(self) -> None:
        for service_name in self.SERVICE_NAMES:
            unit_file = self.SYSTEMD_DIR / f"{service_name}.service"
            unit_file.write_text(self._render_unit(service_name), encoding="utf-8")

        self._run(["systemctl", "daemon-reload"])

        for service_name in self.SERVICE_NAMES:
            self._run(["systemctl", "enable", "--now", service_name])
            self._run(["systemctl", "restart", service_name])

    def _sync_open_ports(self) -> None:
        desired_port = int(self.model.config["port"])
        self.unit.open_port(protocol="tcp", port=desired_port)

        for opened in self.unit.opened_ports():
            if opened.protocol == "tcp" and opened.port != desired_port:
                self.unit.close_port(protocol="tcp", port=opened.port)

    def _reconcile(self, _event) -> None:
        self.unit.status = MaintenanceStatus("configuring test-predictor")

        try:
            proxy_cfg = self._set_runtime_proxy_env()
            self._update_etc_environment(proxy_cfg)
            self._ensure_runtime()
            self._configure_services()
            self._sync_open_ports()
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.exception("Failed to configure test-predictor")
            self.unit.status = BlockedStatus(f"failed to configure service: {exc}")
            return

        port = self.model.config["port"]
        ready_message = f"configuration ready: api running on port {port}"
        logger.info(ready_message)
        self.unit.status = ActiveStatus(ready_message)

    def _on_stop(self, _event) -> None:
        try:
            for service_name in self.SERVICE_NAMES:
                self._run(["systemctl", "disable", "--now", service_name])
        except (subprocess.CalledProcessError, OSError):
            logger.info("One or more services were not running during stop")

    def _on_restart_services(self, event: ActionEvent) -> None:
        try:
            self._run(["systemctl", "daemon-reload"])
            self._run(["systemctl", "restart", *self.SERVICE_NAMES])
        except (subprocess.CalledProcessError, OSError) as exc:
            event.fail(f"Failed to restart services: {exc}")
            return
        event.set_results({"result": "All services restarted successfully."})

    def _on_start_services(self, event: ActionEvent) -> None:
        try:
            self._run(["systemctl", "enable", "--now", *self.SERVICE_NAMES])
        except (subprocess.CalledProcessError, OSError) as exc:
            event.fail(f"Failed to start services: {exc}")
            return
        event.set_results({"result": "All services started successfully."})

    def _on_stop_services(self, event: ActionEvent) -> None:
        try:
            self._run(["systemctl", "stop", *self.SERVICE_NAMES])
        except (subprocess.CalledProcessError, OSError) as exc:
            event.fail(f"Failed to stop services: {exc}")
            return
        event.set_results({"result": "All services stopped successfully."})

    def _on_service_status(self, event: ActionEvent) -> None:
        results = {}
        for service_name in self.SERVICE_NAMES:
            proc = subprocess.run(
                ["systemctl", "is-active", service_name],
                check=False,
                capture_output=True,
                text=True,
            )
            results[service_name] = proc.stdout.strip() or "unknown"
        event.set_results(results)


if __name__ == "__main__":
    main(TestPredictorCharm)
