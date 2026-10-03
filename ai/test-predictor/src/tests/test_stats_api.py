import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.api.stats import count_filtered_success


class TestStatsApi(unittest.TestCase):
    def test_counts_results_for_selected_backend_and_system(self):
        with TemporaryDirectory() as temp_dir:
            data = Path(temp_dir) / "results.ts"
            data.write_text(
                "backend,system,name,success\n"
                "openstack,ubuntu-24.04-64,tests/main/one,1\n"
                "openstack,ubuntu-24.04-64,tests/main/two,0\n"
                "openstack-ext,ubuntu-24.04-64,tests/main/three,1\n",
                encoding="utf-8",
            )

            passed, failed, matching_files = count_filtered_success(
                temp_dir,
                {"backend": "openstack", "system": "ubuntu-24.04-64"},
            )

        self.assertEqual((passed, failed, matching_files), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()