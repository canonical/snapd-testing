#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import calculate_tests  # noqa: E402
from calculate_tests import (  # noqa: E402
    _interface_name,
    _load_json,
    _matching_tests_for_directory,
    _matching_tests_for_modified_file,
    _normalize_test_name,
    calculate_tests_to_execute,
)


class CalculateTestsUnitTests(unittest.TestCase):
    def test_normalize_test_name_strips_system_prefix(self) -> None:
        system = "amd64:ubuntu-24.04"
        prefixed_name = f"{system}:tests/spread/smoke"

        self.assertEqual(
            _normalize_test_name(system, prefixed_name),
            "tests/spread/smoke",
        )

    def test_normalize_test_name_keeps_name_without_prefix(self) -> None:
        system = "amd64:ubuntu-24.04"

        self.assertEqual(
            _normalize_test_name(system, "tests/spread/smoke"),
            "tests/spread/smoke",
        )

    def test_load_json_reads_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = {"key": ["value"]}
            path = Path(temp_dir) / "payload.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = _load_json(path)

            self.assertEqual(loaded, payload)

    def test_load_json_raises_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.json"

            with self.assertRaisesRegex(RuntimeError, "missing required file"):
                _load_json(path)

    def test_matching_tests_for_modified_file_exact_match(self) -> None:
        spread_map = {
            "src/a.go": ["tests/spread/a", "tests/spread/common"],
            "src/b.go": ["tests/spread/b"],
        }

        matches = _matching_tests_for_modified_file("src/a.go", spread_map)

        self.assertEqual(matches, {"tests/spread/a", "tests/spread/common"})

    def test_matching_tests_for_modified_file_unknown_returns_empty_set(self) -> None:
        spread_map = {"src/a.go": ["tests/spread/a"]}

        matches = _matching_tests_for_modified_file("src/unknown.go", spread_map)

        self.assertEqual(matches, set())

    def test_matching_tests_for_directory_matches_prefix(self) -> None:
        spread_map = {
            "src/a.go": ["tests/spread/a"],
            "pkg/sub/b.go": ["tests/spread/b", "tests/spread/common"],
            "pkg/sub/c.go": ["tests/spread/c", "tests/spread/common"],
        }

        matches = _matching_tests_for_directory("pkg/sub/", spread_map)

        self.assertEqual(
            matches,
            {"tests/spread/b", "tests/spread/c", "tests/spread/common"},
        )

    def test_calculate_tests_includes_base_and_matching_and_unknown(self) -> None:
        system = "amd64:ubuntu-24.04"
        allowed_tests = [
            f"{system}:tests/spread/base",
            f"{system}:tests/spread/feature-a",
            f"{system}:tests/spread/feature-b",
            f"{system}:tests/spread/not-in-map",
        ]
        changed_files = {
            "modified": ["src/feature_a.go"],
            "added": [],
            "deleted": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "spread-execution-map.json").write_text(
                json.dumps(
                    {
                        system: {
                            "src/feature_a.go": [f"{system}:tests/spread/feature-a"],
                            "src/feature_b.go": [f"{system}:tests/spread/feature-b"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "base-executions.json").write_text(
                json.dumps({system: [f"{system}:tests/spread/base"]}),
                encoding="utf-8",
            )

            selected = calculate_tests_to_execute(
                system=system,
                tests=allowed_tests,
                changed_files=changed_files,
                directory=str(data_dir),
            )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/feature-a",
                f"{system}:tests/spread/not-in-map",
            ],
        )

    def test_calculate_tests_returns_all_when_modified_file_not_in_map(self) -> None:
        system = "amd64:ubuntu-24.04"
        allowed_tests = [
            f"{system}:tests/spread/base",
            f"{system}:tests/spread/feature-a",
        ]
        changed_files = {
            "modified": ["src/unmapped.go"],
            "added": [],
            "deleted": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "spread-execution-map.json").write_text(
                json.dumps({system: {"src/feature_a.go": [f"{system}:tests/spread/feature-a"]}}),
                encoding="utf-8",
            )
            (data_dir / "base-executions.json").write_text(
                json.dumps({system: [f"{system}:tests/spread/base"]}),
                encoding="utf-8",
            )

            selected = calculate_tests_to_execute(
                system=system,
                tests=allowed_tests,
                changed_files=changed_files,
                directory=str(data_dir),
            )

        self.assertEqual(selected, allowed_tests)

    def test_calculate_tests_uses_directory_matching_for_added_and_deleted(self) -> None:
        system = "amd64:ubuntu-24.04"
        allowed_tests = [
            f"{system}:tests/spread/base",
            f"{system}:tests/spread/dir-hit",
            f"{system}:tests/spread/other",
        ]
        changed_files = {
            "modified": [],
            "added": ["pkg/sub/new_file.go"],
            "deleted": ["pkg/sub/old_file.go"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "spread-execution-map.json").write_text(
                json.dumps(
                    {
                        system: {
                            "pkg/sub/existing.go": [f"{system}:tests/spread/dir-hit"],
                            "different/path.go": [f"{system}:tests/spread/other"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "base-executions.json").write_text(
                json.dumps({system: [f"{system}:tests/spread/base"]}),
                encoding="utf-8",
            )

            selected = calculate_tests_to_execute(
                system=system,
                tests=allowed_tests,
                changed_files=changed_files,
                directory=str(data_dir),
            )

        self.assertEqual(
            selected,
            [
                f"{system}:tests/spread/base",
                f"{system}:tests/spread/dir-hit",
            ],
        )


if __name__ == "__main__":
    unittest.main()


class InterfaceUsersTests(unittest.TestCase):
    system = "amd64:ubuntu-24.04"

    def _calculate(self, modified: list[str], interface_users: dict | None) -> list[str]:
        system = self.system
        tests = [
            f"{system}:tests/main/base",
            f"{system}:tests/main/cups-coverage",
            f"{system}:tests/main/cups-connects",
            f"{system}:tests/main/xilinx-connects:v1",
            f"{system}:tests/main/other",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "spread-execution-map.json").write_text(
                json.dumps(
                    {
                        system: {
                            "interfaces/builtin/cups.go": [f"{system}:tests/main/cups-coverage"],
                            "interfaces/builtin/common.go": [
                                f"{system}:tests/main/cups-connects",
                                f"{system}:tests/main/xilinx-connects:v1",
                                f"{system}:tests/main/other",
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            (data_dir / "base-executions.json").write_text(json.dumps({system: ["tests/main/base"]}), encoding="utf-8")
            if interface_users is not None:
                (data_dir / "interface-users.json").write_text(json.dumps(interface_users), encoding="utf-8")
            return calculate_tests_to_execute(
                system=system,
                tests=tests,
                changed_files={"modified": modified, "added": [], "deleted": []},
                directory=str(data_dir),
            )

    def test_interface_name(self) -> None:
        self.assertEqual(_interface_name("interfaces/builtin/cups_control.go"), "cups-control")
        self.assertEqual(_interface_name("interfaces/builtin/autopilot.go"), "autopilot-introspection")
        self.assertIsNone(_interface_name("interfaces/builtin/cups_test.go"))
        self.assertIsNone(_interface_name("interfaces/builtin/sub/file.go"))
        self.assertIsNone(_interface_name("interfaces/apparmor/backend.go"))

    def test_covered_interface_adds_connection_users(self) -> None:
        system = self.system
        users = {system: {"cups": [f"{system}:tests/main/cups-connects"]}}
        self.assertEqual(
            self._calculate(["interfaces/builtin/cups.go"], users),
            [f"{system}:tests/main/base", f"{system}:tests/main/cups-coverage", f"{system}:tests/main/cups-connects"],
        )

    def test_uncovered_interface_uses_connection_users(self) -> None:
        system = self.system
        users = {system: {"xilinx-dma": [f"{system}:tests/main/xilinx-connects:v1"]}}
        self.assertEqual(
            self._calculate(["interfaces/builtin/xilinx_dma.go"], users),
            [f"{system}:tests/main/base", f"{system}:tests/main/xilinx-connects:v1"],
        )

    def test_uncovered_interface_nobody_connects_selects_nothing_extra(self) -> None:
        system = self.system
        self.assertEqual(
            self._calculate(["interfaces/builtin/spi.go"], {system: {}}),
            [f"{system}:tests/main/base"],
        )

    def test_without_interface_data_uncovered_interface_selects_everything(self) -> None:
        system = self.system
        everything = [
            f"{system}:tests/main/base",
            f"{system}:tests/main/cups-coverage",
            f"{system}:tests/main/cups-connects",
            f"{system}:tests/main/xilinx-connects:v1",
            f"{system}:tests/main/other",
        ]
        # no interface-users.json at all, or no entry for this system
        self.assertEqual(self._calculate(["interfaces/builtin/xilinx_dma.go"], None), everything)
        self.assertEqual(self._calculate(["interfaces/builtin/xilinx_dma.go"], {"other:system": {}}), everything)


class CacheTests(unittest.TestCase):
    def test_load_json_caches_until_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data.json"
            path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            first = _load_json(path)
            self.assertIs(_load_json(path), first)

            # a refresh replaces the file
            tmp = Path(temp_dir) / "data.json.tmp"
            tmp.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
            tmp.replace(path)
            second = _load_json(path)
            self.assertEqual(second, {"a": 1, "b": 2})
            self.assertIs(_load_json(path), second)

    def test_calculate_tests_follows_refreshed_map(self) -> None:
        system = "amd64:ubuntu-24.04"
        tests = [f"{system}:tests/main/a", f"{system}:tests/main/b"]
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "base-executions.json").write_text(json.dumps({}), encoding="utf-8")
            map_path = data_dir / "spread-execution-map.json"

            def calculate():
                return calculate_tests_to_execute(
                    system=system,
                    tests=tests,
                    changed_files={"modified": ["src/a.go"], "added": [], "deleted": []},
                    directory=str(data_dir),
                )

            # b has no coverage yet, so it always runs
            map_path.write_text(json.dumps({system: {"src/a.go": [f"{system}:tests/main/a"]}}), encoding="utf-8")
            self.assertEqual(calculate(), tests)

            # after a refresh b has coverage elsewhere, so it is no longer selected
            map_path.write_text(
                json.dumps({system: {"src/a.go": [f"{system}:tests/main/a"], "src/b.go": [f"{system}:tests/main/b"]}}),
                encoding="utf-8",
            )
            self.assertEqual(calculate(), [f"{system}:tests/main/a"])
            self.assertEqual(sum(1 for key in calculate_tests._known_tests_cache if key[1] == system), 1)
