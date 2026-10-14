import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import Flask

from common.cache import SystemStateCache
from services.jobs import predictor
from services.jobs.context import register_context_endpoint


class TestContextApi(unittest.TestCase):
    def test_prediction_does_not_reorder_context_results(self):
        original_cache = predictor.app.state_cache
        self.addCleanup(setattr, predictor.app, "state_cache", original_cache)

        predictor.app.state_cache = SystemStateCache(history_size=25)
        prs = [1, None, 3, 4, 5, 6, 4, 3, None, 2, 2, 1]
        for run_id, pr in enumerate(prs, start=100):
            predictor.app.state_cache.update({
                "system": "opensuse-tumbleweed-selinux-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "generic",
                "level": "task",
                "run_id": run_id,
                "pr": pr,
                "success": run_id % 2,
            })

        client = predictor.app.test_client()
        prediction_response = client.post(
            "/internal/predict",
            json={
                "system": "opensuse-tumbleweed-selinux-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "generic",
                "level": "task",
            },
        )
        self.assertEqual(prediction_response.status_code, 200)

        context_response = client.get(
            "/internal/context",
            query_string={
                "system": "opensuse-tumbleweed-selinux-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "generic",
                "level": "task",
            },
        )

        self.assertEqual(context_response.status_code, 200)
        history = context_response.get_json()["history"]
        self.assertEqual([item["pr"] for item in history], prs)
        self.assertEqual([item["run_id"] for item in history], list(range(100, 112)))

    def test_context_filters_by_level(self):
        app = Flask(__name__)
        app.state_cache = SystemStateCache(history_size=14)
        for level in ("task", "project"):
            app.state_cache.update({
                "system": "opensuse-16.0-64",
                "name": "unknown_name",
                "verb": "restoring",
                "backend": "garden",
                "scenario": "generic",
                "level": level,
                "success": 0,
            })
        register_context_endpoint(app)

        response = app.test_client().get(
            "/internal/context",
            query_string={
                "system": "opensuse-16.0-64",
                "name": "unknown_name",
                "verb": "restoring",
                "backend": "garden",
                "scenario": "generic",
                "level": "project",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["level"], "project")
        self.assertEqual(len(payload["history"]), 1)
        self.assertEqual(payload["history"][0]["level"], "project")

    def test_rebuild_cache_from_files_without_training(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ts_dir = root / "ts"
            model_dir = root / "model"
            ts_dir.mkdir()
            filename = (
                "results_job_107455174491_run_35731362637_"
                "scenario_generic_attempt_5.ts"
            )
            (ts_dir / filename).write_text(
                "system,name,verb,backend,scenario,success,attempt,start\n"
                "debian-sid-64,tests/main/snapd-reexec:snapd,executing,"
                "openstack,generic,0,5,2026-09-25T18:02:35Z\n",
                encoding="utf-8",
            )

            app = Flask(__name__)
            app.state_cache = SystemStateCache(history_size=14)
            register_context_endpoint(app)

            with (
                patch("services.jobs.context.config.TS_DIR", str(ts_dir)),
                patch("services.jobs.context.config.MODEL_DIR", str(model_dir)),
            ):
                response = app.test_client().post("/internal/rebuild-cache")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["cache"]["entries"], 1)
            self.assertEqual(payload["cache"]["missing_provenance"], 0)
            self.assertEqual(
                payload["cache"]["provenance_samples"],
                [{"job_id": 107455174491, "run_id": 35731362637}],
            )
            self.assertTrue(Path(app.state_cache.snapshot_path).exists())

            history = app.state_cache.get_context(
                "debian-sid-64", "tests/main/snapd-reexec:snapd", "executing",
                scenario="generic", backend="openstack",
            )
            self.assertEqual(history[0]["job_id"], 107455174491)
            self.assertEqual(history[0]["run_id"], 35731362637)

    def test_file_to_snapshot_to_context_response_preserves_github_ids(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ts_dir = root / "ts"
            ts_dir.mkdir()
            filename = (
                "results_job_107455174491_run_35731362637_"
                "scenario_generic_attempt_5.ts"
            )
            (ts_dir / filename).write_text(
                "system,name,verb,backend,scenario,success,attempt,start\n"
                "debian-sid-64,tests/main/snapd-reexec:snapd,executing,"
                "openstack,generic,0,5,2026-09-25T18:02:35Z\n",
                encoding="utf-8",
            )

            built_cache = SystemStateCache(history_size=14)
            built_cache.prime_from_disk(str(ts_dir))
            snapshot_path = root / "cache_snapshot.pkl"
            built_cache.snapshot_path = str(snapshot_path)
            built_cache.save_snapshot()

            restored_cache = SystemStateCache(history_size=14)
            restored_cache.snapshot_path = str(snapshot_path)
            restored_cache.initialize()

            app = Flask(__name__)
            app.state_cache = restored_cache
            register_context_endpoint(app)

            response = app.test_client().get(
                "/internal/context",
                query_string={
                    "system": "debian-sid-64",
                    "name": "tests/main/snapd-reexec:snapd",
                    "verb": "executing",
                    "backend": "openstack",
                    "scenario": "generic",
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["system"], "debian-sid-64")
            self.assertEqual(payload["name"], "tests/main/snapd-reexec:snapd")
            self.assertEqual(len(payload["history"]), 1)
            self.assertEqual(payload["history"][0]["job_id"], 107455174491)
            self.assertEqual(payload["history"][0]["run_id"], 35731362637)


if __name__ == "__main__":
    unittest.main()