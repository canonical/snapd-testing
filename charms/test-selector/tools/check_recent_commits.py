#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Check how calculate-tests behaves against the most recent snapd commits.

For each of the last N commits on a snapd ref, the changed files are fed to the
same TestExecutionService.calculate_tests the charm's API uses, with the full
`spread -list` output (taken at that ref) as the allowed tests. The result is a
table of how many tests each commit would run and why.

The execution map and base tests are read from --data-dir. If they are missing
(or --refresh is given) they are rebuilt from mongodb with the charm's refresh
helper, which needs --credentials and the snapd query_features.py dependencies
(pymongo) available to `python3`.

Example:
    tools/check_recent_commits.py --snapd ~/source/snapd --data-dir /tmp/tel \\
        --credentials ~/mongo-creds.json --commits 20 --fetch
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CHARM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHARM_DIR / "src"))

from calculate_tests import _interface_name  # noqa: E402
from service_api import TestExecutionService  # noqa: E402

DEFAULT_FOCUS = "openstack:ubuntu-24.04-64"


def git(snapd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(snapd), *args], check=True, capture_output=True, text=True).stdout


def changed_files(snapd: Path, commit: str) -> dict[str, list[str]]:
    files: dict[str, list[str]] = {"modified": [], "added": [], "deleted": []}
    out = git(snapd, "diff-tree", "--no-commit-id", "-r", "-M", "--name-status", f"{commit}^1", commit)
    for line in out.splitlines():
        status, *paths = line.split("\t")
        if status == "M":
            files["modified"].append(paths[0])
        elif status == "A":
            files["added"].append(paths[0])
        elif status == "D":
            files["deleted"].append(paths[0])
        else:
            # renames and copies: the old path goes away, the new one appears
            files["deleted"].append(paths[0])
            files["added"].append(paths[-1])
    return files


def refresh_data(args: argparse.Namespace) -> None:
    if not args.credentials:
        raise SystemExit(f"no execution data in {args.data_dir}; pass --credentials to build it from mongodb")
    query_features = args.query_features_path or args.snapd / "tests/utils/features/query_features.py"
    print(f"building execution data in {args.data_dir} from mongodb ...", file=sys.stderr)
    subprocess.run(
        [
            sys.executable,
            str(CHARM_DIR / "src/refresh_spread_tests_execution.py"),
            "--query-features-path", str(query_features),
            "--credentials-path", str(args.credentials),
            "--output-dir", str(args.data_dir),
            "--base-tests-max-minutes", str(args.base_tests_max_minutes),
        ],
        check=True,
    )


def spread_lists(args: argparse.Namespace, sha: str, systems: list[str]) -> dict[str, list[str]]:
    """Return `spread -list` output per system at the given commit, cached on disk."""
    cache = args.data_dir / f"spread-lists-{sha[:12]}"
    wanted = {system: cache / f"{system}.txt" for system in systems}
    missing = [system for system, path in wanted.items() if not path.exists()]
    if missing:
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "snapd"
            git(args.snapd, "worktree", "add", "--detach", str(worktree), sha)
            try:
                def list_system(system: str) -> tuple[str, str]:
                    out = []
                    for suite in args.suites:
                        result = subprocess.run(
                            ["spread", "-list", f"{system}:{suite}"],
                            cwd=worktree, capture_output=True, text=True,
                        )
                        if result.returncode == 0:
                            out.append(result.stdout)
                    return system, "".join(out)

                print(f"listing spread tests for {len(missing)} systems at {sha[:10]} ...", file=sys.stderr)
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(missing)) as pool:
                    for system, output in pool.map(list_system, missing):
                        wanted[system].write_text(output, encoding="utf-8")
            finally:
                git(args.snapd, "worktree", "remove", "--force", str(worktree))
    return {system: path.read_text(encoding="utf-8").split() for system, path in wanted.items()}


def explain(
    files: dict[str, list[str]],
    spread_map_for_focus: dict[str, list[str]],
    has_interface_users: bool,
) -> tuple[str, list[str]]:
    """Classify why calculate-tests selects what it does for the focus system."""
    files = {key: [p for p in paths if not TestExecutionService._is_ignored(p)] for key, paths in files.items()}
    flat = [path for paths in files.values() for path in paths]
    build = [p for p in flat if TestExecutionService._requires_all_tests(p)]
    if build:
        return "all: non-Go file", build
    shared = [p for p in flat if TestExecutionService._is_test_script(p) and TestExecutionService._owning_test_dir(p) is None]
    if shared:
        return "all: shared tests/ file", shared
    go = [p for p in files["modified"] if TestExecutionService._is_go_source(p)]
    # with connection data, interface files are matched through it instead
    uncovered = [
        p for p in go
        if p not in spread_map_for_focus and not (has_interface_users and _interface_name(p))
    ]
    if uncovered:
        return "all: Go file without coverage", uncovered
    folders = sorted({TestExecutionService._owning_test_dir(p) for p in flat if TestExecutionService._is_test_script(p)} - {None})
    added_deleted_go = [p for p in files["added"] + files["deleted"] if TestExecutionService._is_go_source(p)]
    if go or folders or added_deleted_go:
        return "targeted", go + folders + added_deleted_go
    return "base only", []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapd", required=True, type=Path, help="path to a snapd git checkout")
    parser.add_argument("--commits", default=20, type=int, help="number of most recent first-parent commits to check")
    parser.add_argument("--ref", default="upstream/master", help="ref whose history is checked (default: upstream/master)")
    parser.add_argument("--fetch", action="store_true", help="fetch the remote of --ref first")
    parser.add_argument("--data-dir", required=True, type=Path, help="directory with spread-execution-map.json and base-executions.json; also caches spread lists")
    parser.add_argument("--refresh", action="store_true", help="rebuild the execution data from mongodb even if present")
    parser.add_argument("--credentials", type=Path, help="mongodb credentials json (host, port, user, password) used to build the execution data")
    parser.add_argument("--query-features-path", type=Path, help="query_features.py to use (default: from --snapd)")
    parser.add_argument("--base-tests-max-minutes", default=15, type=int, help="same as the charm config option")
    parser.add_argument("--systems", nargs="+", help="backend:system values to check (default: openstack systems in the execution map)")
    parser.add_argument("--suites", nargs="+", default=["tests/main/"], help="suites passed to spread -list (default: tests/main/)")
    parser.add_argument("--added-tests", type=Path, help="added-spread-tests.txt to apply, like the charm does")
    parser.add_argument("--focus", default=DEFAULT_FOCUS, help=f"system shown in the table (default: {DEFAULT_FOCUS})")
    parser.add_argument("--details", action="store_true", help="also print the files behind each decision")
    parser.add_argument("--show-tests", metavar="COMMIT", help="print the tests selected on the focus system for this commit")
    parser.add_argument("--json", type=Path, help="write per-commit results to this file")
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    map_path = args.data_dir / "spread-execution-map.json"
    base_path = args.data_dir / "base-executions.json"
    if args.refresh or not map_path.exists() or not base_path.exists():
        refresh_data(args)
    spread_map = json.loads(map_path.read_text(encoding="utf-8"))
    base = json.loads(base_path.read_text(encoding="utf-8"))
    interface_users_path = args.data_dir / "interface-users.json"
    interface_users = json.loads(interface_users_path.read_text(encoding="utf-8")) if interface_users_path.exists() else {}

    if args.fetch:
        remote, _, branch = args.ref.partition("/")
        git(args.snapd, "fetch", remote, branch)
    sha = git(args.snapd, "rev-parse", args.ref).strip()

    systems = args.systems or sorted(s for s in spread_map if s.split(":", 1)[0] in ("openstack", "openstack-arm"))
    lists = spread_lists(args, sha, systems)
    empty = [s for s, tests in lists.items() if not tests]
    if empty:
        print(f"skipping systems with no tests in {args.suites}: {' '.join(empty)}", file=sys.stderr)
    systems = [s for s in systems if lists[s]]
    if not systems:
        raise SystemExit("no systems with tests to check")
    focus = args.focus if args.focus in systems else systems[0]

    added_path = args.added_tests or args.data_dir / "no-added-tests.txt"
    service = TestExecutionService({
        "spread_tests_execution_path": str(map_path),
        "added_spread_tests_path": str(added_path),
        "refresh_command": ["true"],
    })

    all_tests = [test for system in systems for test in lists[system]]
    focus_tests = lists[focus]
    focus_known = {t for tests in spread_map.get(focus, {}).values() for t in tests}
    focus_floor = sum(
        1 for t in focus_tests if t.split(":", 2)[2] in base.get(focus, []) or t not in focus_known
    )

    rows = []
    log = git(args.snapd, "log", "--format=%H%x09%s", "--first-parent", f"-{args.commits}", sha)
    for line in log.splitlines():
        commit, subject = line.split("\t", 1)
        files = changed_files(args.snapd, commit)
        selected = service.calculate_tests(files, systems, all_tests)
        if not set(selected) <= set(all_tests):
            raise SystemExit(f"{commit[:10]}: selection is not a subset of the input")
        focus_selected = [t for t in selected if t.startswith(f"{focus}:")]
        reason, because = explain(files, spread_map.get(focus, {}), focus in interface_users)
        rows.append({
            "commit": commit, "subject": subject, "reason": reason, "because": because,
            "files": files, "selected": len(focus_selected), "all_selected": len(selected),
            "focus_tests": focus_selected,
        })

    print(f"ref {args.ref} @ {sha[:10]}, {len(rows)} commits, {len(systems)} systems ({len(all_tests)} tests)")
    print(f"focus {focus}: {len(focus_tests)} tests, base + tests without coverage = {focus_floor}")
    print("reasons:", dict(collections.Counter(r["reason"] for r in rows)))
    print(f"share run: focus {sum(r['selected'] for r in rows) / (len(focus_tests) * len(rows)) * 100:.1f}%, "
          f"all systems {sum(r['all_selected'] for r in rows) / (len(all_tests) * len(rows)) * 100:.1f}%\n")
    for r in rows:
        print(f"{r['commit'][:10]} {r['selected']:5}/{len(focus_tests)} {r['all_selected']:6}/{len(all_tests)}  "
              f"{r['reason']:30} {r['subject'][:60]}")
        if args.details and r["because"]:
            shown = r["because"][:5]
            more = f" (+{len(r['because']) - 5} more)" if len(r["because"]) > 5 else ""
            print(f"{'':12}{', '.join(shown)}{more}")

    if args.show_tests:
        match = [r for r in rows if r["commit"].startswith(args.show_tests)]
        if not match:
            raise SystemExit(f"commit {args.show_tests} is not among the checked commits")
        print(f"\ntests selected on {focus} for {match[0]['commit'][:10]}:")
        print("\n".join(match[0]["focus_tests"]))

    if args.json:
        args.json.write_text(json.dumps({"ref": args.ref, "sha": sha, "focus": focus, "systems": systems, "commits": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
