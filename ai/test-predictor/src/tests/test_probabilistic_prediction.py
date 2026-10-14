import unittest
from itertools import product

from services.jobs.predictor import (
    _demote_duplicate_prs,
    _weighted_history_probability,
    probabilistic_prediction,
)


def prob_for(pattern):
    return probabilistic_prediction([{"success": value} for value in pattern])


class TestHistoryPartitioning(unittest.TestCase):
    def test_older_duplicate_pr_is_moved_out_of_newest_partition(self):
        prs = [1, None, 3, 4, 5, 6, 4, 3, None, 2, 2, 1]
        history = [{"pr": pr, "success": 1} for pr in prs]

        reordered = _demote_duplicate_prs(history, (3, 3, 4))

        self.assertEqual(
            [item["pr"] for item in reordered],
            [1, None, 3, 4, 5, 6, 4, 3, 2, None, 2, 1],
        )
        for start, end in ((9, 12), (6, 9), (2, 6)):
            partition_prs = [
                item["pr"]
                for item in reordered[start:end]
                if item["pr"] is not None
            ]
            self.assertEqual(len(partition_prs), len(set(partition_prs)))

    def test_missing_pr_values_are_not_treated_as_the_same_change(self):
        history = [
            {"pr": None, "success": 0},
            {"pr": None, "success": 1},
            {"pr": None, "success": 1},
        ]

        self.assertEqual(_demote_duplicate_prs(history, (3, 3, 4)), history)

    def test_duplicate_pr_with_different_results_keeps_original_positions(self):
        prs = [1, None, 3, 4, 5, 6, 4, 3, None, 2, 2, 1]
        successes = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1]
        history = [
            {"pr": pr, "success": success}
            for pr, success in zip(prs, successes)
        ]

        reordered = _demote_duplicate_prs(history, (3, 3, 4))

        self.assertEqual(reordered, history)

    def test_duplicate_pr_with_two_failures_is_moved_to_separate_partitions(self):
        prs = [1, None, 3, 4, 5, 6, 4, 3, None, 2, 2, 1]
        history = [
            {"pr": pr, "success": 0 if pr == 2 else 1}
            for pr in prs
        ]

        reordered = _demote_duplicate_prs(history, (3, 3, 4))

        self.assertEqual(
            [item["pr"] for item in reordered],
            [1, None, 3, 4, 5, 6, 4, 3, 2, None, 2, 1],
        )

    def test_three_results_from_same_pr_are_moved_to_separate_partitions(self):
        prs = [1, 9, 3, 4, 5, 6, 7, 8, 2, 2, 2, 10]
        history = [{"pr": pr, "success": 1} for pr in prs]

        reordered = _demote_duplicate_prs(history, (3, 3, 4))

        self.assertEqual(
            [
                sum(item["pr"] == 2 for item in reordered[start:end])
                for start, end in ((9, 12), (6, 9), (2, 6))
            ],
            [1, 1, 1],
        )
        self.assertEqual([item["pr"] for item in history], prs)

    def test_three_results_with_different_endpoints_keep_original_positions(self):
        prs = [1, 9, 3, 4, 5, 6, 7, 8, 2, 2, 2, 10]
        successes = [1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1]
        history = [
            {"pr": pr, "success": success}
            for pr, success in zip(prs, successes)
        ]

        self.assertEqual(_demote_duplicate_prs(history, (3, 3, 4)), history)


class TestWeightedHistory(unittest.TestCase):
    def test_probability_uses_configured_partition_weights(self):
        history = (
            [{"success": 1}] * 2
            + [{"success": 0}] * 4
            + [{"success": 1}] * 3
            + [{"success": 0}] * 3
        )

        probability = _weighted_history_probability(
            history,
            part_sizes=(3, 3, 4),
            part_weights=(0.50, 0.25, 0.15, 0.10),
        )

        self.assertAlmostEqual(probability, 0.35)

    def test_partial_history_normalizes_active_partition_weights(self):
        history = [{"success": 1}, {"success": 0}]

        probability = _weighted_history_probability(
            history,
            part_sizes=(3, 3, 4),
            part_weights=(0.50, 0.25, 0.15, 0.10),
        )

        self.assertAlmostEqual(probability, 0.5)

    def test_invalid_partition_configuration_is_rejected(self):
        history = [{"success": 1}]

        with self.assertRaises(ValueError):
            _weighted_history_probability(history, (3, 3, 4), (0.50, 0.25))
        with self.assertRaises(ValueError):
            _weighted_history_probability(history, (3, 0, 4), (0.50, 0.25, 0.15, 0.10))
        with self.assertRaises(ValueError):
            _weighted_history_probability(history, (3, 3, 4), (0.50, 0.25, 0.15, -0.10))


class TestProbabilisticPrediction(unittest.TestCase):
    def test_no_history_is_neutral(self):
        self.assertEqual(prob_for([]), 0.5)

    def test_stable_histories_reach_probability_extremes(self):
        self.assertEqual(prob_for([1] * 25), 1.0)
        self.assertEqual(prob_for([0] * 25), 0.0)

    def test_newer_results_have_more_influence(self):
        newest_pass = prob_for([0] * 11 + [1])
        oldest_pass = prob_for([1] + [0] * 11)

        self.assertGreater(newest_pass, oldest_pass)

    def test_prediction_matches_direct_partition_sum(self):
        pattern = [1] * 2 + [0] * 4 + [1] * 3 + [0] * 3

        self.assertAlmostEqual(prob_for(pattern), 0.35)

    def test_probabilities_are_always_bounded(self):
        for pattern in product([0, 1], repeat=12):
            probability = prob_for(pattern)
            self.assertGreaterEqual(probability, 0.0)
            self.assertLessEqual(probability, 1.0)


if __name__ == "__main__":
    unittest.main()