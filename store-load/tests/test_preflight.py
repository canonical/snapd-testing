import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from store_load.credentials import load_credential_pool
from store_load.loaders import load_manifest, load_profile, load_target_catalog
from store_load.models import RequestTemplate, Stage, TargetCatalog, WorkloadManifest
from store_load.preflight import run_preflight
from store_load.workload import compile_execution_plan

FIXTURES = Path(__file__).parent / "fixtures"


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class CredentialTests(unittest.TestCase):
    def test_credential_values_are_not_in_repr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "credentials": [
                            {
                                "id": "device-1",
                                "device_authorization": "Macaroon top-secret",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pool = load_credential_pool(path)

        self.assertNotIn("top-secret", repr(pool))
        self.assertNotIn("top-secret", repr(pool.credentials[0]))


class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(FIXTURES / "workload.json")
        cls.catalog = load_target_catalog(FIXTURES / "targets.toml")
        base_profile = load_profile(FIXTURES / "steady-fleet.toml")
        cls.profile = replace(
            base_profile,
            stages=(Stage("test", clients=2, duration_seconds=10, spawn_rate=2),),
        )

    def _write_credentials(self, path: Path, count: int) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "credentials": [
                        {
                            "id": f"device-{index}",
                            "device_authorization": f"Macaroon device-{index}",
                        }
                        for index in range(count)
                    ],
                }
            ),
            encoding="utf-8",
        )
        os.chmod(path, 0o600)

    def test_offline_preflight_checks_proxy_and_credentials(self) -> None:
        plan = compile_execution_plan(
            self.catalog, "enterprise", self.profile, self.manifest
        )
        with tempfile.TemporaryDirectory() as directory:
            credentials = Path(directory) / "credentials.json"
            self._write_credentials(credentials, 2)
            report = run_preflight(
                plan,
                credentials_path=credentials,
                environment={"HTTPS_PROXY": "http://proxy.example:3128"},
                check_network=False,
            )

        self.assertTrue(report.ok)
        self.assertIn("2 distinct client identities", report.checks[2].detail)

    def test_preflight_reports_missing_runtime_inputs(self) -> None:
        plan = compile_execution_plan(
            self.catalog, "enterprise", self.profile, self.manifest
        )
        report = run_preflight(plan, environment={}, check_network=True)

        self.assertFalse(report.ok)
        self.assertFalse(report.checks[1].ok)
        self.assertFalse(report.checks[2].ok)
        self.assertEqual(
            report.checks[3].detail, "not attempted because proxy check failed"
        )

    def test_network_probe_reaches_local_allowlisted_target(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            local_target = replace(
                self.catalog.get("local"),
                base_url=f"http://127.0.0.1:{server.server_port}",
            )
            catalog = TargetCatalog(version=1, targets={"local": local_target})
            manifest = WorkloadManifest(
                schema_version=1,
                name="anonymous",
                requests=(RequestTemplate("find", "GET", "/v2/snaps/find"),),
                clients=self.manifest.clients,
            )
            plan = compile_execution_plan(catalog, "local", self.profile, manifest)

            report = run_preflight(plan, environment={}, check_network=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertTrue(report.ok)
        self.assertEqual(report.checks[-1].detail, "target responded with HTTP 204")


if __name__ == "__main__":
    unittest.main()