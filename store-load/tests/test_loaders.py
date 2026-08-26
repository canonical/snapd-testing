import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from store_load.cli import main
from store_load.errors import ConfigurationError
from store_load.loaders import load_manifest, load_profile, load_target_catalog
from store_load.models import RequestTemplate, TargetConfig

FIXTURES = Path(__file__).parent / "fixtures"


class LoaderTests(unittest.TestCase):
    def test_loads_representative_configuration(self) -> None:
        manifest = load_manifest(FIXTURES / "workload.json")
        targets = load_target_catalog(FIXTURES / "targets.toml")
        profile = load_profile(FIXTURES / "steady-fleet.toml")

        self.assertEqual(manifest.name, "representative-spread-queries")
        self.assertEqual([request.name for request in manifest.requests], ["refresh", "find"])
        self.assertEqual(targets.get("enterprise").max_clients, 520)
        self.assertEqual(profile.peak_clients, 400)
        self.assertEqual(profile.total_duration_seconds, 2400)
        self.assertAlmostEqual(profile.estimated_peak_requests_per_second, 10 / 3)

    def test_manifest_rejects_sensitive_headers(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "sensitive header"):
            RequestTemplate(
                name="unsafe",
                method="GET",
                path="/v2/snaps/find",
                headers={"Authorization": "Macaroon secret"},
            )

    def test_request_rejects_absolute_url(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "safe relative URL path"):
            RequestTemplate(
                name="off-target",
                method="GET",
                path="https://other.example/v2/snaps/find",
            )

    def test_target_rejects_host_outside_allowlist(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "allowed_hosts"):
            TargetConfig(
                name="wrong-host",
                base_url="https://store.example",
                allowed_hosts=("other.example",),
                protected=True,
                max_clients=10,
                max_duration_seconds=60,
                max_requests_per_second=1,
            )

    def test_loader_reports_json_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text('{"schema_version": 1,', encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, r"broken.json:1:"):
                load_manifest(path)

    def test_loader_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "typo.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "typo",
                        "requets": [],
                        "requests": [
                            {"name": "find", "method": "GET", "path": "/find"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigurationError, "unknown field.*requets"):
                load_manifest(path)


class CliTests(unittest.TestCase):
    def test_validate_command(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            result = main(
                [
                    "validate",
                    "--manifest",
                    str(FIXTURES / "workload.json"),
                    "--targets",
                    str(FIXTURES / "targets.toml"),
                    "--profile",
                    str(FIXTURES / "steady-fleet.toml"),
                ]
            )

        self.assertEqual(result, 0)
        self.assertIn("valid: manifest 'representative-spread-queries'", stdout.getvalue())

    def test_validate_command_reports_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "unsafe",
                        "default_headers": {"Cookie": "secret"},
                        "requests": [
                            {"name": "find", "method": "GET", "path": "/find"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = main(["validate", "--manifest", str(path)])

        self.assertEqual(result, 2)
        self.assertIn("must not contain sensitive header", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()