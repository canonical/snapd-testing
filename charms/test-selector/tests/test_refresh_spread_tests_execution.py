#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import refresh_spread_tests_execution as refresh  # noqa: E402


class FeatureQueryTests(unittest.TestCase):
    @patch("refresh_spread_tests_execution.subprocess.run")
    def test_query_features_builds_command_and_parses_json(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=0, stdout='{"ok": true}', stderr="")
        fq = refresh.FeatureQuery("/tmp/query_features.py", "/tmp/creds.json")

        result = fq.query_features("feat sys", "-t 123 -s amd64")

        self.assertEqual(result, {"ok": True})
        mock_run.assert_called_once_with(
            [
                sys.executable,
                "/tmp/query_features.py",
                "feat",
                "sys",
                "-f",
                "/tmp/creds.json",
                "-t",
                "123",
                "-s",
                "amd64",
            ],
            capture_output=True,
            text=True,
        )

    @patch("refresh_spread_tests_execution.subprocess.run")
    def test_query_features_raises_on_non_zero_exit(self, mock_run: Mock) -> None:
        mock_run.return_value = Mock(returncode=2, stdout="", stderr="boom")
        fq = refresh.FeatureQuery("/tmp/query_features.py", "/tmp/creds.json")

        with self.assertRaisesRegex(RuntimeError, "failed with exit code 2"):
            fq.query_features("list")


class FetchSnapdFeaturesTests(unittest.TestCase):
    def test_fetch_new_and_existing_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapd_dir = Path(temp_dir) / "snapd"
            commands = []

            def run(cmd):
                commands.append(cmd)
                if cmd[:2] == ["git", "init"]:
                    (snapd_dir / ".git").mkdir(parents=True)
                if "checkout" in cmd:
                    (snapd_dir / refresh.FEATURES_SUBDIR).mkdir(parents=True, exist_ok=True)
                    (snapd_dir / refresh.FEATURES_SUBDIR / "query_features.py").write_text("")

            repo = str(snapd_dir)
            path = refresh.fetch_snapd_features(snapd_dir, "https://example.com/snapd.git", "main", run)
            self.assertEqual(path, snapd_dir / "tests/utils/features/query_features.py")
            self.assertEqual(commands, [
                ["git", "init", "-q", repo],
                ["git", "-C", repo, "remote", "add", "origin", "https://example.com/snapd.git"],
                ["git", "-C", repo, "sparse-checkout", "set", "tests/utils/features"],
                ["git", "-C", repo, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", "main"],
                ["git", "-C", repo, "checkout", "-q", "--force", "FETCH_HEAD"],
            ])

            commands.clear()
            refresh.fetch_snapd_features(snapd_dir, "https://example.com/fork.git", "branch", run)
            self.assertEqual(commands, [
                ["git", "-C", repo, "remote", "set-url", "origin", "https://example.com/fork.git"],
                ["git", "-C", repo, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", "branch"],
                ["git", "-C", repo, "checkout", "-q", "--force", "FETCH_HEAD"],
            ])

    def test_fetch_fails_without_query_features(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "query_features.py not found"):
                refresh.fetch_snapd_features(Path(temp_dir), "https://example.com/snapd.git", "main", lambda cmd: None)


class LatestTimestampTests(unittest.TestCase):
    def test_latest_timestamp_and_systems_returns_first_entry(self) -> None:
        fq = Mock()
        fq.query_features.return_value = [
            {"timestamp": 12345, "systems": ["s1", "s2"]},
            {"timestamp": 99999, "systems": ["s3"]},
        ]

        timestamp, systems = refresh._latest_timestamp_and_systems(fq)

        self.assertEqual(timestamp, "12345")
        self.assertEqual(systems, ["s1", "s2"])

    def test_latest_timestamp_and_systems_raises_on_empty(self) -> None:
        fq = Mock()
        fq.query_features.return_value = []

        with self.assertRaisesRegex(RuntimeError, "returned no timestamps"):
            refresh._latest_timestamp_and_systems(fq)

    def test_latest_timestamp_and_systems_raises_when_timestamp_missing(self) -> None:
        fq = Mock()
        fq.query_features.return_value = [{"systems": ["s1"]}]

        with self.assertRaisesRegex(RuntimeError, "missing timestamp"):
            refresh._latest_timestamp_and_systems(fq)


class NamingTests(unittest.TestCase):
    def test_get_test_name_variants(self) -> None:
        self.assertEqual(
            refresh.get_test_name("amd64:ubuntu-24.04", "tests/spread/foo", "", ""),
            "amd64:ubuntu-24.04:tests/spread/foo",
        )
        self.assertEqual(
            refresh.get_test_name("amd64:ubuntu-24.04", "tests/spread/foo", "task", ""),
            "amd64:ubuntu-24.04:tests/spread/foo/task",
        )
        self.assertEqual(
            refresh.get_test_name("amd64:ubuntu-24.04", "tests/spread/foo", "task", "v1"),
            "amd64:ubuntu-24.04:tests/spread/foo/task:v1",
        )


COVERAGE_TESTS = [
    {
        "suite": "tests/spread/a",
        "task_name": "",
        "variant": "",
        "coverages": [
            {"file": "snap/foo.go", "func": "Run"},
            {"file": "snap/foo.go", "func": "init"},
            {"file": "interfaces/bar.go", "func": "Name"},
            {"file": "interfaces/bar.go", "func": "StaticInfo"},
            {"file": "interfaces/bar.go", "func": "Other"},
        ],
    },
    {
        "suite": "tests/spread/b",
        "task_name": "task",
        "variant": "v",
        "coverages": [{"file": "snap/foo.go", "func": "Do"}],
    },
    {
        "suite": "tests/spread/ignored",
        "task_name": "",
        "variant": "",
    },
    {
        "suite": "tests/spread/failed",
        "task_name": "task",
        "variant": "",
        "success": False,
        "coverages": [{"file": "snap/failed-only.go", "func": "Do"}],
    },
]

INTERFACE_TESTS = [
    {
        "suite": "tests/main",
        "task_name": "a",
        "variant": "",
        "success": True,
        "interfaces": [
            {"name": "home", "plug_snap_type": "app", "slot_snap_type": "snapd"},
            {"name": "cups-control", "plug_snap_type": "app", "slot_snap_type": "app"},
        ],
    },
    {
        "suite": "tests/main",
        "task_name": "b",
        "variant": "v1",
        "interfaces": [{"name": "home", "plug_snap_type": "app", "slot_snap_type": "snapd"}],
    },
    {
        "suite": "tests/main",
        "task_name": "failed",
        "variant": "",
        "success": False,
        "interfaces": [{"name": "home", "plug_snap_type": "app", "slot_snap_type": "snapd"}],
    },
    {"suite": "tests/main", "task_name": "none", "variant": ""},
]


class SystemExecutionMapTests(unittest.TestCase):
    def test_system_execution_map_filters_coverage(self) -> None:
        output = refresh.system_execution_map("s1", COVERAGE_TESTS)

        self.assertEqual(output["snap/foo.go"], ["s1:tests/spread/a", "s1:tests/spread/b/task:v"])
        self.assertEqual(output["interfaces/bar.go"], ["s1:tests/spread/a"])
        self.assertNotIn("snap/failed-only.go", output)


class SystemInterfaceUsersTests(unittest.TestCase):
    def test_system_interface_users(self) -> None:
        output = refresh.system_interface_users("s1", INTERFACE_TESTS)

        self.assertEqual(
            output,
            {
                "cups-control": ["s1:tests/main/a"],
                "home": ["s1:tests/main/a", "s1:tests/main/b:v1"],
            },
        )


class BuildSystemDataTests(unittest.TestCase):
    def test_build_system_data_queries_each_system_once(self) -> None:
        responses = {
            "s1": {"tests": COVERAGE_TESTS + INTERFACE_TESTS},
            "s2": {},
            "s3": {"tests": [{"suite": "tests/spread/c", "task_name": "task", "variant": "",
                              "coverages": [{"file": "snap/baz.go", "func": "Do"}]}]},
        }
        fq = Mock()
        fq.query_features.side_effect = lambda command, args: responses[args.split()[3]]

        for jobs in (1, 3):
            with self.subTest(jobs=jobs):
                fq.query_features.reset_mock()
                execution_map, interface_users = refresh.build_system_data(fq, "ts-1", ["s1", "s2", "s3"], jobs)

                self.assertEqual(set(execution_map), {"s1", "s3"})
                self.assertEqual(execution_map["s1"]["snap/foo.go"], ["s1:tests/spread/a", "s1:tests/spread/b/task:v"])
                self.assertEqual(execution_map["s3"], {"snap/baz.go": ["s3:tests/spread/c/task"]})
                self.assertEqual(interface_users["s1"]["home"], ["s1:tests/main/a", "s1:tests/main/b:v1"])
                self.assertEqual(interface_users["s3"], {})
                self.assertEqual(
                    sorted(call.args for call in fq.query_features.call_args_list),
                    [("feat sys", f"-t ts-1 -s {system} --exclude cmds endpoints ensures tasks changes")
                     for system in ("s1", "s2", "s3")],
                )


class BuildBaseTests(unittest.TestCase):
    def test_build_base_tests_without_exclude(self) -> None:
        fq = Mock()
        fq.query_features.return_value = {"s1": ["s1:tests/spread/base"]}

        output = refresh.build_base_tests(fq, "ts-base", max_minutes=30, exclude=[])

        self.assertEqual(output, {"s1": ["s1:tests/spread/base"]})
        fq.query_features.assert_called_once_with("feat cover", "-t ts-base --max-minutes 30")

    def test_build_base_tests_with_exclude(self) -> None:
        fq = Mock()
        refresh.build_base_tests(fq, "ts-base", max_minutes=15, exclude=["coverages", "changes"])
        fq.query_features.assert_called_once_with("feat cover", "-t ts-base --max-minutes 15 --exclude coverages changes")

    def test_build_base_tests_with_force_match_keywords(self) -> None:
        fq = Mock()
        refresh.build_base_tests(fq, "ts-base", max_minutes=30, exclude=["coverages"], force_match_keywords=["refresh"])
        fq.query_features.assert_called_once_with(
            "feat cover", "-t ts-base --max-minutes 30 --force-match-keywords refresh --exclude coverages"
        )

    def test_build_base_tests_ignores_force_match_keywords_without_max_minutes(self) -> None:
        fq = Mock()
        refresh.build_base_tests(fq, "ts-base", max_minutes=0, exclude=["coverages"], force_match_keywords=["refresh"])
        fq.query_features.assert_called_once_with("feat cover", "-t ts-base --max-minutes 0 --exclude coverages")


class MainTests(unittest.TestCase):
    def test_main_writes_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_path = Path(temp_dir) / "query_features.py"
            creds_path = Path(temp_dir) / "creds.json"
            output_dir = Path(temp_dir) / "out"
            query_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            creds_path.write_text("{}", encoding="utf-8")

            execution_map = {"s1": {"snap/foo.go": ["s1:tests/spread/a"]}}
            base_tests = {"s1": ["s1:tests/spread/base"]}
            interface_users = {"s1": {"home": ["s1:tests/spread/a"]}}

            argv = [
                "prog",
                "--query-features-path",
                str(query_path),
                "--credentials-path",
                str(creds_path),
                "--output-dir",
                str(output_dir),
                "--base-tests-max-minutes",
                "42",
            ]

            with patch.object(sys, "argv", argv + ["--jobs", "3"]), patch(
                "refresh_spread_tests_execution._latest_timestamp_and_systems",
                return_value=("ts-1", ["s1"]),
            ) as latest_mock, patch(
                "refresh_spread_tests_execution.build_system_data",
                return_value=(execution_map, interface_users),
            ) as data_mock, patch(
                "refresh_spread_tests_execution.build_base_tests",
                return_value=base_tests,
            ) as base_mock:
                refresh.main()

            # the latest timestamp is looked up once and used for everything
            latest_mock.assert_called_once()
            self.assertEqual(data_mock.call_args.args[1:], ("ts-1", ["s1"], 3))
            self.assertEqual(base_mock.call_args.args[1:4], ("ts-1", 42, ["coverages"]))

            written_execution_map = json.loads((output_dir / "spread-execution-map.json").read_text(encoding="utf-8"))
            written_base_tests = json.loads((output_dir / "base-executions.json").read_text(encoding="utf-8"))

            self.assertEqual(written_execution_map, execution_map)
            self.assertEqual(written_base_tests, base_tests)
            written_interface_users = json.loads((output_dir / "interface-users.json").read_text(encoding="utf-8"))
            self.assertEqual(written_interface_users, interface_users)

    def test_main_fetches_features_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            creds_path = Path(temp_dir) / "creds.json"
            creds_path.write_text("{}", encoding="utf-8")
            snapd_dir = Path(temp_dir) / "snapd"
            query_path = snapd_dir / "tests/utils/features/query_features.py"
            argv = [
                "prog",
                "--query-features-path", str(query_path),
                "--credentials-path", str(creds_path),
                "--output-dir", str(Path(temp_dir) / "out"),
                "--snapd-dir", str(snapd_dir),
                "--snapd-repo-url", "https://example.com/snapd.git",
                "--snapd-ref", "main",
            ]

            def fetch(path, url, ref):
                # query_features.py only exists once fetched
                query_path.parent.mkdir(parents=True)
                query_path.write_text("")
                return query_path

            with patch.object(sys, "argv", argv), patch(
                "refresh_spread_tests_execution.fetch_snapd_features", side_effect=fetch
            ) as fetch_mock, patch(
                "refresh_spread_tests_execution._latest_timestamp_and_systems", return_value=("ts-1", [])
            ), patch(
                "refresh_spread_tests_execution.build_system_data", return_value=({}, {})
            ), patch(
                "refresh_spread_tests_execution.build_base_tests", return_value={}
            ):
                refresh.main()
            fetch_mock.assert_called_once_with(snapd_dir, "https://example.com/snapd.git", "main")

    def test_main_rejects_partial_fetch_options(self) -> None:
        argv = ["prog", "--query-features-path", "q.py", "--credentials-path", "c.json", "--output-dir", "out",
                "--snapd-dir", "/tmp/snapd"]
        with patch.object(sys, "argv", argv):
            with self.assertRaisesRegex(RuntimeError, "must be given together"):
                refresh.main()

    def test_main_raises_when_query_features_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            creds_path = Path(temp_dir) / "creds.json"
            creds_path.write_text("{}", encoding="utf-8")

            argv = [
                "prog",
                "--query-features-path",
                str(Path(temp_dir) / "missing_query_features.py"),
                "--credentials-path",
                str(creds_path),
                "--output-dir",
                str(Path(temp_dir) / "out"),
            ]

            with patch.object(sys, "argv", argv), self.assertRaisesRegex(RuntimeError, "query_features.py not found"):
                refresh.main()

    def test_main_raises_when_credentials_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            query_path = Path(temp_dir) / "query_features.py"
            query_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            argv = [
                "prog",
                "--query-features-path",
                str(query_path),
                "--credentials-path",
                str(Path(temp_dir) / "missing_creds.json"),
                "--output-dir",
                str(Path(temp_dir) / "out"),
            ]

            with patch.object(sys, "argv", argv), self.assertRaisesRegex(RuntimeError, "credentials file not found"):
                refresh.main()


if __name__ == "__main__":
    unittest.main()
