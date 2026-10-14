import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import Flask

from common import config
from common.stats import build_stats_snapshot
from services.api.stats import (
    count_filtered_outcomes,
    get_aborted_stats,
    get_all_systems_stats,
    get_all_targets_stats,
    stats_bp,
)


class TestStatsApi(unittest.TestCase):
    def test_counts_results_for_selected_backend_and_system(self):
        with TemporaryDirectory() as temp_dir:
            data = Path(temp_dir) / "results.ts"
            data.write_text(
                "backend,system,name,verb,success\n"
                "openstack,ubuntu-24.04-64,tests/main/one,executing,1\n"
                "openstack,ubuntu-24.04-64,tests/main/two,preparing,0\n"
                "openstack-ext,ubuntu-24.04-64,tests/main/three,executing,1\n",
                encoding="utf-8",
            )

            passed, failed, matching_files = count_filtered_outcomes(
                temp_dir,
                {"backend": "openstack", "system": "ubuntu-24.04-64"},
            )

        self.assertEqual((passed, failed, matching_files), (1, 1, 1))

    def test_counts_every_retained_run_for_same_pr(self):
        with TemporaryDirectory() as temp_dir:
            for run_id in (100, 101, 102):
                data = Path(temp_dir) / (
                    f"results_job_{run_id}_run_{run_id}_pr_10_"
                    "scenario_generic_attempt_1.ts"
                )
                data.write_text(
                    "runid,job_id,backend,system,name,verb,scenario,attempt,success,start\n"
                    f"batch-{run_id},{run_id},openstack,ubuntu-24.04-64,"
                    "tests/main/one,executing,generic,1,1,2026-10-14T10:00:00Z\n",
                    encoding="utf-8",
                )

            passed, failed, matching_files = count_filtered_outcomes(
                temp_dir,
                {"backend": "openstack", "system": "ubuntu-24.04-64"},
            )

        self.assertEqual((passed, failed, matching_files), (3, 0, 3))

    def test_failure_overrides_pass_across_files(self):
        with TemporaryDirectory() as temp_dir:
            header = (
                "runid,job_id,backend,system,name,verb,scenario,attempt,success\n"
            )
            (Path(temp_dir) / "pass.ts").write_text(
                header
                + "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,"
                "executing,generic,1,1\n",
                encoding="utf-8",
            )
            (Path(temp_dir) / "fail.ts").write_text(
                header
                + "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,"
                "restoring,generic,1,0\n",
                encoding="utf-8",
            )

            passed, failed, matching_files = count_filtered_outcomes(
                temp_dir,
                {"backend": "openstack", "scenario": "generic"},
            )
            system_stats = get_all_systems_stats(
                temp_dir, {"scenario": "generic"}
            )
            target_stats = get_all_targets_stats(
                temp_dir, {"scenario": "generic"}
            )

        self.assertEqual((passed, failed, matching_files), (0, 1, 2))
        self.assertEqual(
            system_stats["ubuntu-24.04-64"], {"pass": 0, "fail": 1}
        )
        self.assertEqual(
            target_stats[("openstack", "ubuntu-24.04-64")],
            {"pass": 0, "fail": 1},
        )

    def test_counts_aborted_tests_from_archived_csv(self):
        with TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir)
            data = archive / "results_job_1_run_2_scenario_master_attempt_3.csv"
            data.write_text(
                "name,variant,system,backend,verb,aborted,scenario,attempt\n"
                "tests/main/one,fast,ubuntu-24.04-64,openstack,executing,true,master,3\n"
                "tests/main/two,,ubuntu-24.04-64,openstack,executing,true,master,3\n",
                encoding="utf-8",
            )

            stats = get_aborted_stats(temp_dir, {
                "system": "ubuntu-24.04-64",
                "scenario": "master",
                "attempt": 3,
            })

        self.assertEqual(stats["aborted"], 2)
        self.assertEqual(stats["matching_files"], 1)
        self.assertEqual(stats["by_test"], {
            "tests/main/one:fast": 1,
            "tests/main/two": 1,
        })
        self.assertEqual(stats["by_system"], {"ubuntu-24.04-64": 2})

    def test_snapshot_filters_inclusive_dates(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ts_dir = root / "ts"
            archive_dir = root / "archive"
            model_dir = root / "model"
            ts_dir.mkdir()
            archive_dir.mkdir()
            model_dir.mkdir()

            (ts_dir / "results.ts").write_text(
                "runid,job_id,backend,system,name,verb,scenario,attempt,success,start\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,executing,generic,1,1,2026-10-14T10:00:00Z\n"
                "batch-2,2,openstack,ubuntu-24.04-64,tests/main/two,restoring,generic,1,0,2026-10-15T10:00:00Z\n"
                "batch-3,3,openstack,ubuntu-24.04-64,tests/main/three,executing,generic,1,1,2026-10-16T10:00:00Z\n",
                encoding="utf-8",
            )
            (archive_dir / "results.csv").write_text(
                "name,variant,system,backend,verb,aborted,scenario,attempt,start\n"
                "tests/main/aborted,,ubuntu-24.04-64,openstack,executing,true,generic,1,2026-10-15T11:00:00Z\n",
                encoding="utf-8",
            )
            snapshot_path = model_dir / config.STATS_SNAPSHOT
            self.assertTrue(
                build_stats_snapshot(ts_dir, archive_dir, snapshot_path)
            )

            app = Flask(__name__)
            app.register_blueprint(stats_bp)
            with patch.object(config, "MODEL_DIR", str(model_dir)):
                client = app.test_client()
                from_response = client.get(
                    "/stats?scenario=generic&from=2026-10-15"
                )
                to_response = client.get(
                    "/stats?scenario=generic&to=2026-10-15"
                )
                day_response = client.get(
                    "/stats?scenario=generic&from=2026-10-15&to=2026-10-15"
                )

        self.assertEqual(from_response.get_json()["results"], {
            "pass": 1,
            "fail": 1,
            "aborted": 1,
            "total": 3,
            "matching_files": 1,
        })
        self.assertEqual(to_response.get_json()["results"], {
            "pass": 1,
            "fail": 1,
            "aborted": 1,
            "total": 3,
            "matching_files": 1,
        })
        self.assertEqual(day_response.get_json()["results"], {
            "pass": 0,
            "fail": 1,
            "aborted": 1,
            "total": 2,
            "matching_files": 1,
        })

    def test_standard_stats_include_aborted_results(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ts_dir = root / "ts"
            results_dir = root / "results"
            archive_dir = root / "archive"
            ts_dir.mkdir()
            results_dir.mkdir()
            archive_dir.mkdir()

            (ts_dir / "results_job_1_run_2_scenario_master_attempt_3.ts").write_text(
                "runid,job_id,backend,system,name,verb,scenario,attempt,instance,success,start\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,preparing,master,3,a,1,2026-10-14T10:00:00Z\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,preparing,master,3,b,0,2026-10-14T10:00:00Z\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/one,executing,master,3,c,1,2026-10-14T10:00:00Z\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/two,executing,master,3,a,0,2026-10-14T10:00:00Z\n"
                "batch-1,1,openstack,ubuntu-24.04-64,tests/main/two,restoring,master,3,b,0,2026-10-14T10:00:00Z\n"
                "batch-2,2,openstack,ubuntu-24.04-64,tests/main/two,preparing,master,3,c,0,2026-10-16T12:00:00Z\n",
                encoding="utf-8",
            )
            (archive_dir / "results_job_1_run_2_scenario_master_attempt_3.csv").write_text(
                "name,variant,system,backend,verb,aborted,scenario,attempt,start\n"
                "tests/main/three,,ubuntu-24.04-64,openstack,executing,true,master,3,2026-10-15T11:00:00Z\n",
                encoding="utf-8",
            )
            (results_dir / "results_job_3_run_4_scenario_master_attempt_3.json").write_text(
                '{"items":[{"name":"tests/main/pending","system":"ubuntu-24.04-64",'
                '"backend":"openstack","verb":"executing","aborted":true}]}',
                encoding="utf-8",
            )

            app = Flask(__name__)
            app.register_blueprint(stats_bp)
            with (
                patch.object(config, "TS_DIR", str(ts_dir)),
                patch.object(config, "RESULTS_DIR", str(results_dir)),
                patch.object(config, "ARCHIVE_DIR", str(archive_dir)),
            ):
                response = app.test_client().get(
                    "/stats?backend=openstack&scenario=master&attempt=3"
                )
                targets_response = app.test_client().get(
                    "/stats/all-targets?scenario=master"
                )
                from_response = app.test_client().get(
                    "/stats?scenario=master&from=2026-10-16"
                )
                to_response = app.test_client().get(
                    "/stats?scenario=master&to=2026-10-15"
                )
                invalid_time_response = app.test_client().get(
                    "/stats?scenario=master&from=2026-10-14T10:00:00Z"
                )
                reversed_time_response = app.test_client().get(
                    "/stats?scenario=master&from=2026-10-15&to=2026-10-14"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["results"], {
            "pass": 0,
            "fail": 3,
            "aborted": 1,
            "total": 4,
            "matching_files": 1,
        })
        self.assertEqual(targets_response.status_code, 200)
        self.assertEqual(targets_response.get_json()["targets"], [{
            "backend": "openstack",
            "system": "ubuntu-24.04-64",
            "pass": 0,
            "fail": 3,
            "aborted": 1,
            "total": 4,
        }])
        self.assertEqual(from_response.get_json()["results"], {
            "pass": 0,
            "fail": 1,
            "aborted": 0,
            "total": 1,
            "matching_files": 1,
        })
        self.assertEqual(to_response.get_json()["results"], {
            "pass": 0,
            "fail": 2,
            "aborted": 1,
            "total": 3,
            "matching_files": 1,
        })
        self.assertEqual(invalid_time_response.status_code, 400)
        self.assertEqual(reversed_time_response.status_code, 400)


if __name__ == "__main__":
    unittest.main()