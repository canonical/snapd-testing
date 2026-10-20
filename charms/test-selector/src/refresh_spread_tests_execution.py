#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Helper to refresh spread-tests-execution and base-test mappings from mongodb metadata."""

import argparse
import concurrent.futures
import json
import subprocess
import os
import sys
from pathlib import Path



# the part of the snapd repository the refresh needs: query_features.py and its modules
FEATURES_SUBDIR = "tests/utils/features"


def _run_checked(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def fetch_snapd_features(snapd_dir: Path, url: str, ref: str, run=_run_checked) -> Path:
    """
    Update snapd_dir to a shallow, sparse checkout of only FEATURES_SUBDIR from
    url at ref (a branch, tag or full commit SHA), and return the path of
    query_features.py in it.
    """
    repo = str(snapd_dir)
    if not (snapd_dir / ".git").exists():
        run(["git", "init", "-q", repo])
        run(["git", "-C", repo, "remote", "add", "origin", url])
        run(["git", "-C", repo, "sparse-checkout", "set", FEATURES_SUBDIR])
    else:
        run(["git", "-C", repo, "remote", "set-url", "origin", url])
    run(["git", "-C", repo, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref])
    run(["git", "-C", repo, "checkout", "-q", "--force", "FETCH_HEAD"])
    query_features = snapd_dir / FEATURES_SUBDIR / "query_features.py"
    if not query_features.exists():
        raise FileNotFoundError(f"query_features.py not found in {url} at {ref}: {query_features}")
    return query_features


class FeatureQuery:
    def __init__(self, query_features_path, creds_path):
        self.query_features_path = query_features_path
        self.creds_path = creds_path

    def query_features(self, command: str, args: str = ""):
        # same interpreter as this helper, i.e. the charm's venv with pymongo
        cmd = [
            sys.executable,
            str(self.query_features_path),
            *command.split(),
            "-f",
            self.creds_path,
            *args.split()
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"query_features.py failed with exit code {result.returncode}\n"
                f"Command: {' '.join(cmd)}\n"
                f"stderr: {result.stderr}"
            )
        return json.loads(result.stdout or "{}")


def _latest_timestamp_and_systems(fq: FeatureQuery) -> tuple[str, list[str]]:
    entries = fq.query_features("list")
    if not entries:
        raise RuntimeError("query_features.py list returned no timestamps")
    latest = entries[0].get("timestamp")
    systems = entries[0].get("systems", [])
    if not latest:
        raise RuntimeError("latest timestamp entry is missing timestamp")
    return str(latest), systems


def write_json(path: Path, data) -> None:
    """
    Write json to path atomically, so the service, which caches these files
    until they change, never reads a partially written one.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def get_test_name(system: str, suite: str, task_name: str, variant: str):
    if not task_name:
        return f"{system}:{suite}"
    if not variant:
        return f"{system}:{suite}/{task_name}"
    return f"{system}:{suite}/{task_name}:{variant}"


# features not needed to build the execution map and interface users
SYSTEM_QUERY_EXCLUDE = "cmds endpoints ensures tasks changes"


def system_execution_map(system: str, tests: list[dict]) -> dict[str, list[str]]:
    """Returns, for one system, the tests that covered each file."""
    output: dict[str, set[str]] = {}
    for test in tests:
        # Failed tests stop recording coverage where they failed, so their
        # coverage is incomplete. Leaving them out of the map makes them
        # always selected, like any other test without coverage.
        if 'coverages' not in test or test.get('success') is False:
            continue
        name = get_test_name(system, test['suite'], test['task_name'], test['variant'])
        files = {
            coverage['file']
            for coverage in test['coverages']
            if  coverage['func'] != 'init' and
                (not coverage['file'].startswith('interfaces/') or coverage['func'] != 'Name') and
                (not coverage['file'].startswith('interfaces/') or coverage['func'] != 'StaticInfo')
        }
        for file_path in files:
            output.setdefault(file_path, set()).add(name)
    return {key: sorted(names) for key, names in output.items()}


def system_interface_users(system: str, tests: list[dict]) -> dict[str, list[str]]:
    """
    Returns, for one system, the tests that connected each interface, taken
    from the interfaces feature. Many interfaces have no coverage of their own
    (their code runs in shared files), so this is how changes to them are matched.
    """
    users: dict[str, set[str]] = {}
    for test in tests:
        # like the execution map, failed tests are left out so they are always selected
        if test.get('success') is False:
            continue
        name = get_test_name(system, test['suite'], test['task_name'], test['variant'])
        for interface in test.get('interfaces') or []:
            if interface.get('name'):
                users.setdefault(interface['name'], set()).add(name)
    return {interface: sorted(names) for interface, names in users.items()}


def build_system_data(
    fq: FeatureQuery,
    timestamp: str,
    systems: list[str],
    jobs: int,
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, list[str]]]]:
    """
    Returns the execution map and the interface users of all systems. Each
    system is queried once, for coverage and interfaces together, and up to
    jobs systems are queried in parallel. Each worker reduces its system's
    feature data to its part of the outputs, so the large query results are
    not all held in memory at once.
    """
    def build(system: str):
        data = fq.query_features("feat sys", f"-t {timestamp} -s {system} --exclude {SYSTEM_QUERY_EXCLUDE}")
        if 'tests' not in data:
            return system, None, None
        return system, system_execution_map(system, data['tests']), system_interface_users(system, data['tests'])

    execution_map: dict[str, dict[str, list[str]]] = {}
    interface_users: dict[str, dict[str, list[str]]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for system, system_map, system_users in pool.map(build, systems):
            if system_map is None:
                continue
            execution_map[system] = system_map
            interface_users[system] = system_users
    return execution_map, interface_users


def build_base_tests(
    fq: FeatureQuery,
    timestamp: str,
    max_minutes: int,
    exclude: list[str],
    force_match_keywords: list[str] | None = None,
) -> dict[str, list[str]]:
    args = f"-t {timestamp} --max-minutes {max_minutes}"
    # Without a time limit the base set already covers every feature, and
    # query_features.py only accepts keywords together with --max-minutes.
    if force_match_keywords and max_minutes > 0:
        args += " --force-match-keywords " + " ".join(force_match_keywords)
    if exclude:
        args += " --exclude " + " ".join(exclude)
    return fq.query_features("feat cover", args)


def main() -> None:
    parser = argparse.ArgumentParser(description="refresh spread-execution-map.json, interface-users.json and base-executions.json using query_features.py")
    parser.add_argument("--query-features-path", required=True, type=str)
    parser.add_argument("--credentials-path", required=True, type=str)
    parser.add_argument("--output-dir", required=True, type=str)
    parser.add_argument("--base-tests-max-minutes", default=0, type=int)
    parser.add_argument(
        "--base-tests-force-keywords",
        nargs="*",
        default=["refresh"],
        help="features containing these keywords are always covered by the base tests, even beyond the max minutes",
    )
    parser.add_argument("--snapd-dir", type=str, help="snapd checkout to update before refreshing")
    parser.add_argument("--snapd-repo-url", type=str, help="repository to update --snapd-dir from")
    parser.add_argument("--snapd-ref", type=str, help="branch, tag or full commit SHA to update --snapd-dir to")
    parser.add_argument(
        "--jobs",
        default=2,
        type=int,
        help="number of systems queried in parallel; each query_features.py process can use several hundred MB",
    )
    args = parser.parse_args()

    output_path = Path(args.output_dir)

    fetch_args = (args.snapd_dir, args.snapd_repo_url, args.snapd_ref)
    if any(fetch_args) and not all(fetch_args):
        raise RuntimeError("--snapd-dir, --snapd-repo-url and --snapd-ref must be given together")
    if all(fetch_args):
        # refresh with the current query_features.py, however the refresh was triggered
        fetch_snapd_features(Path(args.snapd_dir), args.snapd_repo_url, args.snapd_ref)

    if not Path(args.query_features_path).exists():
        raise RuntimeError(f"query_features.py not found: {args.query_features_path}")
    if not Path(args.credentials_path).exists():
        raise RuntimeError(f"credentials file not found: {args.credentials_path}")
    
    fq = FeatureQuery(args.query_features_path, args.credentials_path)

    # one timestamp for all outputs, so they come from the same upload
    timestamp, systems = _latest_timestamp_and_systems(fq)

    execution_map, interface_users = build_system_data(fq, timestamp, systems, args.jobs)
    base_tests = build_base_tests(
        fq,
        timestamp,
        args.base_tests_max_minutes,
        ["coverages"],
        args.base_tests_force_keywords,
    )

    output_path.mkdir(parents=True, exist_ok=True)
    write_json(output_path / "spread-execution-map.json", execution_map)
    write_json(output_path / "interface-users.json", interface_users)
    write_json(output_path / "base-executions.json", base_tests)


if __name__ == "__main__":
    main()
