#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""HTTP API for test execution list operations."""

import argparse
import json
import logging
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from calculate_tests import calculate_tests_to_execute

logger = logging.getLogger(__name__)

# Spread suites in the snapd repository. Each folder directly under a suite is
# a test, so a changed file at tests/<suite>/<test>/... belongs to that test.
SPREAD_SUITES = (
    "tests/completion",
    "tests/core",
    "tests/cross",
    "tests/cross-distro-reexec",
    "tests/fips-deb",
    "tests/fips-snap",
    "tests/main",
    "tests/nested/classic",
    "tests/nested/core",
    "tests/nested/manual",
    "tests/nightly",
    "tests/perf/main",
    "tests/perf/nested",
    "tests/regression",
    "tests/release",
    "tests/smoke",
    "tests/unit",
    "tests/upgrade",
)

# Go packages that only unit tests use: any folder ending in "test" (e.g.
# overlord/snapstate/snapstatetest) plus these, and testdata folders, which Go
# builds ignore. Like _test.go files they cannot affect spread tests.
UNIT_TEST_PACKAGES = ("testutil", "testtime", "testdata")
# Test helper packages that spread tests do use, through programs in tests/lib
# such as the fake store.
SPREAD_USED_TEST_PACKAGES = ("asserts/assertstest",)

# Documentation cannot affect spread tests.
DOC_EXTENSIONS = (".md", ".rst")
# Folders whose changes never affect spread tests and so are ignored entirely:
# the API documentation site and the GitHub CI configuration.
IGNORED_PREFIXES = ("docs/", ".github/")
# C unit test files, which only affect unit tests.
C_UNIT_TEST_SUFFIXES = ("-test.c", "-test.h")


class TestExecutionService:
    """Core logic behind API operations."""

    def __init__(self, config: dict[str, Any]):
        self.spread_tests_execution_path = Path(config["spread_tests_execution_path"])
        self.refresh_command = config["refresh_command"]
        self.refresh_timeout_seconds = int(config.get("refresh_timeout_seconds", 300))
        self._refresh_lock = threading.Lock()

    @staticmethod
    def _is_go_source(path: str) -> bool:
        # Go unit test files and unit test helper packages never appear in
        # spread coverage, so treating them as unmatched would select every test.
        if not path.endswith(".go") or path.endswith("_test.go"):
            return False
        return not TestExecutionService._is_unit_test_package(path)

    @staticmethod
    def _is_unit_test_package(path: str) -> bool:
        package = path.rsplit("/", 1)[0] if "/" in path else ""
        if any(package == p or package.startswith(p + "/") for p in SPREAD_USED_TEST_PACKAGES):
            return False
        return any(part.endswith("test") or part in UNIT_TEST_PACKAGES for part in package.split("/") if part)

    @staticmethod
    def _is_test_script(path: str) -> bool:
        return path.startswith("tests/")

    @staticmethod
    def _test_dir(system: str, test: str) -> str:
        """Return the folder of a system-prefixed test, without its variant."""
        return test[len(system) + 1:].split(":", 1)[0]

    @staticmethod
    def _owning_test_dir(path: str) -> str | None:
        """Return the test folder a file is in, or None if it is not in one."""
        for suite in SPREAD_SUITES:
            rest = path[len(suite) + 1:] if path.startswith(suite + "/") else None
            if rest and "/" in rest:
                return f"{suite}/{rest.split('/', 1)[0]}"
        return None

    @staticmethod
    def _is_ignored(path: str) -> bool:
        return path.startswith(IGNORED_PREFIXES)

    @staticmethod
    def _requires_all_tests(path: str) -> bool:
        """
        Whether a changed file requires running every test. Non-Go files have
        no coverage data, so any change to one (C code, packaging, data files,
        spread.yaml, go.mod, ...) runs everything, except for the files with
        rules of their own: spread tests and their helpers under tests/,
        documentation, and files that only unit tests use.
        """
        if path.endswith(".go") or TestExecutionService._is_test_script(path):
            return False
        if path.endswith(DOC_EXTENSIONS) or path.endswith(C_UNIT_TEST_SUFFIXES):
            return False
        return not TestExecutionService._is_unit_test_package(path)

    def _missing_data_files(self) -> list[str]:
        data_dir = self.spread_tests_execution_path.parent
        required = (self.spread_tests_execution_path, data_dir / "base-executions.json")
        return [str(path) for path in required if not path.exists()]

    @staticmethod
    def _normalize_changed_files(changed_files: Any) -> dict[str, list[str]]:
        if not isinstance(changed_files, dict):
            raise RuntimeError("changed_files must be an object with modified/added/deleted lists")

        normalized: dict[str, list[str]] = {}
        for key in ("modified", "added", "deleted"):
            raw = changed_files.get(key, [])
            if not isinstance(raw, list):
                raise RuntimeError(f"changed_files.{key} must be a list")
            normalized[key] = [str(path) for path in raw if isinstance(path, str)]
        return normalized

    @staticmethod
    def _tests_for_system(system: str, tests: list[str]) -> list[str]:
        selected: list[str] = []
        system_prefix = f"{system}:"
        for test in tests:
            value = str(test).strip()
            if not value:
                continue
            if value.startswith(system_prefix):
                selected.append(value)
                continue
            if ":" in value:
                continue
            selected.append(f"{system}:{value}")
        return selected

    @staticmethod
    def _unique_keep_order(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    def calculate_tests(self, changed_files: Any, backend_systems: list[str], tests: list[str]) -> list[str]:
        changed_files_by_type = {
            key: [path for path in paths if not self._is_ignored(path)]
            for key, paths in self._normalize_changed_files(changed_files).items()
        }

        allowed_by_system: dict[str, list[str]] = {
            backend_system: self._unique_keep_order(self._tests_for_system(backend_system, tests))
            for backend_system in backend_systems
        }

        # Spread tests and their helpers are not part of the coverage data. A
        # change inside a test's own folder selects that test where it is
        # allowed; any other change under tests/ (e.g. shared helpers in
        # tests/lib or files directly in a suite folder) requires running
        # everything.
        changed_test_dirs: set[str] = set()
        run_all = False
        missing_data = self._missing_data_files()
        if missing_data:
            # e.g. before the first refresh: without data nothing can be
            # excluded, so run everything rather than failing the request
            logger.warning("execution data missing (%s), returning all allowed tests", ", ".join(missing_data))
            run_all = True
        for paths in changed_files_by_type.values():
            for path in paths:
                if self._requires_all_tests(path):
                    run_all = True
                    continue
                if not self._is_test_script(path):
                    continue
                owner = self._owning_test_dir(path)
                if owner is None:
                    run_all = True
                else:
                    changed_test_dirs.add(owner)

        for key, paths in changed_files_by_type.items():
            changed_files_by_type[key] = [path for path in paths if self._is_go_source(path)]

        resolved_tests: list[str] = []
        for backend_system, allowed_tests in allowed_by_system.items():
            if run_all:
                resolved_tests.extend(allowed_tests)
                continue

            selected = set(
                calculate_tests_to_execute(
                    system=backend_system,
                    tests=allowed_tests,
                    changed_files=changed_files_by_type,
                    directory=str(self.spread_tests_execution_path.parent),
                )
            )
            resolved_tests.extend(
                test
                for test in allowed_tests
                if test in selected
                or self._test_dir(backend_system, test) in changed_test_dirs
            )

        return self._unique_keep_order(resolved_tests)

    def mongodb_updated(self) -> None:
        with self._refresh_lock:
            subprocess.run(self.refresh_command, check=True, capture_output=True, text=True, timeout=self.refresh_timeout_seconds)


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP handlers for the test execution API."""

    service: TestExecutionService

    def log_message(self, fmt: str, *args):
        logger.info("api %s - %s", self.address_string(), fmt % args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        payload = json.loads(raw.decode("utf-8") if raw else "{}")
        if not isinstance(payload, dict):
            raise RuntimeError("request payload must be a json object")
        return payload

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        try:
            payload = self._read_json()
            if self.path == "/calculate-tests":
                changed_files = payload.get("changed_files", [])
                backend_systems = payload.get("backend_systems", [])
                tests = payload.get("tests", [])
                if not isinstance(changed_files, dict):
                    raise RuntimeError("changed_files must be an object with modified/added/deleted lists")
                if not isinstance(backend_systems, list):
                    raise RuntimeError("backend_systems must be a list")
                if not isinstance(tests, list):
                    raise RuntimeError("tests must be a list")
                tests = self.service.calculate_tests(
                    changed_files,
                    [str(item) for item in backend_systems],
                    [str(item) for item in tests],
                )
                self._write_json(HTTPStatus.OK, {"tests": tests, "count": len(tests)})
                return

            if self.path == "/mongodb-updated":
                self.service.mongodb_updated()
                self._write_json(HTTPStatus.OK, {"result": "ok"})
                return

            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except subprocess.CalledProcessError as exc:
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": "refresh command failed",
                    "returncode": exc.returncode,
                    "stdout": (exc.stdout or "").strip(),
                    "stderr": (exc.stderr or "").strip(),
                },
            )
        except subprocess.TimeoutExpired:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "refresh command timed out"})
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="spread-test-selector api service")
    parser.add_argument("--config", required=True, type=str)
    parser.add_argument("--host", default="127.0.0.1", type=str)
    parser.add_argument("--port", default=8181, type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    ApiHandler.service = TestExecutionService(config)

    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    logger.info("starting api server on %s:%s", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
