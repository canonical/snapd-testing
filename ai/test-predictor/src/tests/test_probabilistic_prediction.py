import unittest
from itertools import product

from common import config
from services.jobs.predictor import probabilistic_prediction


def prob_for(pattern):
    history = [{"success": v} for v in pattern]
    return probabilistic_prediction(history)


class TestProbabilisticPrediction(unittest.TestCase):
    """Validates the model-free (model="probabilistic") prediction path."""

    def test_no_history_uses_optimistic_baseline(self):
        self.assertGreaterEqual(prob_for([]), 0.93)

    def test_short_history_returns_raw_success_ratio(self):
        # Fewer than 6 samples: flaky-pattern rules are skipped, so the
        # probability should be the plain historical success ratio.
        cases = {
            "all_pass": ([1, 1, 1], 1.0),
            "all_fail": ([0, 0, 0], 0.0),
            "mixed": ([1, 0, 1, 0, 1], 0.6),
        }
        for label, (pattern, expected) in cases.items():
            with self.subTest(label=label):
                self.assertAlmostEqual(prob_for(pattern), expected, places=6)

    def test_stable_pass_history_is_high_confidence(self):
        self.assertGreaterEqual(prob_for([1] * 14), 0.99)

    def test_stable_fail_history_is_low_confidence(self):
        self.assertLessEqual(prob_for([0] * 14), 0.01)

    def test_death_spiral_is_flagged_as_high_risk(self):
        pattern = [1, 1] + [0] * 12
        self.assertLessEqual(prob_for(pattern), 0.05)

    def test_perfectly_flaky_history_lands_mid_band(self):
        pattern = [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
        self.assertGreaterEqual(prob_for(pattern), 0.45)
        self.assertLessEqual(prob_for(pattern), 0.55)

    def test_recovery_after_failures_is_reasonably_confident(self):
        pattern = [1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1]
        self.assertGreaterEqual(prob_for(pattern), 0.75)

    def test_deterioration_lowers_probability_below_stable_pass(self):
        stable = prob_for([1] * 14)
        deteriorating = prob_for([1] * 10 + [0] * 4)
        self.assertLess(deteriorating, stable)

    def test_improving_trend_raises_probability_above_stable_fail(self):
        stable = prob_for([0] * 14)
        improving = prob_for([0] * 6 + [1] * 8)
        self.assertGreater(improving, stable)

    def test_probabilities_are_always_bounded(self):
        for tail in product([0, 1], repeat=6):
            pattern = [1, 1, 0, 1, 0, 1, 0, 1] + list(tail)
            prob = prob_for(pattern)
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)


class TestProbabilisticPredictionLargeHistory(unittest.TestCase):
    """Same checks at CACHE_HISTORY_SIZE scale, since the cache can now retain
    much longer histories than the model's SEQUENCE_LENGTH window."""

    N = config.CACHE_HISTORY_SIZE

    def test_large_stable_pass_history_is_high_confidence(self):
        self.assertGreaterEqual(prob_for([1] * self.N), 0.99)

    def test_large_stable_fail_history_is_low_confidence(self):
        self.assertLessEqual(prob_for([0] * self.N), 0.01)

    def test_large_flaky_alternating_history_lands_mid_band(self):
        pattern = [0, 1] * (self.N // 2)
        prob = prob_for(pattern)
        self.assertGreaterEqual(prob, 0.45)
        self.assertLessEqual(prob, 0.55)

    def test_flaky_tails_from_two_to_ten_entries_land_mid_band(self):
        prefix = [1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0]

        for tail_length in range(2, 11):
            for first_value in (0, 1):
                tail = [(first_value + offset) % 2 for offset in range(tail_length)]
                pattern = (prefix + tail)[-self.N:]

                with self.subTest(tail_length=tail_length, first_value=first_value):
                    probability = prob_for(pattern)
                    self.assertGreaterEqual(probability, 0.40)
                    self.assertLessEqual(probability, 0.60)

    def test_flaky_tails_with_adjacent_pairs_land_mid_band(self):
        prefix = [1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0]

        for tail_length in range(3, 11):
            variants = {
                "double_fail": [0, 0] + [(offset + 1) % 2 for offset in range(tail_length - 2)],
                "double_pass": [1, 1] + [offset % 2 for offset in range(tail_length - 2)],
            }
            for label, tail in variants.items():
                pattern = (prefix + tail)[-self.N:]

                with self.subTest(tail_length=tail_length, variant=label):
                    probability = prob_for(pattern)
                    self.assertGreaterEqual(probability, 0.40)
                    self.assertLessEqual(probability, 0.60)

    def test_two_identical_recent_entries_are_short_trends(self):
        prefix = [1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 1, 0]

        self.assertLess(prob_for(prefix + [0, 0]), 0.40)
        self.assertGreater(prob_for(prefix + [1, 1]), 0.60)

    def test_large_isolated_recent_failure_does_not_spook_long_pass_history(self):
        pattern = [1] * (self.N - 1) + [0]
        self.assertGreaterEqual(prob_for(pattern), 0.95)

    def test_large_isolated_recent_pass_lifts_probability_above_ratio(self):
        pattern = [0] * (self.N - 1) + [1]
        ratio = sum(pattern) / len(pattern)
        self.assertGreater(prob_for(pattern), ratio)

    def test_large_recent_deterioration_lowers_probability_below_ratio(self):
        pattern = [1] * (self.N - 2) + [0, 0]
        ratio = sum(pattern) / len(pattern)
        self.assertLess(prob_for(pattern), ratio)

    def test_extended_recent_deterioration_is_high_risk(self):
        pattern = [1] * 16 + [0] * 9
        self.assertLessEqual(prob_for(pattern), 0.05)

    def test_mixed_histories_lose_confidence_with_sustained_fail_tail(self):
        cases = {
            "three_fail_deterioration": (
                [1] * 11 + [0] * 3,
                0.20,
            ),
            "six_fail_deterioration": (
                [1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 0] + [0] * 6,
                0.11,
            ),
            "eight_fail_deterioration": (
                [0, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1] + [0] * 8,
                0.05,
            ),
            "mixed_nine_fail_deterioration": (
                [1, 1, 1, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 0, 1, 1] + [0] * 9,
                0.03,
            ),
        }

        for label, (pattern, maximum) in cases.items():
            with self.subTest(label=label):
                self.assertLessEqual(prob_for(pattern), maximum + 1e-9)

    def test_large_recent_recovery_raises_probability_above_ratio(self):
        pattern = [0] * (self.N - 2) + [1, 1]
        ratio = sum(pattern) / len(pattern)
        self.assertGreater(prob_for(pattern), ratio)

    def test_extended_recent_recovery_is_high_confidence(self):
        pattern = [0] * 16 + [1] * 9
        self.assertGreaterEqual(prob_for(pattern), 0.95)

    def test_mixed_histories_gain_confidence_with_sustained_pass_tail(self):
        cases = {
            "three_pass_recovery": (
                [0] * 11 + [1] * 3,
                0.80,
            ),
            "six_pass_recovery": (
                [0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 1] + [1] * 6,
                0.89,
            ),
            "eight_pass_recovery": (
                [1, 0, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0] + [1] * 8,
                0.95,
            ),
            "reported_nine_pass_recovery": (
                [0, 0, 0, 0, 1, 1, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0] + [1] * 9,
                0.97,
            ),
        }

        for label, (pattern, minimum) in cases.items():
            with self.subTest(label=label):
                self.assertGreaterEqual(prob_for(pattern), minimum)

    def test_large_extended_fail_tail_is_riskier_than_overall_ratio(self):
        pattern = [1] * 30 + [0] * 20
        ratio = sum(pattern) / len(pattern)
        self.assertLess(prob_for(pattern), ratio)

    def test_large_history_probabilities_are_always_bounded(self):
        prefix = [1, 1, 0, 1, 0, 1, 0, 1] * 5  # 40 stable-ish entries
        for tail in product([0, 1], repeat=6):
            pattern = prefix + list(tail)
            prob = prob_for(pattern)
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)


if __name__ == "__main__":
    unittest.main()
