#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from service_api import ApiHandler, TestExecutionService  # noqa: E402


class TestExecutionServiceCalculateTests(unittest.TestCase):
    def _make_service(
        self,
        spread_map: dict,
        base_tests: dict,
    ) -> TestExecutionService:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)

        spread_path = root / "spread-execution-map.json"
        spread_path.write_text(json.dumps(spread_map), encoding="utf-8")

        base_path = root / "base-executions.json"
        base_path.write_text(json.dumps(base_tests), encoding="utf-8")

        config = {
            "spread_tests_execution_path": str(spread_path),
            "base_tests_path": str(base_path),
            "refresh_command": ["true"],
            "refresh_timeout_seconds": 60,
        }
        return TestExecutionService(config)

    def tearDown(self) -> None:
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def test_calculate_tests_supports_structured_changes_and_allowed_tests(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                system: {
                    "snap/foo.go": [f"{system}:tests/spread/mod"],
                    "pkg/sub/existing.go": [f"{system}:tests/spread/dir"],
                    "other/unchanged.go": [f"{system}:tests/spread/manual"],
                }
            },
            base_tests={system: [f"{system}:tests/spread/base"]},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["snap/foo.go"],
                "added": ["pkg/sub/new.go"],
                "deleted": [],
            },
            backend_systems=[system],
            tests=[
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
                f"{system}:tests/spread/dir",
                f"{system}:tests/spread/unknown",
                f"{system}:tests/spread/manual",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
                f"{system}:tests/spread/dir",
                f"{system}:tests/spread/unknown",
            ],
        )

    def test_calculate_tests_returns_all_allowed_on_unmatched_modified(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/spread/mod"]}},
            base_tests={system: [f"{system}:tests/spread/base"]},
        )

        selected = service.calculate_tests(
            changed_files={"modified": ["snap/unmapped.go"], "added": [], "deleted": []},
            backend_systems=[system],
            tests=[
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
                f"{system}:tests/spread/unknown",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
                f"{system}:tests/spread/unknown",
            ],
        )

    def test_calculate_tests_returns_all_on_non_go_changes(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/spread/mod"]}},
            base_tests={system: [f"{system}:tests/spread/base"]},
        )
        tests = [f"{system}:tests/spread/base", f"{system}:tests/spread/mod", f"{system}:tests/spread/other"]

        for path in (
            "cmd/snap-confine/snap-confine.c",
            "cmd/snap-confine/mount-support.h",
            "cmd/snap-confine/snap-confine.apparmor.in",
            "cmd/Makefile.am",
            "build-aux/snap/snapcraft.yaml",
            "packaging/ubuntu-16.04/control",
            "data/systemd/snapd.service.in",
            "c-vendor/squashfuse/ll.c",
            "spread.yaml",
            "go.mod",
            "go.sum",
            "cmd/.gitignore",
            "po/fr.po",
            "run-checks",
        ):
            for key in ("modified", "added", "deleted"):
                with self.subTest(path=path, key=key):
                    changed_files = {"modified": [], "added": [], "deleted": []}
                    changed_files[key] = [path]
                    selected = service.calculate_tests(changed_files=changed_files, backend_systems=[system], tests=tests)
                    self.assertEqual(selected, tests)

        # docs, C unit tests and unit test data do not affect spread tests
        for path in (
            "cmd/snap-confine/README.md",
            "packaging/README.md",
            "HACKING.md",
            "docs/interfaces.rst",
            "cmd/snap-confine/mount-support-test.c",
            "cmd/libsnap-confine-private/utils-test.h",
            "cmd/snap-update-ns/testdata/opt/fstab",
            "overlord/snapstate/snapstatetest/fixture.json",
        ):
            with self.subTest(path=path):
                selected = service.calculate_tests(
                    changed_files={"modified": [path], "added": [], "deleted": []},
                    backend_systems=[system],
                    tests=tests,
                )
                self.assertEqual(selected, [f"{system}:tests/spread/base", f"{system}:tests/spread/other"])

    def test_calculate_tests_ignores_docs_and_github(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/spread/mod"]}},
            base_tests={system: [f"{system}:tests/spread/base"]},
        )
        tests = [f"{system}:tests/spread/base", f"{system}:tests/spread/mod", f"{system}:tests/spread/other"]

        for path in (
            "docs/api/openapi.yaml",
            "docs/api/tools/dark-theme.css",
            "docs/tool.go",
            ".github/workflows/test.yaml",
            ".github/actions/run-spread/action.yaml",
            ".github/scripts/helper.go",
        ):
            for key in ("modified", "added", "deleted"):
                with self.subTest(path=path, key=key):
                    changed_files = {"modified": [], "added": [], "deleted": []}
                    changed_files[key] = [path]
                    selected = service.calculate_tests(changed_files=changed_files, backend_systems=[system], tests=tests)
                    self.assertEqual(selected, [f"{system}:tests/spread/base", f"{system}:tests/spread/other"])

        # they do not hide other changes
        selected = service.calculate_tests(
            changed_files={"modified": ["docs/api/openapi.yaml", "snap/foo.go"], "added": [], "deleted": []},
            backend_systems=[system],
            tests=tests,
        )
        self.assertEqual(selected, tests)

    def test_calculate_tests_ignores_unit_test_packages(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                system: {
                    "snap/foo.go": [f"{system}:tests/spread/mod"],
                    "overlord/snapstate/handlers.go": [f"{system}:tests/spread/dir"],
                    "asserts/asserts.go": [f"{system}:tests/spread/asserts"],
                }
            },
            base_tests={system: [f"{system}:tests/spread/base"]},
        )
        tests = [
            f"{system}:tests/spread/base",
            f"{system}:tests/spread/mod",
            f"{system}:tests/spread/dir",
            f"{system}:tests/spread/asserts",
        ]

        selected = service.calculate_tests(
            changed_files={
                "modified": [
                    "snap/foo.go",
                    "overlord/snapstate/snapstatetest/snapstate.go",
                    "testutil/checkers.go",
                    "testtime/timer.go",
                    "osutil/disks/testdata/fake.go",
                ],
                # unit test helpers do not pull in tests from their parent folder
                "added": ["overlord/snapstate/snapstatetest/new.go"],
                "deleted": [],
            },
            backend_systems=[system],
            tests=tests,
        )
        self.assertEqual(selected, [f"{system}:tests/spread/base", f"{system}:tests/spread/mod"])

        # assertstest is used by spread tests through the fake store, and has no
        # coverage, so a change to it still selects everything
        selected = service.calculate_tests(
            changed_files={"modified": ["asserts/assertstest/assertstest.go"], "added": [], "deleted": []},
            backend_systems=[system],
            tests=tests,
        )
        self.assertEqual(selected, tests)

    def test_calculate_tests_ignores_go_unit_test_files(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                system: {
                    "snap/foo.go": [f"{system}:tests/spread/mod"],
                    "pkg/sub/existing.go": [f"{system}:tests/spread/dir"],
                    "other/thing.go": [f"{system}:tests/spread/other"],
                }
            },
            base_tests={system: [f"{system}:tests/spread/base"]},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["snap/foo.go", "snap/foo_test.go"],
                "added": ["pkg/sub/new_test.go"],
                "deleted": ["other/old_test.go"],
            },
            backend_systems=[system],
            tests=[
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
                f"{system}:tests/spread/dir",
                f"{system}:tests/spread/other",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/mod",
            ],
        )

    def test_calculate_tests_returns_all_allowed_on_changed_test_script(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/spread/mod"]}},
            base_tests={system: [f"{system}:tests/spread/base"]},
        )

        for changed_files in (
            {"modified": [], "added": ["tests/lib/new-helper.sh"], "deleted": []},
            {"modified": ["tests/completion/dirs.sh"], "added": [], "deleted": []},
            {"modified": ["snap/foo.go"], "added": [], "deleted": ["tests/utils/tool.py"]},
        ):
            with self.subTest(changed_files=changed_files):
                selected = service.calculate_tests(
                    changed_files=changed_files,
                    backend_systems=[system],
                    tests=[
                        f"{system}:tests/spread/base",
                        f"{system}:tests/spread/mod",
                        f"{system}:tests/spread/other",
                    ],
                )

                self.assertEqual(
                    selected,
                    [
                        f"{system}:tests/spread/base",
                        f"{system}:tests/spread/mod",
                        f"{system}:tests/spread/other",
                    ],
                )

    def test_calculate_tests_selects_test_owning_changed_files(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                system: {
                    "snap/foo.go": [
                        f"{system}:tests/main/some-test",
                        f"{system}:tests/main/some-test-two",
                        f"{system}:tests/main/other:v1",
                        f"{system}:tests/main/other:v2",
                        f"{system}:tests/main/mod",
                    ]
                }
            },
            base_tests={system: ["tests/main/base"]},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["tests/main/some-test/script.sh", "tests/main/some-test/task.yaml"],
                "added": ["tests/main/other/data/file.txt"],
                "deleted": [],
            },
            backend_systems=[system],
            tests=[
                f"{system}:tests/main/base",
                f"{system}:tests/main/mod",
                f"{system}:tests/main/some-test",
                f"{system}:tests/main/some-test-two",
                f"{system}:tests/main/other:v1",
                f"{system}:tests/main/other:v2",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/main/base",
                f"{system}:tests/main/some-test",
                f"{system}:tests/main/other:v1",
                f"{system}:tests/main/other:v2",
            ],
        )

    def test_calculate_tests_test_folders_follow_spread_suites(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                system: {
                    "snap/foo.go": [
                        f"{system}:tests/nested/manual/nested-test",
                        f"{system}:tests/regression/lp-1",
                        f"{system}:tests/main/mod",
                    ]
                }
            },
            base_tests={},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["tests/nested/manual/nested-test/task.yaml", "tests/regression/lp-1/task.yaml"],
                "added": [],
                # a deleted test, and a test that is not in the input, select nothing
                "deleted": ["tests/main/removed/task.yaml", "tests/smoke/not-listed/task.yaml"],
            },
            backend_systems=[system],
            tests=[
                f"{system}:tests/nested/manual/nested-test",
                f"{system}:tests/regression/lp-1",
                f"{system}:tests/main/mod",
            ],
        )

        self.assertEqual(
            selected,
            [f"{system}:tests/nested/manual/nested-test", f"{system}:tests/regression/lp-1"],
        )

    def test_calculate_tests_selects_new_test(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/main/mod"]}},
            base_tests={system: ["tests/main/base"]},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["snap/foo.go"],
                "added": ["tests/main/new-test/task.yaml"],
                "deleted": [],
            },
            backend_systems=[system],
            tests=[
                f"{system}:tests/main/base",
                f"{system}:tests/main/mod",
                f"{system}:tests/main/new-test",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/main/base",
                f"{system}:tests/main/mod",
                f"{system}:tests/main/new-test",
            ],
        )

    def test_calculate_tests_test_folder_known_from_other_system(self) -> None:
        system_a = "amd64:ubuntu-24.04"
        system_b = "amd64:debian-12"
        service = self._make_service(
            spread_map={
                system_a: {"snap/foo.go": [f"{system_a}:tests/main/only-a", f"{system_a}:tests/main/shared"]},
                system_b: {"snap/foo.go": [f"{system_b}:tests/main/shared"]},
            },
            base_tests={},
        )

        selected = service.calculate_tests(
            changed_files={"modified": ["tests/main/only-a/task.yaml"], "added": [], "deleted": []},
            backend_systems=[system_a, system_b],
            tests=[
                f"{system_a}:tests/main/only-a",
                f"{system_a}:tests/main/shared",
                f"{system_b}:tests/main/shared",
            ],
        )

        self.assertEqual(selected, [f"{system_a}:tests/main/only-a"])

    def test_calculate_tests_returns_all_when_test_folder_and_shared_files_change(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {"snap/foo.go": [f"{system}:tests/main/some-test", f"{system}:tests/main/mod"]}},
            base_tests={},
        )

        selected = service.calculate_tests(
            changed_files={
                "modified": ["tests/main/some-test/task.yaml", "tests/lib/snaps.sh"],
                "added": [],
                "deleted": [],
            },
            backend_systems=[system],
            tests=[f"{system}:tests/main/some-test", f"{system}:tests/main/mod"],
        )

        self.assertEqual(selected, [f"{system}:tests/main/some-test", f"{system}:tests/main/mod"])

    def test_calculate_tests_filters_allowed_tests_per_backend(self) -> None:
        amd64 = "amd64:ubuntu-24.04"
        arm64 = "arm64:ubuntu-24.04"
        service = self._make_service(
            spread_map={
                amd64: {"snap/foo.go": [f"{amd64}:tests/spread/mod"]},
                arm64: {"snap/foo.go": [f"{arm64}:tests/spread/mod"]},
            },
            base_tests={
                amd64: [f"{amd64}:tests/spread/base"],
                arm64: [f"{arm64}:tests/spread/base"],
            },
        )

        selected = service.calculate_tests(
            changed_files={"modified": ["snap/foo.go"], "added": [], "deleted": []},
            backend_systems=[amd64, arm64],
            tests=[
                f"{amd64}:tests/spread/base",
                f"{amd64}:tests/spread/mod",
                f"{arm64}:tests/spread/base",
                f"{arm64}:tests/spread/mod",
            ],
        )

        self.assertEqual(
            selected,
            [
                f"{amd64}:tests/spread/base",
                f"{amd64}:tests/spread/mod",
                f"{arm64}:tests/spread/base",
                f"{arm64}:tests/spread/mod",
            ],
        )

    def test_calculate_tests_returns_all_without_data(self) -> None:
        system = "amd64:ubuntu-24.04"
        tests = [f"{system}:tests/spread/a", f"{system}:tests/spread/b"]
        for missing in ("spread-execution-map.json", "base-executions.json"):
            with self.subTest(missing=missing):
                service = self._make_service(
                    spread_map={system: {"snap/foo.go": [f"{system}:tests/spread/a"]}},
                    base_tests={system: []},
                )
                (Path(self.temp_dir.name) / missing).unlink()
                selected = service.calculate_tests(
                    changed_files={"modified": ["snap/foo.go"], "added": [], "deleted": []},
                    backend_systems=[system],
                    tests=tests,
                )
                self.assertEqual(selected, tests)
                self.tearDown()

    def test_calculate_tests_rejects_invalid_changed_files_type(self) -> None:
        system = "amd64:ubuntu-24.04"
        service = self._make_service(
            spread_map={system: {}},
            base_tests={system: []},
        )

        with self.assertRaisesRegex(RuntimeError, "changed_files must be an object"):
            service.calculate_tests(changed_files="not-valid", backend_systems=[system], tests=[])


if __name__ == "__main__":
    unittest.main()


class ApiHttpTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        (root / "spread-execution-map.json").write_text(json.dumps({}), encoding="utf-8")
        (root / "base-executions.json").write_text(json.dumps({}), encoding="utf-8")
        previous = getattr(ApiHandler, "service", None)
        ApiHandler.service = TestExecutionService({
            "spread_tests_execution_path": str(root / "spread-execution-map.json"),
            "refresh_command": ["true"],
        })
        server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def stop():
            server.shutdown()
            server.server_close()
            ApiHandler.service = previous

        self.addCleanup(stop)
        self.base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def _request(self, path, payload=None):
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_calculate_tests_before_first_refresh(self):
        for name in ("spread-execution-map.json", "base-executions.json"):
            (Path(self.temp_dir.name) / name).unlink()
        status, body = self._request(
            "/calculate-tests",
            {"changed_files": {"modified": ["snap/foo.go"], "added": [], "deleted": []},
             "backend_systems": ["s1"], "tests": ["tests/main/a", "tests/main/b"]},
        )
        self.assertEqual((status, body["tests"]), (200, ["s1:tests/main/a", "s1:tests/main/b"]))

    def test_endpoints(self):
        status, body = self._request(
            "/calculate-tests",
            {"changed_files": {"modified": [], "added": [], "deleted": []}, "backend_systems": ["s1"], "tests": ["tests/main/a"]},
        )
        self.assertEqual((status, body["tests"]), (200, ["s1:tests/main/a"]))
        self.assertEqual(self._request("/add-tests", {"tests": ["tests/main/b"]})[0], 404)
        self.assertEqual(self._request("/health"), (200, {"status": "ok"}))
        self.assertEqual(self._request("/calculate-tests", {"changed_files": []})[0], 400)
        self.assertEqual(self._request("/unknown", {})[0], 404)
