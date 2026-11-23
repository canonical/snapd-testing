"""Offline regression tests for full-snap download benchmarks."""

import contextlib
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from download.errors import DownloadBenchmarkError
from download.requests import ResolvedDownload
from download.metrics import SUMMARY_FIELDS
from download.load import download_command
from download.requests import download_request_headers
from download.load import download_worker_counts
from download.load import estimate_transfer_bytes
from download.metadata import generator_version
from download.metadata import run_metadata
from download.profile import load_profile
from download.cli import main
from download.metrics import parse_download_metrics
from download.load import render_lua_script
from download.requests import resolve_download_url
from download.requests import resolve_snap_download_url
from download.runner import run_benchmark
from download.requests import send_download_once
from download.metadata import write_metadata
from common.graph_data import GraphError
from common.graphs import graph_results
from common.graph_data import load_results
from common.graph_data import parse_latency_percentiles
from common.errors import ProfileError
from common.wrk import run_wrk2
from common.profile import wrk_time_seconds
from common.results import write_summary


PROFILES = Path(__file__).parent / "profiles"


def write_legacy_rate_profile(directory: Path) -> Path:
    document = yaml.safe_load((PROFILES / "helloworld-download-profile.yaml").read_text())
    document["target"].update(expected_size_bytes=1024, byte_range=None)
    del document["load"]["concurrent_downloads"]
    document["load"].update(
        downloads_per_second={"start": 1, "end": 10, "increment": 1},
        connections=10, scale_workers_with_rate=False,
    )
    path = directory / "legacy-rate.yaml"
    path.write_text(yaml.safe_dump(document))
    return path


class DownloadBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_profile(PROFILES / "gimp-download-profile.yaml")

    def test_script_uses_bundled_profile_from_another_directory(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--dry-run", "--wrk", "/custom/wrk"]), 0)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable, str(PROFILES.parent / "download_benchmark.py"),
                    "--dry-run", "--wrk", "/custom/wrk",
                ],
                cwd=directory, capture_output=True, text=True, check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, output.getvalue())

    def test_gimp_downloads_entire_snap(self) -> None:
        self.assertIsNone(self.profile.byte_range)
        self.assertEqual(self.profile.response_size_bytes, self.profile.expected_size_bytes)
        self.assertEqual(self.profile.expected_download_status, 200)
        self.assertNotIn("Range", dict(download_request_headers(self.profile)))
        script = render_lua_script(self.profile)
        self.assertIn("if status == 200 then", script)
        self.assertIn(f"if #body ~= {self.profile.expected_size_bytes} then", script)
        self.assertNotIn("Range", script)

    def test_gimp_sweeps_concurrency_without_a_rate(self) -> None:
        self.assertEqual(self.profile.rates, ())
        self.assertEqual(self.profile.stages, (2, 4, 8, 16))
        for count in self.profile.stages:
            with self.subTest(count=count):
                connections, threads = download_worker_counts(self.profile, count)
                self.assertEqual(connections, count)
                self.assertEqual(connections % threads, 0)
                command = download_command(
                    self.profile, count, binary="wrk", script_file=Path("download.lua"),
                    url="https://example.test/snap",
                )
                self.assertNotIn("--rate", command)
                self.assertEqual(command[command.index("--connections") + 1], str(count))
                self.assertEqual(command[command.index("--duration") + 1], "5m")
                self.assertEqual(command[command.index("--timeout") + 1], "5m")
        self.assertEqual(wrk_time_seconds(self.profile.duration), 300)
        self.assertEqual(wrk_time_seconds(self.profile.timeout), 300)

    def test_bundled_downloads_use_concurrency_and_gimp_load_settings_match(self) -> None:
        for path in PROFILES.glob("*-download-profile.yaml"):
            with self.subTest(path=path):
                profile = load_profile(path)
                self.assertEqual(profile.load_mode, "concurrency")
                self.assertEqual(profile.stages, (2, 4, 8, 16))
                self.assertEqual(profile.wrk_binary, "wrk")
        enterprise = load_profile(PROFILES / "enterprise-gimp-download-profile.yaml")
        for field in (
            "byte_range", "expected_download_status",
            "concurrent_downloads", "duration", "threads", "timeout",
        ):
            with self.subTest(field=field):
                self.assertEqual(getattr(enterprise, field), getattr(self.profile, field))

    def test_concurrency_transfer_is_unknown_and_requires_opt_in(self) -> None:
        self.assertIsNone(estimate_transfer_bytes(self.profile))
        with patch("download.requests.http_get") as http_get:
            with self.assertRaisesRegex(DownloadBenchmarkError, "unknown transfer volume"):
                run_benchmark(self.profile, binary="wrk", allow_over_budget=False)
            http_get.assert_not_called()

    def test_rate_profile_keeps_workers_fixed_and_retains_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = load_profile(write_legacy_rate_profile(Path(directory)))
        self.assertEqual(profile.load_mode, "rate")
        estimate = estimate_transfer_bytes(profile)
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate, (30 * 55 * 1024, 2 * 30 * 55 * 1024))
        for rate in profile.rates:
            command = download_command(
                profile, rate, binary="wrk2", script_file=Path("download.lua"),
                url="https://example.test/snap",
            )
            self.assertEqual(command[command.index("--rate") + 1], str(rate))
            self.assertEqual(command[command.index("--connections") + 1], "10")
            self.assertEqual(command[command.index("--threads") + 1], "2")
        with self.assertRaisesRegex(DownloadBenchmarkError, "above the"):
            run_benchmark(
                replace(profile, max_estimated_transfer_mib=1),
                binary="wrk2", allow_over_budget=False,
            )

    def test_invalid_load_configurations_are_rejected(self) -> None:
        invalid_loads = [
            {},
            {"concurrent_downloads": []},
            {"concurrent_downloads": "2,4"},
            {"concurrent_downloads": [0, 2]},
            {"concurrent_downloads": [True, 2]},
            {"concurrent_downloads": [2, 2]},
            {"concurrent_downloads": [4, 2]},
            {"concurrent_downloads": [2, 3]},
            {"concurrent_downloads": [2, 4], "connections": 4},
            {"concurrent_downloads": [2, 4], "scale_workers_with_rate": False},
            {"concurrent_downloads": [2, 4], "downloads_per_second": {}},
        ]
        document = yaml.safe_load(self.profile.profile_file.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            for load in invalid_loads:
                with self.subTest(load=load):
                    document["load"] = {"threads": 2, "duration": "2m", **load}
                    path.write_text(yaml.safe_dump(document))
                    with self.assertRaises(ProfileError):
                        load_profile(path)

    def test_fixed_rate_workers_reject_uneven_connection_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_legacy_rate_profile(Path(directory))
            document = yaml.safe_load(path.read_text())
            document["load"].update(connections=3, threads=2, scale_workers_with_rate=False)
            path.write_text(yaml.safe_dump(document))
            with self.assertRaisesRegex(ProfileError, "divide"):
                load_profile(path)

    def test_rate_worker_scaling_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_legacy_rate_profile(Path(directory))
            document = yaml.safe_load(path.read_text())
            document["load"]["scale_workers_with_rate"] = True
            path.write_text(yaml.safe_dump(document))
            output = io.StringIO()
            with patch("download.requests.http_get") as network, contextlib.redirect_stderr(output):
                self.assertEqual(main([str(path), "--run"]), 2)
                network.assert_not_called()
            self.assertIn("rate sweeps require fixed connections and threads", output.getvalue())
            del document["load"]["scale_workers_with_rate"]
            path.write_text(yaml.safe_dump(document))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path), "--dry-run"]), 0)

    def test_cli_selects_correct_backend_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy = write_legacy_rate_profile(Path(directory))
            cases = [
                (PROFILES / "gimp-download-profile.yaml", ["--wrk", "/custom/wrk"], "/custom/wrk", 0),
                (PROFILES / "gimp-download-profile.yaml", ["--wrk2", "wrk2"], "", 2),
                (PROFILES / "helloworld-download-profile.yaml", ["--wrk", "/custom/wrk"], "/custom/wrk", 0),
                (legacy, ["--wrk2", "/custom/wrk2"], "/custom/wrk2", 0),
                (legacy, ["--wrk", "wrk"], "", 2),
            ]
            for path, options, binary, expected in cases:
                with self.subTest(path=path, options=options):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                        with patch("download.requests.http_get") as http_get:
                            self.assertEqual(main([str(path), "--dry-run", *options]), expected)
                            http_get.assert_not_called()
                    if expected == 0:
                        self.assertIn(binary + " --threads", output.getvalue())

    def test_failed_generator_records_exit_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = replace(
                self.profile, explicit_url="https://example.test/snap",
                output_directory=Path(directory), warmup_requests=0,
            )
            with (
                patch("download.runner.require_wrk2", return_value="wrk"),
                patch("download.runner.generator_version", return_value="wrk test [epoll]"),
                patch("download.runner.resolve_download_url", return_value=profile.explicit_url),
                patch("download.runner.run_wrk2", return_value=subprocess.CompletedProcess(
                    ["wrk"], 1, "invalid options\n"
                )) as run,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(run_benchmark(profile, binary="wrk", allow_over_budget=True), 1)
            self.assertEqual(run.call_count, 1)
            summary = next(Path(directory).glob("*/summary.csv"))
            with summary.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["exit_code"], "1")
            self.assertEqual(rows[0]["log_file"], "connections-2.log")

    def test_configured_size_drives_validation_and_warmup(self) -> None:
        document = yaml.safe_load(self.profile.profile_file.read_text())
        url = "https://example.test/snap"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            for payload_size in (8, 65536):
                with self.subTest(payload_size=payload_size):
                    document["target"].update(expected_size_bytes=payload_size, byte_range=None)
                    document["preflight"]["expected_download_status"] = 200
                    path.write_text(yaml.safe_dump(document))
                    profile = load_profile(path)
                    self.assertEqual(profile.expected_size_bytes, payload_size)
                    self.assertEqual(profile.response_size_bytes, payload_size)
                    self.assertIn(
                        f"if #body ~= {payload_size} then", render_lua_script(profile),
                    )
                    with patch(
                        "download.requests.http_get", return_value=(200, {}, b"x" * payload_size),
                    ) as http_get:
                        self.assertEqual(resolve_download_url(profile, url), url)
                        self.assertEqual(send_download_once(profile, url), (200, payload_size))
                    for call in http_get.call_args_list:
                        self.assertNotIn("Range", dict(call.args[1]))

    def test_preflight_rejects_truncated_payload(self) -> None:
        profile = replace(self.profile, expected_size_bytes=8, response_size_bytes=8)
        with patch(
            "download.requests.http_get", return_value=(200, {}, b"short")
        ):
            with self.assertRaisesRegex(
                DownloadBenchmarkError, "returned 5 bytes; expected 8"
            ):
                resolve_download_url(profile, "https://example.test/snap")

    def test_achieved_rate_counts_only_successful_downloads(self) -> None:
        output = (
            "  120 requests in 120.00s, 20.00GB read\n"
            "Requests/sec: 1.00\n"
            "Transfer/sec: 170.67MB\n"
            "Expected HTTP status responses: 110\n"
            "Unexpected HTTP status responses: 10\n"
            "Unexpected response body sizes: 5\n"
        )
        for payload_size in (8, 65536):
            with self.subTest(payload_size=payload_size):
                profile = replace(
                    self.profile, expected_size_bytes=payload_size, response_size_bytes=payload_size,
                )
                metrics = parse_download_metrics(output, profile)
                self.assertEqual(metrics["requests_completed"], 120)
                self.assertEqual(metrics["successful_downloads"], 105)
                self.assertEqual(metrics["requests_per_second_achieved"], 0.875)
                self.assertEqual(
                    metrics["payload_mib_per_second"],
                    round(0.875 * payload_size / 1024**2, 6),
                )
                self.assertEqual(metrics["transfer_mib_per_second"], 170.67)

    def test_incomplete_transfers_do_not_count_as_downloads(self) -> None:
        output = (
            "  0 requests in 120.00s, 16.00MB read\n"
            "Requests/sec: 0.00\n"
            "Transfer/sec: 136.53KB\n"
            "Expected HTTP status responses: 0\n"
            "Unexpected HTTP status responses: 0\n"
            "Unexpected response body sizes: 0\n"
        )
        metrics = parse_download_metrics(output, self.profile)
        self.assertEqual(metrics["successful_downloads"], 0)
        self.assertEqual(metrics["requests_per_second_achieved"], 0)
        self.assertEqual(metrics["payload_mib_per_second"], 0)
        self.assertGreater(metrics["transfer_mib_per_second"], 0)

    def test_range_downloads_remain_supported(self) -> None:
        profile = replace(
            self.profile,
            expected_size_bytes=4096,
            byte_range=(0, 1023),
            response_size_bytes=1024,
            expected_download_status=206,
        )
        self.assertEqual(
            dict(download_request_headers(profile))["Range"], "bytes=0-1023"
        )
        script = render_lua_script(profile)
        self.assertIn("if status == 206 then", script)
        self.assertIn("if #body ~= 1024 then", script)
        self.assertIsNone(estimate_transfer_bytes(profile))
        rate_profile = replace(profile, concurrent_downloads=(), rates=(1, 2), duration="2m")
        self.assertEqual(estimate_transfer_bytes(rate_profile), (120 * 3 * 1024, 240 * 3 * 1024))


class DownloadSafetyTests(unittest.TestCase):
    def test_source_fingerprint_tracks_download_and_common_modules(self) -> None:
        profile = load_profile(PROFILES / "gimp-download-profile.yaml")
        resolved = ResolvedDownload("https://example.test/snap", 561, profile.expected_size_bytes)

        def fingerprint() -> str:
            metadata = run_metadata(
                profile, resolved, resolved.url, "wrk fixture [epoll]",
                confirm_stages=False, allow_over_budget=True,
            )
            return metadata["tools"]["benchmark_source_sha256"]

        original = fingerprint()
        read_bytes = Path.read_bytes
        for module in ("download/requests.py", "common/wrk.py"):
            changed_path = PROFILES.parent / module

            def changed_source(path: Path) -> bytes:
                content = read_bytes(path)
                return content + b"\n# changed\n" if path == changed_path else content

            with self.subTest(module=module), patch.object(Path, "read_bytes", changed_source):
                self.assertNotEqual(fingerprint(), original)
        with patch.object(Path, "read_bytes", side_effect=OSError("unreadable")):
            with self.assertRaisesRegex(DownloadBenchmarkError, "cannot fingerprint"):
                fingerprint()

    def run_fixture(
        self, output: str, *, answers=None, resolved_snap=False, rate_mode=False,
        payload_size=8192,
    ):
        with tempfile.TemporaryDirectory() as directory:
            profile = replace(
                load_profile(PROFILES / "gimp-download-profile.yaml"),
                expected_size_bytes=payload_size, response_size_bytes=payload_size,
                explicit_url=None if resolved_snap else "https://example.test/secret-path?token=secret-query",
                store_url="https://example.test/private-store?token=store-secret",
                store_headers=(("Authorization", "secret-auth"),),
                download_headers=(("X-Custom", "secret-header"),),
                output_directory=Path(directory), warmup_requests=0, pause_seconds=0,
                concurrent_downloads=() if rate_mode else (2, 4),
                rates=(2, 4) if rate_mode else (),
            )
            with (
                patch("download.runner.require_wrk2", return_value="wrk"),
                patch("download.runner.generator_version", return_value="wrk fixture [epoll]"),
                patch("download.runner.resolve_snap_download_url", return_value=ResolvedDownload(
                    "https://example.test/secret-path?token=secret-query", 561, profile.expected_size_bytes
                )),
                patch("download.runner.resolve_download_url", return_value="https://cdn.test/signed-path?signature=secret-signature"),
                patch("download.runner.run_wrk2", return_value=subprocess.CompletedProcess(
                    ["wrk"], 0, output
                )) as run,
                patch("builtins.input", side_effect=answers) as prompt,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = run_benchmark(
                    profile, binary="wrk", allow_over_budget=True,
                    confirm_stages=answers is not None,
                )
            summary = next(Path(directory).glob("*/summary.csv"))
            with summary.open() as stream:
                rows = list(csv.DictReader(stream))
            metadata_file = summary.with_name("metadata.json")
            metadata_text = metadata_file.read_text()
            self.assertEqual(metadata_file.stat().st_mode & 0o777, 0o600)
            for secret in (
                "secret-path", "secret-query", "private-store", "store-secret",
                "secret-auth", "secret-header", "signed-path", "secret-signature",
                str(directory),
            ):
                self.assertNotIn(secret, metadata_text)
            return result, rows, json.loads(metadata_text), run.call_count, prompt.call_count

    @staticmethod
    def output(*, completed=10, unexpected=0, body_errors=0, socket_errors="0, read 0, write 0, timeout 0"):
        return (
            f" {completed} requests in 1.00s, 1.00MB read\n"
            f"Requests/sec: {completed}.00\nTransfer/sec: 1.00MB\n"
            f"Expected HTTP status responses: {completed - unexpected}\n"
            f"Unexpected HTTP status responses: {unexpected}\n"
            f"Unexpected response body sizes: {body_errors}\n"
            f" Socket errors: connect {socket_errors}\n"
        )

    def test_errors_save_results_and_prevent_escalation(self) -> None:
        cases = [
            (self.output(unexpected=1), "unexpected_http_responses"),
            (self.output(body_errors=1), "body_size_errors"),
            (self.output(completed=0), "zero successful downloads"),
            (self.output(socket_errors="1, read 0, write 0, timeout 0"), "socket_connect_errors"),
            (self.output(socket_errors="0, read 1, write 0, timeout 0"), "socket_read_errors"),
            (self.output(socket_errors="0, read 0, write 1, timeout 0"), "socket_write_errors"),
            (self.output(socket_errors="0, read 0, write 0, timeout 1"), "socket_timeout_errors"),
        ]
        for rate_mode in (False, True):
            for output, reason in cases:
                with self.subTest(reason=reason, rate_mode=rate_mode):
                    result, rows, metadata, runs, prompts = self.run_fixture(
                        output, answers=["yes"], rate_mode=rate_mode,
                    )
                    self.assertEqual((result, len(rows), runs, prompts), (1, 1, 1, 0))
                    self.assertEqual(rows[0]["exit_code"], "0")
                    self.assertEqual(metadata["status"], "failed")
                    self.assertIn(reason, metadata["stop_reason"])
                    self.assertEqual(metadata["completed_stages"], 1)

    def test_confirmation_only_allows_explicit_yes(self) -> None:
        for answer in ("", "n", "maybe", EOFError(), "y", " YES "):
            with self.subTest(answer=answer):
                result, rows, metadata, runs, prompts = self.run_fixture(
                    self.output(), answers=[answer],
                )
                accepted = isinstance(answer, str) and answer.strip().lower() in {"y", "yes"}
                self.assertEqual(prompts, 1)
                self.assertEqual((len(rows), runs), (2, 2) if accepted else (1, 1))
                self.assertEqual(result, 0 if accepted else 1)
                self.assertEqual(metadata["status"], "completed" if accepted else "stopped")

    def test_metadata_records_resolved_revision_and_explicit_unknown(self) -> None:
        for resolved_snap, payload_size in ((False, 8), (True, 65536)):
            with self.subTest(resolved_snap=resolved_snap, payload_size=payload_size):
                result, rows, metadata, runs, prompts = self.run_fixture(
                    self.output(), resolved_snap=resolved_snap, payload_size=payload_size,
                )
                self.assertEqual((result, len(rows), runs, prompts), (0, 2, 2, 0))
                resolved = metadata["resolved_download"]
                self.assertEqual(resolved["revision"], 561 if resolved_snap else None)
                self.assertEqual(resolved["size_bytes"], payload_size)
                self.assertEqual(metadata["profile"]["expected_size_bytes"], payload_size)
                self.assertTrue(all(int(row["response_size_bytes"]) == payload_size for row in rows))
                self.assertEqual(resolved["download_origin"], "https://cdn.test")
                self.assertEqual(metadata["profile"]["concurrent_downloads"], [2, 4])
                self.assertEqual(metadata["tools"]["load_generator_version"], "wrk fixture [epoll]")
                self.assertEqual(len(metadata["tools"]["benchmark_source_sha256"]), 64)
                self.assertTrue(metadata["client"]["hostname"])
                self.assertIn("finished_at", metadata)

    def test_resolution_captures_revision_from_same_lookup(self) -> None:
        profile = load_profile(PROFILES / "gimp-download-profile.yaml")
        entry = {"revision": 561, "download": {"url": "/blob", "size": profile.expected_size_bytes}}
        with (
            patch("download.requests.fetch_store_info", return_value={}) as fetch,
            patch("download.requests.find_channel_map_entry", return_value=entry),
        ):
            result = resolve_snap_download_url(profile)
            fetch.assert_called_once()
            self.assertEqual(result, ResolvedDownload(
                "https://api.snapcraft.io/blob", 561, profile.expected_size_bytes
            ))
            entry["download"]["size"] = profile.expected_size_bytes + 1
            with self.assertRaisesRegex(DownloadBenchmarkError, "update the profile"):
                resolve_snap_download_url(profile)
            entry["download"]["size"] = profile.expected_size_bytes
            entry["revision"] = None
            with self.assertRaisesRegex(DownloadBenchmarkError, "valid revision"):
                resolve_snap_download_url(profile)

    def test_version_and_metadata_failures_are_explicit(self) -> None:
        for returncode, output, accepted in (
            (1, "wrk 4.1 [epoll]\nUsage:", True),
            (0, "wrk 4.0 [epoll]", True),
            (1, "broken binary", False),
            (2, "wrk 4.1 [epoll]", False),
        ):
            with self.subTest(returncode=returncode, output=output), patch(
                "download.metadata.subprocess.run",
                return_value=subprocess.CompletedProcess(["wrk"], returncode, output, ""),
            ):
                if accepted:
                    self.assertEqual(generator_version("wrk"), output.splitlines()[0])
                else:
                    with self.assertRaises(DownloadBenchmarkError):
                        generator_version("wrk")
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(DownloadBenchmarkError, "cannot write run metadata"):
                write_metadata(Path("metadata.json"), {})


class DownloadGraphTests(unittest.TestCase):
    def test_both_latency_formats_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "stage.log"
            for suffix in ("", ".000"):
                log.write_text(f" 50{suffix}% 20.0ms\n 75{suffix}% 30us\n 90{suffix}% 1s\n 99{suffix}% 2m\n")
                self.assertEqual(
                    parse_latency_percentiles(log),
                    {"p50": 20.0, "p75": 0.03, "p90": 1000, "p99": 120000},
                )

    def test_graph_axes_and_legacy_summaries(self) -> None:
        for mode, latency in (("concurrency", True), ("concurrency", False), ("rate", True)):
            with self.subTest(mode=mode, latency=latency), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                rows = []
                for count in (4, 2):
                    log = f"{mode}-{count}.log"
                    (run / log).write_text(
                        " 50% 1ms\n 75% 2ms\n 90% 3ms\n 99% 4ms\n" if latency else ""
                    )
                    row: dict[str, int | float | str] = dict.fromkeys(SUMMARY_FIELDS, 0)
                    row.update(
                        benchmark_kind="download", load_mode=mode,
                        requests_per_second_target="" if mode == "concurrency" else count,
                        requests_per_second_achieved=1.5, connections=count, threads=2,
                        download_host="localhost", log_file=log,
                    )
                    rows.append(row)
                fields = SUMMARY_FIELDS
                if mode == "rate":
                    fields = tuple(field for field in fields if field != "load_mode")
                    for row in rows:
                        del row["load_mode"]
                write_summary(run / "summary.csv", fields, rows)
                results = load_results(run)
                self.assertEqual([result.load_value for result in results], [2, 4])
                with patch("matplotlib.figure.Figure.savefig", autospec=True) as save:
                    graph_results(run, results, run / "graphs.png")
                    save.assert_called_once()
                axes = save.call_args.args[0].axes
                self.assertEqual(
                    [line.get_label() for line in axes[1].lines],
                    ["Completed, validated payload", "Bytes read (includes incomplete transfers)"],
                )
                labels = [line.get_label() for line in axes[2].lines]
                self.assertEqual("Target" in labels, mode == "rate")
                self.assertEqual(axes[2].lines[-1].get_xdata().tolist(), [2, 4])
                self.assertIn(
                    "Concurrent downloads" if mode == "concurrency" else "Target downloads",
                    axes[-1].get_xlabel(),
                )
                if not latency:
                    self.assertIn("No positive latency", axes[0].texts[0].get_text())
                if mode == "concurrency":
                    rows[0]["load_mode"] = "rate"
                    rows[0]["requests_per_second_target"] = 4
                    write_summary(run / "summary.csv", fields, rows)
                    with self.assertRaisesRegex(GraphError, "cannot combine"):
                        load_results(run)


@unittest.skipUnless(shutil.which("wrk"), "standard wrk is required for localhost integration")
class LocalConcurrencyTests(unittest.TestCase):
    def test_cancelled_transfers_are_excluded_from_completed_metrics(self) -> None:
        payload = b"x" * 65536
        for complete_first in (False, True):
            with self.subTest(complete_first=complete_first):
                stop = threading.Event()

                class Handler(BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.1"

                    def handle(self) -> None:
                        self.completed_responses = 0
                        try:
                            super().handle()
                        except ConnectionResetError:
                            pass  # The stage deadline closes active connections.

                    def do_GET(self) -> None:
                        try:
                            self.send_response(200)
                            self.send_header("Content-Length", str(len(payload)))
                            self.end_headers()
                            if complete_first and self.completed_responses == 0:
                                self.wfile.write(payload)
                            else:
                                for offset in range(0, len(payload), 1024):
                                    if stop.wait(0.05):
                                        return
                                    self.wfile.write(payload[offset:offset + 1024])
                            self.completed_responses += 1
                        except (BrokenPipeError, ConnectionResetError):
                            pass  # Deliberately slow responses outlive the stage.

                    def log_message(self, format: str, *args: object) -> None:
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    with tempfile.TemporaryDirectory() as directory:
                        profile = replace(
                            load_profile(PROFILES / "gimp-download-profile.yaml"),
                            explicit_url=f"http://127.0.0.1:{server.server_port}/snap",
                            expected_size_bytes=len(payload), response_size_bytes=len(payload),
                            concurrent_downloads=(2,), duration="1s", timeout="5s",
                            pause_seconds=0, warmup_requests=0,
                            output_directory=Path(directory),
                        )
                        # Bypass preflight: this fixture intentionally never completes
                        # its slow responses before the measurement deadline.
                        with (
                            patch("download.runner.resolve_download_url", return_value=profile.explicit_url),
                            contextlib.redirect_stdout(io.StringIO()),
                        ):
                            self.assertEqual(
                                run_benchmark(profile, binary="wrk", allow_over_budget=True),
                                0 if complete_first else 1,
                            )
                        summary = next(Path(directory).glob("*/summary.csv"))
                        with summary.open() as stream:
                            row = next(csv.DictReader(stream))
                        expected_completed = 2 if complete_first else 0
                        self.assertEqual(int(row["requests_completed"]), expected_completed)
                        self.assertEqual(int(row["successful_downloads"]), expected_completed)
                        self.assertEqual(int(row["observed_expected_status_responses"]), expected_completed)
                        successful_rate = float(row["requests_per_second_achieved"])
                        payload_rate = float(row["payload_mib_per_second"])
                        self.assertAlmostEqual(payload_rate, successful_rate * len(payload) / 1024**2, places=5)
                        self.assertGreater(float(row["transfer_mib_per_second"]), payload_rate * 1.1)
                        results = load_results(summary.parent)
                        if not complete_first:
                            self.assertEqual(successful_rate, 0)
                            self.assertEqual(payload_rate, 0)
                            self.assertFalse(any(value > 0 for value in results[0].latencies_ms.values()))
                        graph_results(summary.parent, results, summary.parent / "graphs.png")
                finally:
                    stop.set()
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_real_wrk_sweep_and_graph(self) -> None:
        lock = threading.Lock()
        active = 0
        peak = 0
        payload = b"x" * 4096

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle(self) -> None:
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    super().handle()
                except ConnectionResetError:
                    pass  # wrk also resets idle keep-alive sockets when a stage ends.
                finally:
                    with lock:
                        active -= 1

            def do_GET(self) -> None:
                try:
                    time.sleep(0.02)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # wrk closes in-flight requests at the stage deadline.

            def log_message(self, format: str, *args: object) -> None:
                pass

        observed_peaks = []

        def measure(command: list[str]) -> subprocess.CompletedProcess[str]:
            nonlocal peak
            with lock:
                peak = 0
            completed = run_wrk2(command)
            with lock:
                observed_peaks.append(peak)
            return completed

        class Server(ThreadingHTTPServer):
            request_queue_size = 64

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                profile = replace(
                    load_profile(PROFILES / "gimp-download-profile.yaml"),
                    explicit_url=f"http://127.0.0.1:{server.server_port}/snap",
                    expected_size_bytes=len(payload), response_size_bytes=len(payload),
                    duration="2s", timeout="1s", pause_seconds=0.1, warmup_requests=0,
                    output_directory=Path(directory),
                )
                with patch("download.runner.run_wrk2", side_effect=measure), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(run_benchmark(profile, binary="wrk", allow_over_budget=True), 0)
                self.assertEqual(observed_peaks, [2, 4, 8, 16])
                summary = next(Path(directory).glob("*/summary.csv"))
                results = load_results(summary.parent)
                self.assertEqual([result.load_value for result in results], [2, 4, 8, 16])
                for result in results:
                    self.assertGreater(result.achieved_rps, 0)
                    self.assertEqual(result.unexpected_http_responses, 0)
                    self.assertEqual(result.body_size_errors, 0)
                    self.assertTrue(result.latencies_ms)
                output = summary.parent / "graphs.png"
                graph_results(summary.parent, results, output)
                self.assertEqual(output.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
