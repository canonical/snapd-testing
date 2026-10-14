import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from common.processor import (
    _clean_and_transform_data,
    extract_github_ids,
    extract_pr,
    process_results,
    select_recent_pr_runs,
)


class TestProcessorProvenance(unittest.TestCase):
    def test_selects_latest_entries_per_pr(self):
        files = [
            "results_job_1_run_100_pr_10_scenario_generic_attempt_1.ts",
            "results_job_2_run_101_pr_10_scenario_generic_attempt_1.ts",
            "results_job_3_run_102_pr_10_scenario_generic_attempt_1.ts",
            "results_job_4_run_102_pr_10_scenario_generic_attempt_1.ts",
            "results_job_5_run_200_pr_20_scenario_generic_attempt_1.ts",
            "results_job_6_run_201_pr_20_scenario_generic_attempt_1.ts",
            "results_job_7_run_202_pr_20_scenario_generic_attempt_1.ts",
            "results_job_8_run_300_scenario_generic_attempt_1.ts",
        ]

        selected = select_recent_pr_runs(files, runs_limit=2)

        self.assertEqual(
            selected,
            [files[2], files[3], files[5], files[6], files[7]],
        )

    def test_pr_run_limit_can_be_disabled(self):
        files = [
            "results_job_1_run_100_pr_10_scenario_generic_attempt_1.ts",
            "results_job_2_run_101_pr_10_scenario_generic_attempt_1.ts",
        ]

        self.assertEqual(select_recent_pr_runs(files, runs_limit=0), files)

    def test_extracts_github_ids_from_ingestion_filename(self):
        job_id, run_id = extract_github_ids(
            "results_job_107455174491_run_35731362637_scenario_master_attempt_6.json"
        )

        self.assertEqual(job_id, 107455174491)
        self.assertEqual(run_id, 35731362637)

    def test_extracts_optional_pr_from_ingestion_filename(self):
        self.assertEqual(
            extract_pr("results_job_1_run_2_pr_17719_scenario_generic_attempt_1.ts"),
            17719,
        )
        self.assertIsNone(
            extract_pr("results_job_1_run_2_scenario_generic_attempt_1.ts")
        )

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

    def test_archives_only_aborted_rows_as_csv(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results_dir = root / "results"
            ts_dir = root / "ts"
            archive_dir = root / "archive"
            results_dir.mkdir()
            ts_dir.mkdir()
            source = results_dir / "results_job_1_run_2_scenario_master_attempt_1.json"
            source.write_text(json.dumps({"items": [
                {
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
                    "aborted": False,
                },
                {
                    "instance": "instance-1",
                    "start": "2026-09-24T10:00:01Z",
                    "end": "2026-09-24T10:00:02Z",
                    "verb": "executing",
                    "name": "tests/main/aborted",
                    "variant": "",
                    "level": "task",
                    "backend": "openstack",
                    "system": "ubuntu-24.04-64",
                    "success": False,
                    "aborted": True,
                },
            ]}), encoding="utf-8")

            process_results([str(source)], str(ts_dir), str(archive_dir))

            self.assertFalse(source.exists())
            archive = archive_dir / source.name.replace(".json", ".csv")
            archived = archive.read_text(encoding="utf-8")
            self.assertIn("tests/main/aborted", archived)
            self.assertNotIn("tests/main/example", archived)
            self.assertIn("job_id,run_id,scenario,attempt", archived)
            transformed = (ts_dir / source.name.replace(".json", ".ts")).read_text(
                encoding="utf-8"
            )
            self.assertIn("tests/main/example", transformed)
            self.assertNotIn("tests/main/aborted", transformed)


if __name__ == "__main__":
    unittest.main()