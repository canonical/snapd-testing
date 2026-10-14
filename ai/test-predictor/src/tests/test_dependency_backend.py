import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from common.dependency import DependencyManager
from services.jobs import dependency


class TestDependencyBackend(unittest.TestCase):
    def test_load_all_data_filters_backend(self):
        with TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir, "results.ts")
            data_file.write_text(
                "system,backend,scenario,level,verb,runid,instance,name,success\n"
                "ubuntu-24.04-64,garden,generic,task,executing,1,vm-1,test-a,0\n"
                "ubuntu-24.04-64,openstack,generic,task,executing,2,vm-2,test-b,1\n",
                encoding="utf-8",
            )
            manager = DependencyManager.__new__(DependencyManager)
            manager.ts_dir = temp_dir

            with patch(
                "common.dependency.select_recent_pr_runs",
                side_effect=lambda files: files,
            ):
                result = manager.load_all_data(backend="garden", min_rows=0)

            self.assertEqual(result["backend"].tolist(), ["garden"])
            self.assertEqual(result["name"].tolist(), ["test-a"])

    def test_routes_forward_backend(self):
        manager = Mock()
        manager.get_pass_probabilities_given_fail.return_value = {"probabilities": []}
        manager.get_fail_probabilities_given_fail.return_value = {"probabilities": []}

        with patch.object(dependency, "_manager", manager):
            client = dependency.app.test_client()
            for endpoint in ("pass-given-fail", "fail-given-fail"):
                with self.subTest(endpoint=endpoint):
                    response = client.get(
                        f"/internal/dependencies/{endpoint}",
                        query_string={
                            "test": "test-a",
                            "system": "ubuntu-24.04-64",
                            "backend": "garden",
                            "scenario": "generic",
                        },
                    )
                    self.assertEqual(response.status_code, 200)

        manager.get_pass_probabilities_given_fail.assert_called_once_with(
            test_name="test-a",
            system="ubuntu-24.04-64",
            backend="garden",
            scenario="generic",
            use_cache=True,
            include_self=False,
        )
        manager.get_fail_probabilities_given_fail.assert_called_once_with(
            test_name="test-a",
            system="ubuntu-24.04-64",
            backend="garden",
            scenario="generic",
            use_cache=True,
            include_self=False,
        )


if __name__ == "__main__":
    unittest.main()
