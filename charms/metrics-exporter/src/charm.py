#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Charm for installing prometheus-pushgateway snap."""

import logging
import os
import shlex
import subprocess
from pathlib import Path

from ops.charm import ActionEvent, CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

logger = logging.getLogger(__name__)
METRICS_EXPORTER_CONFIG_SCRIPT = "scripts/configure-metrics-exporter.sh"


class MetricsExporterCharm(CharmBase):
    """Machine charm that installs prometheus-pushgateway snap."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.reconcile_action, self._on_reconcile_action)

    def _on_install(self, _event):
        self.unit.status = MaintenanceStatus("installing metrics exporter")
        self._reconcile()

    def _on_config_changed(self, _event):
        self.unit.status = MaintenanceStatus("re-applying metrics exporter setup")
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
            self._run_metrics_exporter_config_script()
            self.unit.status = ActiveStatus("metrics exporter configured")
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

    def _run_metrics_exporter_config_script(self):
        script = self._resolve_path(METRICS_EXPORTER_CONFIG_SCRIPT, "metrics exporter configuration script")
        self._run([str(script)], check=True)

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
    main(MetricsExporterCharm)
