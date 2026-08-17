#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Charm for installing and configuring grafana-agent."""

import logging
import os
import shlex
import subprocess
from pathlib import Path

from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

logger = logging.getLogger(__name__)
GRAFANA_AGENT_SNAP_RESOURCE = "grafana-agent-snap"
GRAFANA_AGENT_CONFIG_TEMPLATE = "config/charm/grafana-agent.yaml.orig"
GRAFANA_AGENT_CONFIG_SCRIPT = "scripts/configure-grafana-agent.sh"


class GrafanaAgentInstallerCharm(CharmBase):
    """Machine charm that installs and configures grafana-agent snap."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.reconcile_action, self._on_reconcile_action)

    def _on_install(self, _event):
        self.unit.status = MaintenanceStatus("installing grafana-agent")
        self._reconcile()

    def _on_config_changed(self, _event):
        self.unit.status = MaintenanceStatus("applying updated grafana-agent configuration")
        self._reconcile()

    def _on_reconcile_action(self, event: ActionEvent):
        try:
            self._reconcile()
            event.set_results({"result": "reconcile completed"})
        except Exception as exc:
            event.fail(str(exc))

    def _reconcile(self):
        try:
            self._ensure_snapd()
            self._ensure_grafana_agent_snap()
            self._run_grafana_agent_config_script()
            self.unit.status = ActiveStatus("grafana-agent configured")
        except FileNotFoundError as exc:
            self.unit.status = BlockedStatus(str(exc))
            raise
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            msg = f"command failed: {' '.join(exc.cmd)}"
            if stderr:
                msg = f"{msg}: {stderr}"
            self.unit.status = WaitingStatus(msg)
            raise RuntimeError(msg) from exc
        except Exception as exc:
            self.unit.status = BlockedStatus(str(exc))
            raise

    def _ensure_snapd(self):
        self._run(["snap", "version"], check=True)

    def _ensure_grafana_agent_snap(self):
        snap_name = "grafana-agent"

        result = self._run(["snap", "list", snap_name], check=False)
        if result.returncode == 0:
            logger.info("Snap %s already installed", snap_name)
            return

        try:
            snap_path = self.model.resources.fetch(GRAFANA_AGENT_SNAP_RESOURCE)
        except Exception as exc:
            raise FileNotFoundError(
                f"Snap resource '{GRAFANA_AGENT_SNAP_RESOURCE}' is not available; "
                "attach it with: juju attach-resource <app> grafana-agent-snap=/path/to/grafana-agent.snap"
            ) from exc

        if not snap_path.exists() or snap_path.suffix != ".snap":
            raise FileNotFoundError(
                f"Snap resource '{GRAFANA_AGENT_SNAP_RESOURCE}' is invalid: expected a .snap file, got {snap_path}"
            )

        logger.info("Installing snap from %s", snap_path)
        self._run(["snap", "install", "--dangerous", str(snap_path)], check=True)

    def _run_grafana_agent_config_script(self):
        script = self._resolve_path(GRAFANA_AGENT_CONFIG_SCRIPT, "grafana-agent configuration script")

        template_path = str(
            self._resolve_path(GRAFANA_AGENT_CONFIG_TEMPLATE, "grafana-agent config template")
        )

        env = {
            "GRAFANA_AGENT_CONFIG_TEMPLATE": template_path,
            "GRAFANA_AGENT_PROJECT": str(self.config["grafana-agent-project"]).strip(),
            "GRAFANA_AGENT_AGENT": str(self.config["grafana-agent-name"]).strip(),
            "GRAFANA_AGENT_ENDPOINT": str(self.config["grafana-agent-endpoint"]).strip(),
            "GRAFANA_AGENT_TARGET": str(self.config["grafana-agent-target"]).strip(),
            "GRAFANA_AGENT_PORT": str(self.config["grafana-agent-port"]).strip(),
            "GRAFANA_AGENT_SCRAPE_INTERVAL": str(self.config["grafana-agent-scrape-interval"]).strip() or "1m",
            "GRAFANA_AGENT_SCRAPE_TIMEOUT": str(self.config["grafana-agent-scrape-timeout"]).strip() or "10s",
        }

        self._run([str(script)], env=env, check=True)

    def _resolve_path(self, configured_path: str, description: str) -> Path:
        if not configured_path:
            raise FileNotFoundError("script path is empty")

        path_obj = Path(configured_path)
        if path_obj.is_absolute() and path_obj.exists():
            return path_obj

        charm_relative = Path(self.charm_dir) / configured_path
        if charm_relative.exists():
            return charm_relative

        raise FileNotFoundError(f"{description} not found: {configured_path}")

    def _run(self, cmd, env=None, check=False, cwd=None):
        cmd_display = cmd if isinstance(cmd, str) else shlex.join(cmd)
        logger.info("Running command: %s", cmd_display)
        run_env = os.environ.copy()
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
    main(GrafanaAgentInstallerCharm)
