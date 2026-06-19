#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Canonical Ltd

"""Mimic snapd's CI test selection for a pull request, with and without the service.

For each job in the pull request's systems matrix (by default
.github/workflows/data-fundamental-systems.json), this does what
spread-tests.yaml does to pick the tests, then asks the test-selector
service which of them to run:

1. the changed files, as CI sees them (added, copied, modified, renamed and
   deleted paths), read from GitHub with the gh CLI
2. the job's tests: .github/scripts/spread_tests_to_run.py with the job's
   backend, systems, tasks and rules, expanded with `spread -list` in a
   temporary worktree of the pull request's head ("CI" column)
3. tests/utils/features/shared_test_users.py --rewrite on the changed files
   (with --base set to the pull request's base, so changed tools are narrowed
   to the subcommands the change affects), then POST /calculate-tests once per
   system with that system's tests ("service" column)

Both scripts are taken from the pull request's checkout, or from the --snapd
checkout when the pull request does not have them yet. The skip list from
canonical/snapd-testing-skip, applied by CI after this, is not applied.

Example:
    tools/check_pr.py 17856 --snapd ~/source/snapd --service http://10.113.43.87:8181
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SYSTEMS_FILE = "data-fundamental-systems.json"
WORKFLOWS_DIR = ".github/workflows"
TESTS_TO_RUN_SCRIPT = ".github/scripts/spread_tests_to_run.py"
SHARED_USERS_SCRIPT = "tests/utils/features/shared_test_users.py"


def run(cmd: list[str], cwd: str | Path | None = None, stdin: str | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def pr_files(repo: str, pr: int) -> tuple[list[str], dict[str, list[str]]]:
    """
    The pull request's changed files: the list CI uses (tj-actions/changed-files
    all_modified_files: added, copied, modified, renamed and deleted paths), and
    the modified/added/deleted lists the service takes, where a rename deletes
    the old path and adds the new one.
    """
    pages = json.loads(run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls/{pr}/files"]))
    ci_files: list[str] = []
    service_files: dict[str, list[str]] = {"modified": [], "added": [], "deleted": []}
    for entry in (item for page in pages for item in page):
        status, path = entry["status"], entry["filename"]
        ci_files.append(path)
        if status == "added":
            service_files["added"].append(path)
        elif status == "removed":
            service_files["deleted"].append(path)
        elif status in ("renamed", "copied"):
            if status == "renamed":
                service_files["deleted"].append(entry["previous_filename"])
            service_files["added"].append(path)
        else:
            service_files["modified"].append(path)
    return ci_files, service_files


def script(worktree: Path, snapd: Path, relative: str) -> Path | None:
    """A script from the pull request's checkout, else from the --snapd checkout."""
    for root in (worktree, snapd):
        if (root / relative).exists():
            return root / relative
    return None


def jobs(worktree: Path, systems_files: list[str]) -> list[dict]:
    out = []
    for name in systems_files:
        data = json.loads((worktree / WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
        out += data["include"]
    return out


def ci_tests(tests_to_run: Path, worktree: Path, job: dict, ci_files: list[str], run_nested: bool) -> list[str]:
    """The tests a CI job would run, before the skip list: selectors expanded with spread -list."""
    cmd = [sys.executable, str(tests_to_run), "--repo", str(worktree), "--backend", job["backend"],
           "--systems", job["systems"], "--tasks", job.get("tasks", "tests/..."), "--rules", job.get("rules", ""), "--pr"]
    for path in ci_files:
        cmd += ["--change", path]
    if run_nested and job["group"].startswith("nested-"):
        cmd.append("--run-nested")
    selectors = run(cmd).split()
    if not selectors:
        return []
    listed = subprocess.run(["spread", "-list", *selectors], cwd=worktree, capture_output=True, text=True)
    # spread fails when nothing matches the selectors, like CI's "nothing matches provider filter"
    return listed.stdout.split() if listed.returncode == 0 else []


def call_service(service: str, changed: dict[str, list[str]], system: str, tests: list[str]) -> list[str]:
    body = json.dumps({"changed_files": changed, "backend_systems": [system], "tests": tests}).encode()
    request = urllib.request.Request(
        f"{service.rstrip('/')}/calculate-tests", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())["tests"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"service returned {exc.code}: {exc.read().decode(errors='replace')}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pr", type=int, help="pull request number")
    parser.add_argument("--repo", default="canonical/snapd", help="GitHub repository (default: canonical/snapd)")
    parser.add_argument("--snapd", required=True, type=Path,
                        help="local snapd git checkout: holds the worktree, and provides the scripts the pull request lacks")
    parser.add_argument("--service", default="http://127.0.0.1:8181", help="test-selector service URL")
    parser.add_argument("--systems-file", action="append",
                        help=f"systems matrix under {WORKFLOWS_DIR}; can be repeated (default: {DEFAULT_SYSTEMS_FILE})")
    parser.add_argument("--no-shared-users", action="store_true",
                        help=f"send the changed files without rewriting them with {SHARED_USERS_SCRIPT}")
    parser.add_argument("--show-tests", action="store_true", help="also print the tests the service selects")
    parser.add_argument("--json", type=Path, help="write the results to this file")
    args = parser.parse_args()
    systems_files = args.systems_file or [DEFAULT_SYSTEMS_FILE]

    pr_info = json.loads(run(["gh", "pr", "view", str(args.pr), "--repo", args.repo,
                              "--json", "title,url,labels,baseRefOid"]))
    labels = {label["name"] for label in pr_info["labels"]}
    ci_files, service_files = pr_files(args.repo, args.pr)
    print(f"PR #{args.pr}: {pr_info['title']}\n{pr_info['url']}")
    print(f"changed files: {len(service_files['modified'])} modified, {len(service_files['added'])} added, "
          f"{len(service_files['deleted'])} deleted")
    if labels & {"Skip spread", "Run all"}:
        print(f"note: labels {sorted(labels & {'Skip spread', 'Run all'})} change what CI runs and are not mimicked")

    base = pr_info["baseRefOid"]
    run(["git", "-C", str(args.snapd), "fetch", "-q", f"https://github.com/{args.repo}.git", base])
    run(["git", "-C", str(args.snapd), "fetch", "-q", f"https://github.com/{args.repo}.git", f"pull/{args.pr}/head"])
    head = run(["git", "-C", str(args.snapd), "rev-parse", "FETCH_HEAD"]).strip()
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "snapd"
        run(["git", "-C", str(args.snapd), "worktree", "add", "-q", "--detach", str(worktree), head])
        try:
            tests_to_run = script(worktree, args.snapd, TESTS_TO_RUN_SCRIPT)
            if tests_to_run is None:
                raise SystemExit(f"{TESTS_TO_RUN_SCRIPT} found in neither the pull request nor {args.snapd}")
            print(f"tests to run with {tests_to_run}")

            sent = service_files
            shared_users = None if args.no_shared_users else script(worktree, args.snapd, SHARED_USERS_SCRIPT)
            if shared_users is not None:
                cmd = [sys.executable, str(shared_users), "--repo", str(worktree), "--changed-files", "-", "--rewrite"]
                # older copies cannot narrow changed tools to subcommands with --base
                narrows = "subcommand" in run([sys.executable, str(shared_users), "--help"])
                if narrows:
                    cmd += ["--base", base]
                sent = json.loads(run(cmd, stdin=json.dumps(service_files)))
                print(f"changed files rewritten with {shared_users}"
                      + ("" if narrows else " (without narrowing tools to subcommands)"))
            elif not args.no_shared_users:
                print(f"{SHARED_USERS_SCRIPT} not found; shared test files make the service run everything")

            ci_jobs = jobs(worktree, systems_files)
            print(f"listing the tests of {len(ci_jobs)} jobs at {head[:10]} ...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(ci_jobs) or 1) as pool:
                job_tests = list(pool.map(
                    lambda job: ci_tests(tests_to_run, worktree, job, ci_files, "Run nested" in labels), ci_jobs))
        finally:
            run(["git", "-C", str(args.snapd), "worktree", "remove", "--force", str(worktree)])

    results = []
    print(f"\n{'group':22} {'system':40} {'CI':>6} {'service':>8}")
    for job, tests in zip(ci_jobs, job_tests):
        for system in job["systems"].split():
            prefix = f"{job['backend']}:{system}"
            system_tests = [t for t in tests if t.startswith(prefix + ":")]
            selected = call_service(args.service, sent, prefix, system_tests) if system_tests else []
            print(f"{job['group']:22} {prefix:40} {len(system_tests):>6} {len(selected):>8}")
            if args.show_tests:
                print("\n".join(f"    {test}" for test in selected))
            results.append({"group": job["group"], "system": prefix, "ci": system_tests, "service": selected})

    ci_total = sum(len(r["ci"]) for r in results)
    service_total = sum(len(r["service"]) for r in results)
    share = f"  ({service_total / ci_total * 100:.1f}% of CI)" if ci_total else ""
    print(f"{'total':63} {ci_total:>6} {service_total:>8}{share}")

    if args.json:
        args.json.write_text(json.dumps({"pr": args.pr, "base": base, "head": head, "ci_changed_files": ci_files,
                                         "changed_files": service_files, "sent_changed_files": sent,
                                         "results": results}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
