import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common import config
from common.cache import SystemStateCache


class TestSystemStateCache(unittest.TestCase):
    def setUp(self):
        self.cache = SystemStateCache(history_size=10)
        for backend, scenario, success in (
            ("openstack", "master", 1),
            ("openstack", "kernels", 0),
            ("google", "master", 0),
        ):
            self.cache.update({
                "system": "ubuntu-22.04-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": backend,
                "scenario": scenario,
                "level": "task",
                "job_id": 107455174491,
                "run_id": 35731362637,
                "pr": 17719,
                "success": success,
            })

    def test_blank_name_uses_unknown_name(self):
        normalized = self.cache._normalize_entry({"name": ""})

        self.assertEqual(normalized["name"], "unknown_name")

    def test_context_filters_backend(self):
        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            backend="openstack",
        )

        self.assertEqual(
            [item["backend"] for item in history],
            ["openstack", "openstack"],
        )

    def test_context_does_not_fallback_for_unknown_backend(self):
        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            backend="openstack-ext",
        )

        self.assertEqual(history, [])

    def test_context_filters_backend_and_scenario(self):
        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["scenario"], "master")

    def test_context_preserves_and_filters_level(self):
        self.cache.update({
            "system": "ubuntu-22.04-64",
            "name": "tests/main/example",
            "verb": "executing",
            "backend": "openstack",
            "scenario": "master",
            "level": "test",
            "success": 0,
        })

        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack", level="test",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["level"], "test")

    def test_context_includes_github_ids(self):
        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
        )

        self.assertEqual(history[0]["job_id"], 107455174491)
        self.assertEqual(history[0]["run_id"], 35731362637)
        self.assertEqual(history[0]["pr"], 17719)

    def test_snapshot_round_trip_preserves_github_ids(self):
        with TemporaryDirectory() as snapshot_dir:
            snapshot_path = Path(snapshot_dir, "cache_snapshot.pkl")
            self.cache.snapshot_path = str(snapshot_path)
            self.cache.save_snapshot()

            restored_cache = SystemStateCache(history_size=10)
            restored_cache.snapshot_path = str(snapshot_path)
            restored_cache.initialize()

            history = restored_cache.get_context(
                "ubuntu-22.04-64", "tests/main/example", "executing",
                scenario="master", backend="openstack",
            )
            self.assertEqual(history[0]["job_id"], 107455174491)
            self.assertEqual(history[0]["run_id"], 35731362637)
            self.assertEqual(history[0]["pr"], 17719)

    def test_prime_from_disk_recovers_github_ids_from_filename(self):
        with TemporaryDirectory() as ts_dir:
            filename = (
                "results_job_107455174491_run_35731362637_"
                "scenario_master_attempt_6.ts"
            )
            Path(ts_dir, filename).write_text(
                "system,name,verb,backend,scenario,success,attempt,start,job_id,run_id\n"
                "ubuntu-22.04-64,tests/main/example,executing,openstack,"
                "master,1,6,2026-09-24T10:00:00Z,None,None\n"
            )
            cache = SystemStateCache(history_size=10)

            cache.prime_from_disk(ts_dir)

            history = cache.get_context(
                "ubuntu-22.04-64", "tests/main/example", "executing",
                scenario="master", backend="openstack",
            )
            self.assertEqual(history[0]["job_id"], 107455174491)
            self.assertEqual(history[0]["run_id"], 35731362637)
            self.assertIsNone(history[0]["pr"])

    def test_cache_stores_all_pr_entries_but_context_uses_latest_two(self):
        with TemporaryDirectory() as ts_dir:
            files = (
                (1, 100, 17719, "2026-09-20T10:00:00Z"),
                (2, 101, 17719, "2026-09-21T10:00:00Z"),
                (3, 102, 17719, "2026-09-22T10:00:00Z"),
                (4, 102, 17719, "2026-09-22T11:00:00Z"),
            )
            for job_id, run_id, pr, start in files:
                filename = (
                    f"results_job_{job_id}_run_{run_id}_pr_{pr}_"
                    "scenario_generic_attempt_1.ts"
                )
                Path(ts_dir, filename).write_text(
                    "system,name,verb,backend,scenario,success,attempt,start,job_id,run_id\n"
                    "ubuntu-24.04-64,tests/main/example,executing,openstack,"
                    f"generic,1,1,{start},{job_id},{run_id}\n"
                )

            non_pr_files = (
                (5, 90, "2026-09-17T10:00:00Z"),
                (6, 91, "2026-09-18T10:00:00Z"),
                (7, 92, "2026-09-19T10:00:00Z"),
            )
            for job_id, run_id, start in non_pr_files:
                filename = (
                    f"results_job_{job_id}_run_{run_id}_"
                    "scenario_generic_attempt_1.ts"
                )
                Path(ts_dir, filename).write_text(
                    "system,name,verb,backend,scenario,success,attempt,start,job_id,run_id\n"
                    "ubuntu-24.04-64,tests/main/example,executing,openstack,"
                    f"generic,1,1,{start},{job_id},{run_id}\n"
                )
            cache = SystemStateCache(history_size=10)

            cache.prime_from_disk(ts_dir)

            stored = cache.cache["ubuntu-24.04-64"]["tests/main/example"]["executing"]
            self.assertEqual(
                [item["run_id"] for item in stored],
                [90, 91, 92, 100, 101, 102, 102],
            )

            history = cache.get_context(
                "ubuntu-24.04-64", "tests/main/example", "executing",
                scenario="generic", backend="openstack",
            )
            self.assertEqual(
                [item["run_id"] for item in history],
                [90, 91, 92, 102, 102],
            )
            self.assertNotIn(100, [item["run_id"] for item in history])
            self.assertNotIn(101, [item["run_id"] for item in history])

    def test_pr_limit_is_applied_independently_to_each_context(self):
        with TemporaryDirectory() as ts_dir:
            files = (
                (1, 100, "ubuntu-24.04-64", "2026-09-20T10:00:00Z"),
                (2, 101, "ubuntu-24.04-64", "2026-09-21T10:00:00Z"),
                (3, 102, "ubuntu-26.10-64", "2026-09-22T10:00:00Z"),
                (4, 103, "ubuntu-26.10-64", "2026-09-23T10:00:00Z"),
            )
            for job_id, run_id, system, start in files:
                filename = (
                    f"results_job_{job_id}_run_{run_id}_pr_17719_"
                    "scenario_generic_attempt_1.ts"
                )
                Path(ts_dir, filename).write_text(
                    "system,name,verb,backend,scenario,success,attempt,start,job_id,run_id\n"
                    f"{system},tests/main/example,executing,openstack,"
                    f"generic,1,1,{start},{job_id},{run_id}\n"
                )

            cache = SystemStateCache(history_size=10)
            cache.prime_from_disk(ts_dir)

            history = cache.get_context(
                "ubuntu-24.04-64", "tests/main/example", "executing",
                scenario="generic", backend="openstack",
            )
            self.assertEqual([item["run_id"] for item in history], [100, 101])

    def test_live_updates_store_all_pr_entries_but_context_uses_latest_two(self):
        cache = SystemStateCache(history_size=10)
        for run_id in range(100, 104):
            cache.update({
                "system": "ubuntu-24.04-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "generic",
                "pr": 17719,
                "run_id": run_id,
                "success": 1,
            })

        stored = cache.cache["ubuntu-24.04-64"]["tests/main/example"]["executing"]
        self.assertEqual([item["run_id"] for item in stored], [100, 101, 102, 103])

        history = cache.get_context(
            "ubuntu-24.04-64", "tests/main/example", "executing",
            scenario="generic", backend="openstack",
        )
        self.assertEqual([item["run_id"] for item in history], [102, 103])

    def test_context_limits_each_pr_independently_and_keeps_non_pr_entries(self):
        cache = SystemStateCache(history_size=10)
        for pr, run_id in (
            (None, 90),
            (17719, 100),
            (17719, 101),
            (17720, 200),
            (17719, 102),
            (17720, 201),
            (17720, 202),
        ):
            cache.update({
                "system": "ubuntu-24.04-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "generic",
                "pr": pr,
                "run_id": run_id,
                "success": 1,
            })

        history = cache.get_context(
            "ubuntu-24.04-64", "tests/main/example", "executing",
            scenario="generic", backend="openstack",
        )
        self.assertEqual(
            [(item["pr"], item["run_id"]) for item in history],
            [(None, 90), (17719, 101), (17719, 102), (17720, 201), (17720, 202)],
        )


class TestSystemStateCacheCapacity(unittest.TestCase):
    """Cache history uses the configured probabilistic prediction depth."""

    def _fill(self, cache, count):
        for i in range(count):
            cache.update({
                "system": "ubuntu-22.04-64",
                "name": "tests/main/example",
                "verb": "executing",
                "backend": "openstack",
                "scenario": "master",
                "job_id": 1,
                "run_id": i,
                "success": i % 2,
            })

    def test_default_history_size_uses_cache_history_size_config(self):
        cache = SystemStateCache()
        self.assertEqual(cache.history_size, config.CACHE_HISTORY_SIZE)

    def test_cache_retains_history_up_to_prediction_depth(self):
        cache = SystemStateCache()
        total = config.CACHE_HISTORY_SIZE
        self._fill(cache, total)

        stored = cache.cache["ubuntu-22.04-64"]["tests/main/example"]["executing"]
        self.assertEqual(len(stored), total)

    def test_cache_storage_is_not_capped_by_prediction_depth(self):
        cache = SystemStateCache()
        total = config.CACHE_HISTORY_SIZE + 5
        self._fill(cache, total)

        stored = cache.cache["ubuntu-22.04-64"]["tests/main/example"]["executing"]
        self.assertEqual(len(stored), total)

        history = cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
        )
        self.assertEqual(len(history), config.CACHE_HISTORY_SIZE)
        self.assertEqual(stored[0]["run_id"], 0)
        self.assertEqual(stored[-1]["run_id"], total - 1)
        self.assertEqual(history[0]["run_id"], total - config.CACHE_HISTORY_SIZE)

    def test_get_context_defaults_to_cache_capacity(self):
        cache = SystemStateCache()
        total = config.CACHE_HISTORY_SIZE + 10
        self._fill(cache, total)

        history = cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
        )
        self.assertEqual(len(history), config.CACHE_HISTORY_SIZE)
        self.assertEqual(history[-1]["run_id"], total - 1)

    def test_get_context_max_items_override_returns_deeper_history(self):
        cache = SystemStateCache()
        total = config.CACHE_HISTORY_SIZE + 5
        self._fill(cache, total)

        history = cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
            max_items=config.CACHE_HISTORY_SIZE,
        )
        self.assertEqual(len(history), config.CACHE_HISTORY_SIZE)
        self.assertEqual(history[-1]["run_id"], total - 1)


if __name__ == "__main__":
    unittest.main()