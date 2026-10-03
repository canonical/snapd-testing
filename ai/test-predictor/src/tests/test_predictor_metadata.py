import unittest

from services.jobs import predictor


class TestPredictorMetadata(unittest.TestCase):
    def test_lists_backends_systems_and_scenarios(self):
        class SystemStateCache:
            def __init__(self, **_kwargs):
                self.cache = {
                    "ubuntu-24.04-64": {
                        "tests/main/example": {
                            "executing": [{
                                "backend": "openstack",
                                "scenario": "generic",
                            }]
                        }
                    },
                    "ubuntu-26.04-64": {
                        "tests/main/other": {
                            "restoring": [{
                                "backend": "google",
                                "scenario": "master",
                            }]
                        }
                    },
                }

            def initialize(self):
                pass

        predictor.app.state_cache = SystemStateCache()

        classes = {
            "backend": ["google", "openstack"],
            "system": ["ubuntu-24.04-64", "ubuntu-26.04-64"],
            "scenario": ["generic", "master"],
        }
        client = predictor.app.test_client()
        for category, cache_key in (
            ("backends", "backend"),
            ("systems", "system"),
            ("scenarios", "scenario"),
        ):
            with self.subTest(category=category):
                response = client.get(f"/internal/list/{category}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["values"], classes[cache_key])


if __name__ == "__main__":
    unittest.main()