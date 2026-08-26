import unittest
from dataclasses import replace
from pathlib import Path

from store_load.errors import ConfigurationError, SafetyError
from store_load.loaders import load_manifest, load_profile, load_target_catalog
from store_load.models import RequestTemplate, WorkloadManifest
from store_load.safety import require_target_confirmation
from store_load.workload import compile_execution_plan, render_request

FIXTURES = Path(__file__).parent / "fixtures"


class WorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(FIXTURES / "workload.json")
        cls.catalog = load_target_catalog(FIXTURES / "targets.toml")
        cls.profile = load_profile(FIXTURES / "steady-fleet.toml")

    def test_compiles_execution_plan(self) -> None:
        plan = compile_execution_plan(
            self.catalog, "enterprise", self.profile, self.manifest
        )

        summary = plan.as_dict()
        self.assertEqual(summary["peak_clients"], 400)
        self.assertEqual(summary["total_duration_seconds"], 2400)
        self.assertEqual(summary["requests"][0]["share"], 0.8)
        self.assertEqual(summary["client_templates"][0]["share"], 0.8)

    def test_rejects_profile_above_target_client_limit(self) -> None:
        with self.assertRaisesRegex(SafetyError, "allows at most 50"):
            compile_execution_plan(
                self.catalog, "local", self.profile, self.manifest
            )

    def test_renders_structural_and_text_placeholders(self) -> None:
        target = self.catalog.get("enterprise")
        rendered = render_request(
            target,
            self.manifest,
            self.manifest.requests[0],
            self.manifest.clients[0],
        )

        self.assertEqual(
            rendered.url, "https://enterprise-store.example.invalid/v2/snaps/refresh"
        )
        self.assertEqual(rendered.headers["Snap-Device-Architecture"], "amd64")
        self.assertEqual(rendered.body["context"], [])

    def test_rejects_unknown_placeholder_during_compilation(self) -> None:
        invalid_request = RequestTemplate(
            name="unknown-variable",
            method="GET",
            path="/v2/snaps/info/${snap_name}",
        )
        manifest = replace(self.manifest, requests=(invalid_request,))

        with self.assertRaisesRegex(ConfigurationError, "unknown variable 'snap_name'"):
            compile_execution_plan(
                self.catalog, "enterprise", self.profile, manifest
            )

    def test_rejects_non_scalar_path_placeholder(self) -> None:
        invalid_request = RequestTemplate(
            name="structured-path",
            method="GET",
            path="/v2/${refresh_context}",
        )
        manifest = WorkloadManifest(
            schema_version=1,
            name="invalid-path",
            requests=(invalid_request,),
            clients=(self.manifest.clients[0],),
        )

        with self.assertRaisesRegex(ConfigurationError, "cannot embed non-scalar"):
            compile_execution_plan(
                self.catalog, "enterprise", self.profile, manifest
            )

    def test_protected_target_requires_exact_confirmation(self) -> None:
        target = self.catalog.get("enterprise")
        with self.assertRaisesRegex(SafetyError, "--confirm-target"):
            require_target_confirmation(target, None)
        with self.assertRaises(SafetyError):
            require_target_confirmation(target, "production")

        require_target_confirmation(target, "enterprise")


if __name__ == "__main__":
    unittest.main()