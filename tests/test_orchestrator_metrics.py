import unittest

from orchestrator.domain.metrics import dcg, ndcg_at_k, noise_rate, precision_at_k


class MetricsTests(unittest.TestCase):
    def test_ideal_order_gives_ndcg_one_and_reverse_is_lower(self):
        grades = [2, 1, 0, 0]
        self.assertAlmostEqual(ndcg_at_k([2, 1], grades, 3), 1.0)
        self.assertLess(ndcg_at_k([1, 2], grades, 3), 1.0)

    def test_missing_relevant_news_lowers_ndcg(self):
        self.assertLess(ndcg_at_k([1], [2, 1], 3), ndcg_at_k([2], [2, 1], 3))

    def test_no_relevant_candidates_gives_zero(self):
        self.assertEqual(ndcg_at_k([0], [0, 0], 3), 0.0)

    def test_precision_counts_empty_slots_as_misses(self):
        self.assertAlmostEqual(precision_at_k([2, 0], 3), 1 / 3)
        self.assertAlmostEqual(precision_at_k([2, 1, 2], 3, min_grade=1), 1.0)

    def test_noise_rate(self):
        self.assertAlmostEqual(noise_rate([0, 2, 1, 0]), 0.5)
        self.assertEqual(noise_rate([]), 0.0)

    def test_dcg_discounts_by_position(self):
        self.assertGreater(dcg([2, 0], 2), dcg([0, 2], 2))


if __name__ == "__main__":
    unittest.main()
