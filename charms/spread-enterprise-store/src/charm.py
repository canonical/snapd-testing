#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Charm for installing enterprise-store and opening HTTP/HTTPS ports."""

import logging
import os
import shlex
import subprocess

from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, ModelError, WaitingStatus

logger = logging.getLogger(__name__)
SNAPS_TO_INSTALL = ("enterprise-store", "postgresql")


class SpreadEnterpriseStoreCharm(CharmBase):
    """Machine charm that installs enterprise-store and opens ports 80/443."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.reconcile_action, self._on_reconcile_action)

    def _on_install(self, _event):
        self.unit.status = MaintenanceStatus("installing enterprise-store")
        self._reconcile()

    def _on_config_changed(self, _event):
        self.unit.status = MaintenanceStatus("re-applying enterprise-store setup")
        self._reconcile()

    def _on_reconcile_action(self, event: ActionEvent):
        try:
            self._reconcile()
            event.set_results({"result": "reconcile completed"})
        except (FileNotFoundError, OSError, RuntimeError, ModelError, subprocess.CalledProcessError) as exc:
            event.fail(str(exc))

    def _reconcile(self):
        try:
            self._apply_proxy_settings()
            self._ensure_snapd()
            self._ensure_required_snaps()
            self._open_required_ports()
            self.unit.status = ActiveStatus("enterprise-store installed")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            msg = f"command failed: {shlex.join(exc.cmd)}"
            if stderr:
                msg = f"{msg}: {stderr}"
            self.unit.status = WaitingStatus(msg)
            raise RuntimeError(msg) from exc
        except (FileNotFoundError, OSError, RuntimeError, ModelError) as exc:
            self.unit.status = BlockedStatus(str(exc))
            raise

    def _ensure_snapd(self):
        self._run(["snap", "version"], check=True)

    def _ensure_required_snaps(self):
        for snap_name in SNAPS_TO_INSTALL:
            result = self._run(["snap", "list", snap_name], check=False)
            if result.returncode == 0:
                logger.info("Snap %s already installed", snap_name)
                continue

            self._run(["snap", "install", snap_name], check=True)

    def _open_required_ports(self):
        self.unit.open_port("tcp", 80)
        self.unit.open_port("tcp", 443)

    def _apply_proxy_settings(self):
        """Apply HTTP/HTTPS proxy settings to the system environment and snapd."""
        http_proxy = str(self.config.get("http-proxy", "")).strip()
        https_proxy = str(self.config.get("https-proxy", "")).strip()
        no_proxy = str(self.config.get("no-proxy", "")).strip()

        # Build proxy environment variables list (without quotes)
        env_vars = []
        if http_proxy:
            env_vars.append(f"http_proxy={http_proxy}")
            env_vars.append(f"HTTP_PROXY={http_proxy}")
        if https_proxy:
            env_vars.append(f"https_proxy={https_proxy}")
            env_vars.append(f"HTTPS_PROXY={https_proxy}")
        if no_proxy:
            env_vars.append(f"no_proxy={no_proxy}")
            env_vars.append(f"NO_PROXY={no_proxy}")

        if not env_vars:
            return

        # Apply to /etc/environment with quotes
        env_file = "/etc/environment"
        env_content = ""
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                env_content = f.read()
        except FileNotFoundError:
            pass

        # Remove old proxy settings
        proxy_vars = ("HTTP_PROXY=", "HTTPS_PROXY=", "NO_PROXY=", "http_proxy=", "https_proxy=", "no_proxy=")
        lines = [line for line in env_content.split("\n") if not line.startswith(proxy_vars)]

        # Add quoted proxy settings
        for var in env_vars:
            key, value = var.split("=", 1)
            lines.append(f'{key}="{value}"')

        new_content = "\n".join(lines) + ("\n" if lines else "")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info("Proxy settings applied to %s", env_file)

        # Create snapd systemd drop-in configuration (reusing env_vars)
        snapd_service_dir = "/etc/systemd/system/snapd.service.d"
        os.makedirs(snapd_service_dir, mode=0o755, exist_ok=True)

        snapd_proxy_conf = f"""[Service]
Environment={' '.join(env_vars)}
"""
        snapd_proxy_file = os.path.join(snapd_service_dir, "proxy.conf")
        with open(snapd_proxy_file, "w", encoding="utf-8") as f:
            f.write(snapd_proxy_conf)
        logger.info("Snapd proxy configuration written to %s", snapd_proxy_file)

        # Reload systemd daemon and restart snapd to apply new proxy env.
        self._run(["systemctl", "daemon-reload"], check=True)
        self._run(["systemctl", "restart", "snapd"], check=True)

    def _run(self, cmd, env=None, check=False, cwd=None):
        cmd_display = cmd if isinstance(cmd, str) else shlex.join(cmd)
        logger.info("Running command: %s", cmd_display)
        run_env = os.environ.copy()

        # Add configured proxy settings to the environment
        http_proxy = str(self.config.get("http-proxy", "")).strip()
        https_proxy = str(self.config.get("https-proxy", "")).strip()
        no_proxy = str(self.config.get("no-proxy", "")).strip()

        if http_proxy:
            run_env["http_proxy"] = http_proxy
            run_env["HTTP_PROXY"] = http_proxy
        if https_proxy:
            run_env["https_proxy"] = https_proxy
            run_env["HTTPS_PROXY"] = https_proxy
        if no_proxy:
            run_env["no_proxy"] = no_proxy
            run_env["NO_PROXY"] = no_proxy

        if env:
            run_env.update(env)
        return subprocess.run(
            cmd,
            check=check,
            env=run_env,
            text=True,
            capture_output=True,
            cwd=cwd,
        )


if __name__ == "__main__":
    main(SpreadEnterpriseStoreCharm)
