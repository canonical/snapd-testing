#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Calculate which spread tests should execute for a change set."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path


INTERFACE_USERS_FILE = "interface-users.json"
_INTERFACES_DIR = "interfaces/builtin/"
# interface files not named after their interface
_INTERFACE_NAME_EXCEPTIONS = {"autopilot.go": "autopilot-introspection"}


def _interface_name(file_path: str) -> str | None:
	"""Return the interface defined by an interfaces/builtin/<name>.go file."""
	if not file_path.startswith(_INTERFACES_DIR) or not file_path.endswith(".go"):
		return None
	file_name = file_path[len(_INTERFACES_DIR):]
	if "/" in file_name or file_name.endswith("_test.go"):
		return None
	return _INTERFACE_NAME_EXCEPTIONS.get(file_name, file_name[: -len(".go")].replace("_", "-"))


def _normalize_test_name(system: str, test_name: str) -> str:
	prefix = f"{system}:"
	if test_name.startswith(prefix):
		return test_name[len(prefix):]
	return test_name


# Parsed data files, kept until the file changes (i.e. until the next refresh),
# keyed by path with the modification time and size they were read at. The
# execution map is large, so reading it on every request is slow.
_json_cache: dict[Path, tuple[tuple[int, int], dict]] = {}
_json_cache_lock = threading.Lock()


def _load_json(path: Path) -> dict:
	"""Return the parsed json file, from the cache unless the file changed.

	The returned data is shared between callers and must not be modified.
	"""
	try:
		stat = path.stat()
	except FileNotFoundError:
		raise RuntimeError(f"missing required file: {path}") from None
	version = (stat.st_mtime_ns, stat.st_size)
	with _json_cache_lock:
		cached = _json_cache.get(path)
		if cached is not None and cached[0] == version:
			return cached[1]
		with path.open(encoding="utf-8") as handle:
			data = json.load(handle)
		_json_cache[path] = (version, data)
		return data


# Normalized names of the tests in a system's part of the cached execution map,
# keyed by that part's id; the part itself is kept to tell when it was replaced.
_known_tests_cache: dict[tuple[int, str], tuple[dict, frozenset[str]]] = {}


def _known_tests(system: str, spread_map_for_system: dict[str, list[str]]) -> frozenset[str]:
	key = (id(spread_map_for_system), system)
	with _json_cache_lock:
		cached = _known_tests_cache.get(key)
		if cached is not None and cached[0] is spread_map_for_system:
			return cached[1]
	known = frozenset(
		_normalize_test_name(system, test_name)
		for tests_for_file in spread_map_for_system.values()
		for test_name in tests_for_file
	)
	with _json_cache_lock:
		# drop entries for maps that were replaced by a refresh
		for stale in [k for k in _known_tests_cache if k[1] == system]:
			del _known_tests_cache[stale]
		_known_tests_cache[key] = (spread_map_for_system, known)
	return known


def _matching_tests_for_modified_file(
	modified_file: str,
	spread_map_for_system: dict[str, list[str]],
) -> set[str]:
	if modified_file not in spread_map_for_system:
		return set()
	return set(spread_map_for_system[modified_file])


def _matching_tests_for_directory(
	directory_prefix: str,
	spread_map_for_system: dict[str, list[str]],
) -> set[str]:
	matches: set[str] = set()
	for file_path, tests_for_file in spread_map_for_system.items():
		if file_path.startswith(directory_prefix):
			matches.update(tests_for_file)
	return matches


def calculate_tests_to_execute(
	system: str,
	tests: list[str],
	changed_files: dict[str, list[str]],
	directory: str,
) -> list[str]:
	"""Return the ordered subset of allowed tests that should execute.

	Rules:
	- Start from base executions for the given system.
	- Add tests related to modified files by exact file match in spread map.
	- For modified interfaces/builtin/<name>.go files, also add the tests that
	  connected the interface (interface-users.json); with that data, such a
	  file never counts as unmatched.
	- Add tests related to added/deleted files by immediate directory prefix.
	- If any modified file has no match in spread map, return all allowed tests.
	- Include allowed tests missing from spread map for this system.
	"""

	data_dir = Path(directory)
	spread_map = _load_json(data_dir / "spread-execution-map.json")
	base_executions = _load_json(data_dir / "base-executions.json")

	spread_map_for_system: dict[str, list[str]] = spread_map.get(system, {})
	base_tests_for_system: list[str] = base_executions.get(system, [])
	interface_users_path = data_dir / INTERFACE_USERS_FILE
	interface_users_for_system: dict[str, list[str]] | None = (
		_load_json(interface_users_path).get(system) if interface_users_path.exists() else None
	)

	selected_normalized: set[str] = {
		_normalize_test_name(system, test_name) for test_name in base_tests_for_system
	}

	modified_files = changed_files.get("modified", [])
	for file_path in modified_files:
		matches = _matching_tests_for_modified_file(file_path, spread_map_for_system)
		interface = _interface_name(file_path)
		if interface is not None and interface_users_for_system is not None:
			matches |= set(interface_users_for_system.get(interface, []))
		elif not matches:
			return list(tests)
		selected_normalized.update(_normalize_test_name(system, name) for name in matches)

	for key in ("added", "deleted"):
		for file_path in changed_files.get(key, []):
			parent = Path(file_path).parent.as_posix()
			prefix = "" if parent == "." else f"{parent}/"
			matches = _matching_tests_for_directory(prefix, spread_map_for_system)
			selected_normalized.update(_normalize_test_name(system, name) for name in matches)

	spread_known_normalized = _known_tests(system, spread_map_for_system)

	result: list[str] = []
	for test_name in tests:
		normalized = _normalize_test_name(system, test_name)
		if normalized in selected_normalized or normalized not in spread_known_normalized:
			result.append(test_name)
	return result


def main() -> None:
	parser = argparse.ArgumentParser(description="Calculate tests to execute")
	parser.add_argument("--system", help="spread system to calculate tests for", required=True, type=str)
	parser.add_argument("--tests-path", help="path to a list of possible tests to execute, separated by newlines", required=True, type=str)
	parser.add_argument("--changed-files-path", help="path to json file with added, deleted, and modified lists of files", required=True, type=str)
	parser.add_argument("--directory", help="path to directory where spread execution map and base executions are", required=True, type=str)
	args = parser.parse_args()

	tests_path = Path(args.tests_path)
	changed_files_path = Path(args.changed_files_path)
	if not tests_path.exists():
		raise RuntimeError(f"tests file not found: {tests_path}")
	if not changed_files_path.exists():
		raise RuntimeError(f"changed-files file not found: {changed_files_path}")

	tests = tests_path.read_text(encoding="utf-8").splitlines()
	changed_files = json.loads(changed_files_path.read_text(encoding="utf-8"))

	output = calculate_tests_to_execute(
		system=args.system,
		tests=tests,
		changed_files=changed_files,
		directory=args.directory,
	)
	print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
	main()

