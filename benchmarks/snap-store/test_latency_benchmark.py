"""Offline regression tests for the latency benchmark module boundaries."""

import contextlib
import csv
import io
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from common.errors import ProfileError
from common.graph_data import load_results
from common.graphs import graph_results
from latency.cli import main
from latency.profile import load_profile
from latency.requests import CapturedRequest
from latency.runner import run_benchmark


BENCHMARK_DIRECTORY = Path(__file__).resolve().parent


def stage_output(
    *, completed: int = 10, unexpected: int = 0, socket_errors: str = "",
) -> str:
    return (
        f" {completed} requests in 30.00s, 1KB read\nRequests/sec: 0.33\n"
        f"Expected HTTP status responses: {completed - unexpected}\n"
        f"Unexpected HTTP status responses: {unexpected}\n"
        f"{socket_errors}"
    )


class LatencyBenchmarkTests(unittest.TestCase):
    def test_bundled_rate_sweeps_keep_workers_fixed(self) -> None:
        for path in (BENCHMARK_DIRECTORY / "profiles").glob("*-refresh-profile.yaml"):
            with self.subTest(profile=path.name):
                profile = load_profile(path)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main([str(path), "--dry-run", "--wrk2", "/custom/wrk2"]), 0)
                commands = [
                    shlex.split(line) for line in output.getvalue().splitlines()
                    if line.startswith("  /custom/wrk2 ")
                ]
                self.assertEqual(
                    [int(command[command.index("--rate") + 1]) for command in commands],
                    list(profile.rates),
                )
                for command in commands:
                    self.assertEqual(command[command.index("--connections") + 1], str(profile.connections))
                    self.assertEqual(command[command.index("--threads") + 1], str(profile.threads))
                self.assertEqual(profile.connections % profile.threads, 0)

    def test_worker_scaling_is_rejected_before_network(self) -> None:
        document = yaml.safe_load(
            (BENCHMARK_DIRECTORY / "profiles" / "snapd-refresh-profile.yaml").read_text()
        )
        document["load"]["scale_workers_with_rate"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text(yaml.safe_dump(document))
            output = io.StringIO()
            with patch("urllib.request.OpenerDirector.open") as network, contextlib.redirect_stderr(output):
                self.assertEqual(main([str(path), "--run"]), 2)
                network.assert_not_called()
            self.assertIn("rate sweeps require fixed connections and threads", output.getvalue())
            del document["load"]["scale_workers_with_rate"]
            path.write_text(yaml.safe_dump(document))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path), "--dry-run"]), 0)

    def test_fixed_workers_reject_uneven_connection_allocation(self) -> None:
        document = yaml.safe_load(
            (BENCHMARK_DIRECTORY / "profiles" / "snapd-refresh-profile.yaml").read_text()
        )
        document["load"].update(connections=3, threads=2, scale_workers_with_rate=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text(yaml.safe_dump(document))
            with self.assertRaisesRegex(ProfileError, "divide"):
                load_profile(path)

    def test_failed_or_invalid_metrics_save_results_and_stop_escalation(self) -> None:
        cases = [
            (stage_output(unexpected=10), "unexpected_http_responses"),
            (stage_output(completed=0), "zero successful requests"),
            ("not wrk output\n", "missing or invalid"),
            (stage_output().replace("Requests/sec: 0.33\n", ""), "missing or invalid"),
            (stage_output().replace("Expected HTTP status responses: 10\n", ""), "HTTP status"),
            (stage_output().replace("responses: 10", "responses: 9"), "inconsistent"),
        ]
        for field in ("connect", "read", "write", "timeout"):
            counts = ", ".join(
                f"{name} {int(name == field)}" for name in ("connect", "read", "write", "timeout")
            )
            cases.append((stage_output(socket_errors=f" Socket errors: {counts}\n"), f"socket_{field}_errors"))
        for output, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                profile = replace(
                    load_profile(BENCHMARK_DIRECTORY / "profiles" / "snapd-refresh-profile.yaml"),
                    profile_file=Path(directory) / "profile.yaml",
                    output_directory=Path(directory) / "results",
                    rates=(1, 2), preflight_enabled=False, pause_seconds=0,
                )
                with (
                    patch("latency.runner.require_wrk2", return_value="wrk2"),
                    patch("latency.runner.run_wrk2", return_value=subprocess.CompletedProcess(
                        ["wrk2"], 0, output,
                    )) as run,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    result = run_benchmark(
                        profile, CapturedRequest("POST", "/", (), "{}"), wrk2_binary="wrk2",
                    )
                self.assertEqual((result, run.call_count), (1, 1))
                summary = next(profile.output_directory.glob("*/summary.csv"))
                with summary.open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["exit_code"], "0")
                self.assertEqual(rows[0]["stage_status"], "failed")
                self.assertIn(reason, rows[0]["failure_reason"])
                self.assertIn(output, (summary.parent / rows[0]["log_file"]).read_text())
                if reason == "unexpected_http_responses":
                    results = load_results(summary.parent)
                    self.assertEqual(results[0].non_success_responses, 10)
                    with patch("matplotlib.figure.Figure.savefig", autospec=True) as save:
                        graph_results(summary.parent, results, summary.parent / "graphs.png")
                    bars = save.call_args.args[0].axes[-1].patches
                    self.assertEqual([bar.get_height() for bar in bars], [10])
                    labels = save.call_args.args[0].axes[-1].get_xticklabels()
                    self.assertIn("(failed)", labels[0].get_text())

    def test_script_uses_bundled_profile_without_network_from_another_directory(self) -> None:
        output = io.StringIO()
        with (
            patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network request")),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(["--dry-run", "--wrk2", "/custom/wrk2"]), 0)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable, str(BENCHMARK_DIRECTORY / "latency_benchmark.py"),
                    "--dry-run", "--wrk2", "/custom/wrk2",
                ],
                cwd=directory, capture_output=True, text=True, check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, output.getvalue())

    def test_runner_integrates_preflight_lua_commands_and_summary(self) -> None:
        request = CapturedRequest(
            "POST", "/v2/snaps/refresh",
            (("Host", "ignored.test"), ("Content-Type", "application/json")),
            '{"actions": []}',
        )
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                profile = replace(
                    load_profile(BENCHMARK_DIRECTORY / "profiles" / "snapd-refresh-profile.yaml"),
                    profile_file=Path(directory) / "profile.yaml",
                    rates=(1, 2), warmup_requests=2, pause_seconds=0,
                    output_directory=Path(directory) / "results",
                )
                scripts = []

                def run(command: list[str]) -> subprocess.CompletedProcess[str]:
                    self.assertEqual(send.call_count, 3)
                    script = Path(command[command.index("--script") + 1])
                    scripts.append(script)
                    lua = script.read_text()
                    self.assertIn('wrk.body = [[{"actions": []}]]', lua)
                    self.assertNotIn("Host", lua)
                    self.assertEqual(script.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(
                        command[command.index("--connections") + 1],
                        str(profile.connections),
                    )
                    self.assertEqual(command[command.index("--threads") + 1], str(profile.threads))
                    return subprocess.CompletedProcess(
                        command, exit_code, stage_output(),
                    )

                with (
                    patch("latency.runner.require_wrk2", return_value="/custom/wrk2"),
                    patch("latency.requests.send_request", return_value=(200, 2)) as send,
                    patch("latency.runner.run_wrk2", side_effect=run),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(run_benchmark(profile, request, wrk2_binary="wrk2"), exit_code)
                summary = next(profile.output_directory.glob("latency-*/summary.csv"))
                with summary.open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(
                    [row["requests_per_second_target"] for row in rows],
                    ["1", "2"] if exit_code == 0 else ["1"],
                )
                for row in rows:
                    self.assertEqual(row["requests_completed"], "10")
                    self.assertEqual(row["exit_code"], str(exit_code))
                    self.assertEqual(row["stage_status"], "passed" if exit_code == 0 else "failed")
                    self.assertTrue((summary.parent / row["log_file"]).is_file())
                self.assertTrue(all(not script.exists() for script in scripts))


@unittest.skipUnless(shutil.which("wrk2"), "wrk2 is required for localhost integration")
class LocalLatencyTests(unittest.TestCase):
    def test_real_status_counters_and_connection_allocation(self) -> None:
        condition = threading.Condition()
        active = 0
        peak = 0
        response_status = 201

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle(self) -> None:
                nonlocal active, peak
                with condition:
                    active += 1
                    peak = max(peak, active)
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The generator closes keep-alive connections at stage end.
                finally:
                    with condition:
                        active -= 1
                        condition.notify_all()

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(response_status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: object) -> None:
                pass

        class Server(ThreadingHTTPServer):
            request_queue_size = 16

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            for response_status in (201, 302, 500):
                # Snap-packaged wrk2 cannot see the host's /tmp.
                with self.subTest(response_status=response_status), tempfile.TemporaryDirectory(
                    prefix="test-latency-", dir=BENCHMARK_DIRECTORY,
                ) as directory:
                    profile = replace(
                        load_profile(BENCHMARK_DIRECTORY / "profiles" / "snapd-refresh-profile.yaml"),
                        profile_file=Path(directory) / "profile.yaml",
                        url=f"http://127.0.0.1:{server.server_port}/v2/snaps/refresh",
                        output_directory=Path(directory) / "results",
                        rates=(1, 3), duration="12s" if response_status == 201 else "1s",
                        expected_status=201, preflight_enabled=False, pause_seconds=0,
                    )
                    observed_peaks = []
                    outputs = []

                    def measure(command: list[str]) -> subprocess.CompletedProcess[str]:
                        nonlocal peak
                        with condition:
                            self.assertTrue(condition.wait_for(lambda: active == 0, timeout=5))
                            peak = 0
                        completed = subprocess.run(
                            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=20, check=False,
                        )
                        outputs.append(completed.stdout)
                        with condition:
                            observed_peaks.append(peak)
                        return completed

                    with (
                        patch("latency.runner.run_wrk2", side_effect=measure),
                        contextlib.redirect_stdout(io.StringIO()),
                        contextlib.redirect_stderr(io.StringIO()),
                    ):
                        result = run_benchmark(
                            profile, CapturedRequest("POST", "/v2/snaps/refresh", (), "{}"),
                            wrk2_binary="wrk2",
                        )
                    expected_failure = response_status != 201
                    self.assertEqual(result, int(expected_failure), "\n".join(outputs))
                    self.assertEqual(observed_peaks, [10] if expected_failure else [10, 10])
                    summary = next(profile.output_directory.glob("*/summary.csv"))
                    with summary.open() as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual([int(row["connections"]) for row in rows], observed_peaks)
                    self.assertEqual([int(row["threads"]) for row in rows], [2] if expected_failure else [2, 2])
                    for row in rows:
                        self.assertGreater(int(row["requests_completed"]), 0)
                        self.assertEqual(
                            int(row["unexpected_http_responses"]),
                            int(row["requests_completed"]) if expected_failure else 0,
                        )
                        self.assertEqual(row["stage_status"], "failed" if expected_failure else "passed")
                    results = load_results(summary.parent)
                    self.assertEqual(len(results), len(rows))
                    graph_results(summary.parent, results, summary.parent / "graphs.png")
                    self.assertEqual((summary.parent / "graphs.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
