import unittest
from itertools import product
from statistics import mean

from services.jobs.predictor import adjust_for_flaky_pattern


def prob_for(pattern, base_probability=0.0):
    history = [{"success": v} for v in pattern]
    return adjust_for_flaky_pattern(history, base_probability)


def assert_in_range(testcase, value, lower, upper, label):
    testcase.assertGreaterEqual(value, lower, f"{label} below expected range: {value:.4f} < {lower:.4f}")
    testcase.assertLessEqual(value, upper, f"{label} above expected range: {value:.4f} > {upper:.4f}")


def generate_scenarios(prefixes, tail_size):
    """Generate fixed-length scenario patterns by combining prefixes and binary tails."""
    scenarios = []
    for prefix in prefixes:
        for tail in product([0, 1], repeat=tail_size):
            scenarios.append((prefix + list(tail))[-14:])
    return scenarios


class TestPredictionCalibration(unittest.TestCase):
    def test_flaky_corner_ranges(self):
        """
        Cover major corner paths in the prediction calibration pipeline and
        validate each lands in an expected probability band.
        """
        cases = {
            # Entry conditions
            "no_history_baseline": ([], 0.00, 0.93, 0.93),
            "no_history_passthrough_high_base": ([], 0.95, 0.95, 0.95),
            "short_history_passthrough": ([1, 0, 1, 0, 1], 0.37, 0.37, 0.37),

            # Strong trend early returns
            "all_pass_clamp": ([1] * 14, 0.00, 0.97, 1.00),
            "all_fail_clamp": ([0] * 14, 1.00, 0.00, 0.03),
            "low_ones_tail_zero_early_return": ([0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0], 0.50, 0.00, 0.03),

            # Boundary flip rules
            "boundary_pass_to_fail": ([1] * 13 + [0], 0.00, 0.56, 0.66),
            "boundary_fail_to_pass": ([0] * 13 + [1], 0.00, 0.30, 0.40),

            # Mostly-pass rules
            "mostly_pass_positive_tail_floor": ([1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1], 0.00, 0.70, 0.82),
            "single_fresh_fail_floor": ([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0], 0.00, 0.56, 0.66),
            "brief_dip_then_pass_recovery": ([1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1], 0.00, 0.88, 0.92),
            "stale_single_fail_long_pass_tail": ([0] + [1] * 13, 0.00, 0.89, 0.91),

            # Flaky and mixed-history guards
            "flaky_alternating_centered": ([0, 1] * 7, 0.00, 0.45, 0.55),
            "flaky_recovery_cap": ([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 1], 0.00, 0.80, 0.90),
            "mixed_extreme_guard_blend": ([1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], 0.00, 0.22, 0.38),

            # Tail deterioration cap behavior
            "tail_deterioration_cap_high_ones": ([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0], 0.00, 0.30, 0.50),
            "tail_deterioration_cap_low_ones": ([0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0], 0.00, 0.00, 0.10),
        }

        for label, (pattern, base_prob, lower, upper) in cases.items():
            p = prob_for(pattern, base_probability=base_prob)
            assert_in_range(self, p, lower, upper, label)

    def test_core_scenario_ranges(self):
        # Regression anchors for key edge cases discussed during tuning.
        scenarios = {
            "all_fail_then_pass": ([0] * 13 + [1], 0.30, 0.50),
            "all_fail_then_two_passes": ([0] * 12 + [1, 1], 0.70, 0.90),
            "all_pass_then_fail": ([1] * 13 + [0], 0.50, 0.70),
            "all_pass_then_two_fails": ([1] * 12 + [0, 0], 0.20, 0.50),
        }

        for label, (pattern, lower, upper) in scenarios.items():
            p = prob_for(pattern)
            assert_in_range(self, p, lower, upper, label)

    def test_probabilities_are_bounded(self):
        """ 
        Ensure that all output probabilities are valid (between 0 and 1) across a wide
        range of scenarios, including edge cases. This guards against any unexpected
        behavior in the adjustment logic that could produce invalid probabilities.
        """
        # Large combination sweep to ensure output is always a valid probability.
        prefixes = [
            [1] * 10,
            [0] * 10,
            [1, 0] * 5,
            [1, 1, 0, 1, 0, 1, 0, 1, 0, 1],
            [0, 0, 1, 1, 0, 1, 1, 0, 1, 0],
            [1, 1, 1, 0, 0, 1, 0, 0, 1, 1],
        ]
        scenarios = generate_scenarios(prefixes, tail_size=7)

        self.assertGreaterEqual(len(scenarios), 100)

        for pattern in scenarios:
            p = prob_for(pattern)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)

    def test_recovery_is_monotonic_for_long_fail_run(self):
        """
        In a long run of failures, a single pass should increase probability, and two
        passes should increase it further.
        """
        base = [0] * 11
        p1 = prob_for(base + [0, 0, 1])
        p2 = prob_for(base + [0, 1, 1])
        p3 = prob_for(base + [1, 1, 1])

        self.assertLessEqual(p1, p2)
        self.assertLessEqual(p2, p3)

    def test_deterioration_is_monotonic_for_long_pass_run(self):
        """
        In a long run of passes, a single failure should decrease probability, and two
        failures should decrease it further.
        """
        base = [1] * 11
        p1 = prob_for(base + [1, 1, 0])
        p2 = prob_for(base + [1, 0, 0])
        p3 = prob_for(base + [0, 0, 0])

        self.assertGreaterEqual(p1, p2)
        self.assertGreaterEqual(p2, p3)

    def test_bulk_tail_trend_properties(self):
        """
        Large deterministic sweep and aggregate trend checks.
        """
        prefixes = [
            [1] * 10,
            [0] * 10,
            [1, 0] * 5,
            [1, 1, 0, 1, 0, 1, 0, 1, 0, 1],
            [0, 0, 1, 1, 0, 1, 1, 0, 1, 0],
            [1, 1, 1, 0, 0, 1, 0, 0, 1, 1],
        ]
        scenarios = generate_scenarios(prefixes, tail_size=7)
        self.assertGreaterEqual(len(scenarios), 100)

        scored = [(pattern, prob_for(pattern)) for pattern in scenarios]

        def avg_for(predicate):
            vals = [p for pattern, p in scored if predicate(pattern)]
            self.assertGreater(len(vals), 20)
            return mean(vals)

        # Recent strong-pass tails should average higher than strong-fail tails.
        mean_tail_11 = avg_for(lambda pat: pat[-2:] == [1, 1])
        mean_tail_00 = avg_for(lambda pat: pat[-2:] == [0, 0])
        self.assertGreater(mean_tail_11, mean_tail_00)

        mean_tail_111 = avg_for(lambda pat: pat[-3:] == [1, 1, 1])
        mean_tail_000 = avg_for(lambda pat: pat[-3:] == [0, 0, 0])
        self.assertGreater(mean_tail_111, mean_tail_000)

        # Recovery-shaped tails should be less risky than deterioration-shaped tails.
        mean_recovery = avg_for(lambda pat: pat[-3:] == [0, 1, 1])
        mean_deterioration = avg_for(lambda pat: pat[-3:] == [1, 0, 0])
        self.assertGreater(mean_recovery, mean_deterioration)


if __name__ == "__main__":
    unittest.main()
