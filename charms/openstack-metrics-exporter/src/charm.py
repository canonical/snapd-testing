#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Charm for installing and configuring OpenStack exporter."""

import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from ops.charm import ActionEvent, CharmBase, SecretChangedEvent
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

logger = logging.getLogger(__name__)

EXPORTER_SNAP_RESOURCE = "exporter-snap"
EXPORTER_CONFIG_SCRIPT = "scripts/configure-openstack-metrics-exporter.sh"
CLOUDS_YAML_TEMPLATE = "config/charm/clouds.yaml"
CLOUDS_YAML_PATH = "/etc/openstack/clouds.yaml"
SECRET_KEY_ALIASES = {
    "OS_AUTH_URL": ("os-auth-url", "auth_url", "OS_AUTH_URL"),
    "OS_PROJECT_NAME": ("os-project-name", "project_name", "OS_PROJECT_NAME"),
    "OS_USERNAME": ("os-username", "username", "OS_USERNAME"),
    "OS_PASSWORD": ("os-password", "password", "OS_PASSWORD"),
    "OS_USER_DOMAIN_NAME": (
        "os-user-domain-name",
        "user_domain_name",
        "OS_USER_DOMAIN_NAME",
    ),
    "OS_PROJECT_DOMAIN_NAME": (
        "os-project-domain-name",
        "project_domain_name",
        "OS_PROJECT_DOMAIN_NAME",
    ),
    "OS_REGION_NAME": ("os-region-name", "region_name", "OS_REGION_NAME"),
    "OS_TENANT_ID": ("os-tenant-id", "tenant_id", "OS_TENANT_ID"),
}


class OpenstackMetricsExporterCharm(CharmBase):
    """Machine charm that installs and configures exporter snap."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.reconcile_action, self._on_reconcile_action)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)

    def _on_install(self, _event):
        self.unit.status = MaintenanceStatus("installing exporter")
        self._reconcile()

    def _on_config_changed(self, _event):
        self.unit.status = MaintenanceStatus("applying updated configuration")
        self._reconcile()

    def _on_reconcile_action(self, event: ActionEvent):
        try:
            self._reconcile()
            event.set_results({"result": "reconcile completed"})
        except (subprocess.CalledProcessError, RuntimeError, FileNotFoundError) as exc:
            event.fail(str(exc))

    def _on_secret_changed(self, _event: SecretChangedEvent):
        logger.info("OpenStack credentials secret changed, reconciling")
        self._reconcile()

    def _reconcile(self):
        try:
            self._apply_proxy_settings()
            self._ensure_snapd()
            self._ensure_exporter_snap()
            self._generate_clouds_yaml()
            self._run_exporter_config_script()
            self.unit.status = ActiveStatus("exporter configured")
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

    def _apply_proxy_settings(self):
        """Apply HTTP/HTTPS proxy settings to the system environment and snapd."""
        http_proxy = str(self.config.get("http-proxy", "")).strip()
        https_proxy = str(self.config.get("https-proxy", "")).strip()
        no_proxy = str(self.config.get("no-proxy", "")).strip()

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

        env_file = "/etc/environment"
        env_content = ""
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                env_content = f.read()
        except FileNotFoundError:
            pass

        proxy_vars = ("HTTP_PROXY=", "HTTPS_PROXY=", "NO_PROXY=", "http_proxy=", "https_proxy=", "no_proxy=")
        lines = [line for line in env_content.split("\n") if not line.startswith(proxy_vars)]

        for var in env_vars:
            key, value = var.split("=", 1)
            lines.append(f'{key}="{value}"')

        new_content = "\n".join(lines) + ("\n" if lines else "")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info("Proxy settings applied to %s", env_file)

        snapd_service_dir = "/etc/systemd/system/snapd.service.d"
        os.makedirs(snapd_service_dir, mode=0o755, exist_ok=True)

        snapd_proxy_conf = f"""[Service]
Environment={' '.join(env_vars)}
"""
        snapd_proxy_file = os.path.join(snapd_service_dir, "proxy.conf")
        with open(snapd_proxy_file, "w", encoding="utf-8") as f:
            f.write(snapd_proxy_conf)
        logger.info("Snapd proxy configuration written to %s", snapd_proxy_file)

        self._run(["systemctl", "daemon-reload"], check=True)
        self._run(["systemctl", "restart", "snapd"], check=True)

    def _ensure_exporter_snap(self):
        snap_name = str(self.config["exporter-snap-name"]).strip() or "golang-openstack-metrics-exporter"

        result = self._run(["snap", "list", snap_name], check=False)
        if result.returncode == 0:
            logger.info("Snap %s already installed", snap_name)
            return

        try:
            snap_path = self.model.resources.fetch(EXPORTER_SNAP_RESOURCE)
        except Exception as exc:
            raise FileNotFoundError(
                f"Snap resource '{EXPORTER_SNAP_RESOURCE}' is not available; "
                "attach it with: juju attach-resource <app> exporter-snap=/path/to/exporter.snap"
            ) from exc

        if not snap_path.exists() or snap_path.suffix != ".snap":
            raise FileNotFoundError(
                f"Snap resource '{EXPORTER_SNAP_RESOURCE}' is invalid: expected a .snap file, got {snap_path}"
            )

        logger.info("Installing snap from %s", snap_path)
        self._run(["snap", "install", "--dangerous", str(snap_path)], check=True)

    def _generate_clouds_yaml(self):
        """Generate clouds.yaml from OpenStack credentials secret."""
        secret_id = str(self.config["secret-id"]).strip()
        project = str(self.config["project"]).strip()
        if not secret_id:
            logger.info("No OpenStack secret configured, skipping clouds.yaml generation")
            return
        if not project:
            logger.info("No OpenStack project configured, skipping clouds.yaml generation")
            return

        try:
            openstack_creds = self._get_openstack_secret(secret_id)
        except RuntimeError as e:
            logger.warning("Failed to load OpenStack credentials from secret: %s", str(e))
            return

        self._generate_clouds_yaml_from_secret(openstack_creds, project)
        logger.info("Generated clouds.yaml from secret")

    def _get_openstack_secret(self, secret_ref: str) -> dict[str, str]:
        """Load OpenStack credentials from Juju secret by ID."""
        try:
            secret = self.model.get_secret(id=secret_ref)
            content = secret.get_content(refresh=True)

            normalized_content: dict[str, str] = {}
            missing_fields: list[str] = []
            for env_name, aliases in SECRET_KEY_ALIASES.items():
                value = next((content[key] for key in aliases if key in content), None)
                if value is None:
                    missing_fields.append(env_name)
                    continue
                normalized_content[env_name] = value

            if missing_fields:
                raise RuntimeError(f"OpenStack secret missing fields: {', '.join(missing_fields)}")

            return normalized_content
        except TypeError as e:
            raise RuntimeError(f"Could not access secret {secret_ref}: {str(e)}") from e

    def _generate_clouds_yaml_from_secret(self, creds: dict[str, str], project: str):
        """Generate clouds.yaml from secret credentials."""
        template = self._resolve_path(CLOUDS_YAML_TEMPLATE, "clouds.yaml template")

        with open(template, "r", encoding="utf-8") as f:
            content = f.read()

        # Replace placeholders
        replacements = {
            "#PROJECT#": project,
            "#OS_AUTH_URL#": creds.get("OS_AUTH_URL", ""),
            "#OS_PROJECT_NAME#": creds.get("OS_PROJECT_NAME", ""),
            "#OS_USERNAME#": creds.get("OS_USERNAME", ""),
            "#OS_PASSWORD#": creds.get("OS_PASSWORD", ""),
            "#OS_USER_DOMAIN_NAME#": creds.get("OS_USER_DOMAIN_NAME", ""),
            "#OS_PROJECT_DOMAIN_NAME#": creds.get("OS_PROJECT_DOMAIN_NAME", ""),
            "#OS_REGION_NAME#": creds.get("OS_REGION_NAME", ""),
        }

        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        # Write to temporary file and move atomically
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            self._run(["sudo", "mkdir", "-p", str(Path(CLOUDS_YAML_PATH).parent)], check=True)
            self._run(["sudo", "mv", tmp_path, CLOUDS_YAML_PATH], check=True)
            self._run(["sudo", "chmod", "644", CLOUDS_YAML_PATH], check=True)
        except subprocess.CalledProcessError:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _run_exporter_config_script(self):
        script = self._resolve_path(EXPORTER_CONFIG_SCRIPT, "exporter configuration script")
        secret_id = str(self.config["secret-id"]).strip()
        project = str(self.config["project"]).strip()
        if not secret_id:
            raise RuntimeError("secret-id must be configured")
        if not project:
            raise RuntimeError("project must be configured")

        try:
            openstack_creds = self._get_openstack_secret(secret_id)
        except RuntimeError as e:
            raise RuntimeError(f"Failed to load OpenStack credentials from secret: {str(e)}") from e

        tenant_id = openstack_creds.get("OS_TENANT_ID", "").strip()
        if not tenant_id:
            raise RuntimeError("OS_TENANT_ID is missing from OpenStack secret")

        env = {
            "EXPORTER_SNAP_NAME": str(self.config["exporter-snap-name"]).strip() or "golang-openstack-metrics-exporter",
            "PROJECT": project,
            "OS_TENANT_ID": tenant_id,
        }

        self._run([str(script)], env=env, check=True)

    def _resolve_path(self, configured_path: str, description: str) -> Path:
        if not configured_path:
            raise FileNotFoundError("script path is empty")

        script = Path(configured_path)
        if script.is_absolute() and script.exists():
            return script

        charm_relative = Path(self.charm_dir) / configured_path
        if charm_relative.exists():
            return charm_relative

        raise FileNotFoundError(f"{description} not found: {configured_path}")

    def _run(self, cmd, env=None, check=False, cwd=None):
        cmd_display = cmd if isinstance(cmd, str) else shlex.join(cmd)
        logger.info("Running command: %s", cmd_display)
        run_env = os.environ.copy()

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
    main(OpenstackMetricsExporterCharm)
