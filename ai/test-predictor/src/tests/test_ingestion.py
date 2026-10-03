import unittest

from services.api.ingestion import build_results_filename


class TestIngestionFilename(unittest.TestCase):
    def test_includes_pr_after_run_id(self):
        filename = build_results_filename("123", "456", "789", "master", "2")

        self.assertEqual(
            filename,
            "results_job_123_run_456_pr_789_scenario_master_attempt_2.json",
        )

    def test_omits_pr_for_non_pr_execution(self):
        filename = build_results_filename("123", "456", None, "master", "2")

        self.assertEqual(
            filename,
            "results_job_123_run_456_scenario_master_attempt_2.json",
        )


if __name__ == "__main__":
    unittest.main()