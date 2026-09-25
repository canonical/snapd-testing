import unittest

from common.processor import _clean_and_transform_data, extract_github_ids


class TestProcessorProvenance(unittest.TestCase):
    def test_extracts_github_ids_from_ingestion_filename(self):
        job_id, run_id = extract_github_ids(
            "results_job_107455174491_run_35731362637_scenario_master_attempt_6.json"
        )

        self.assertEqual(job_id, 107455174491)
        self.assertEqual(run_id, 35731362637)

    def test_adds_github_ids_to_each_result(self):
        raw_data = {
            "items": [{
                "instance": "instance-1",
                "start": "2026-09-24T10:00:00Z",
                "end": "2026-09-24T10:00:01Z",
                "verb": "executing",
                "name": "tests/main/example",
                "variant": "",
                "level": "task",
                "backend": "openstack",
                "system": "ubuntu-24.04-64",
                "success": True,
            }]
        }

        frame, _ = _clean_and_transform_data(
            raw_data, "master", 6, 107455174491, 35731362637
        )

        self.assertEqual(frame["job_id"].tolist(), [107455174491])
        self.assertEqual(frame["run_id"].tolist(), [35731362637])


if __name__ == "__main__":
    unittest.main()