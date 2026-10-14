import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


def load_stats_client():
    path = Path(__file__).parents[1] / "client" / "stats"
    loader = importlib.machinery.SourceFileLoader("stats_client", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


stats_client = load_stats_client()


def args(**overrides):
    values = {
        "url": "http://example.test",
        "port": 5000,
        "name": None,
        "backend": None,
        "attempt": None,
        "scenario": "generic",
        "verb": None,
        "from_time": None,
        "to_time": None,
        "history": False,
        "system": None,
        "limit": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestStatsClient(unittest.TestCase):
    def test_omits_undefined_time_bounds(self):
        url, params = stats_client.build_request(args())

        self.assertEqual(url, "http://example.test:5000/stats")
        self.assertNotIn("from", params)
        self.assertNotIn("to", params)

    def test_forwards_defined_time_bounds(self):
        url, params = stats_client.build_request(args(
            from_time="2026-10-01",
            to_time="2026-10-14",
        ))

        self.assertEqual(url, "http://example.test:5000/stats")
        self.assertEqual(params["from"], "2026-10-01")
        self.assertEqual(params["to"], "2026-10-14")


if __name__ == "__main__":
    unittest.main()