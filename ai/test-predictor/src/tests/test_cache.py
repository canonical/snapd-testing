import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
                "job_id": 107455174491,
                "run_id": 35731362637,
                "success": success,
            })

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

    def test_context_includes_github_ids(self):
        history = self.cache.get_context(
            "ubuntu-22.04-64", "tests/main/example", "executing",
            scenario="master", backend="openstack",
        )

        self.assertEqual(history[0]["job_id"], 107455174491)
        self.assertEqual(history[0]["run_id"], 35731362637)

    def test_prime_from_disk_recovers_github_ids_from_filename(self):
        with TemporaryDirectory() as ts_dir:
            filename = (
                "results_job_107455174491_run_35731362637_"
                "scenario_master_attempt_6.ts"
            )
            Path(ts_dir, filename).write_text(
                "system,name,verb,backend,scenario,success,attempt,start\n"
                "ubuntu-22.04-64,tests/main/example,executing,openstack,"
                "master,1,6,2026-09-24T10:00:00Z\n"
            )
            cache = SystemStateCache(history_size=10)

            cache.prime_from_disk(ts_dir)

            history = cache.get_context(
                "ubuntu-22.04-64", "tests/main/example", "executing",
                scenario="master", backend="openstack",
            )
            self.assertEqual(history[0]["job_id"], 107455174491)
            self.assertEqual(history[0]["run_id"], 35731362637)


if __name__ == "__main__":
    unittest.main()