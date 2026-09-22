import unittest

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


if __name__ == "__main__":
    unittest.main()