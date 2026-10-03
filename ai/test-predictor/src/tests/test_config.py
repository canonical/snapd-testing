import unittest

from common import config


class TestConfigDefaults(unittest.TestCase):
    def test_prediction_model_defaults_to_probabilistic(self):
        self.assertEqual(config.PREDICTION_MODEL, config.PREDICTION_MODEL_PROBABILISTIC)

    def test_cache_refresh_interval_defaults_to_two_hours(self):
        self.assertEqual(config.CACHE_REFRESH_INTERVAL_HOURS, 2)


if __name__ == "__main__":
    unittest.main()