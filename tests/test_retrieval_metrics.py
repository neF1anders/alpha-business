import math
import unittest

from evaluation.metrics import evaluate


class EvaluationTests(unittest.TestCase):
    def test_hand_calculated_metrics(self):
        labels = [
            {"query_id": "q", "doc_id": "a", "grade": 3},
            {"query_id": "q", "doc_id": "b", "grade": 1},
            {"query_id": "q", "doc_id": "c", "grade": 0},
        ]
        result = evaluate(labels, [{"query_id": "q", "ranking": ["c", "b", "a"]}])["macro"]
        self.assertEqual(result["precision_at_k"], 2 / 5)
        self.assertEqual(result["recall_at_k"], 1)
        self.assertEqual(result["reciprocal_rank_at_k"], 1 / 2)
        expected_ndcg = (1 / math.log2(3) + 7 / math.log2(4)) / (7 + 1 / math.log2(3))
        self.assertAlmostEqual(result["ndcg_at_k"], expected_ndcg)

    def test_business_usefulness_threshold_keeps_graded_ndcg(self):
        labels = [{"query_id":"q", "doc_id":d, "grade":g} for d,g in [("a",3),("b",1)]]
        run = [{"query_id":"q", "ranking":["b","a"]}]
        result = evaluate(labels,run,k=2,min_relevant_grade=2)
        self.assertEqual(result['macro']['precision_at_k'],0.5)
        self.assertEqual(result['macro']['recall_at_k'],1)
        self.assertEqual(result['macro']['reciprocal_rank_at_k'],0.5)
        self.assertEqual(result['macro']['ndcg_at_k'],evaluate(labels,run,k=2)['macro']['ndcg_at_k'])

    def test_only_weak_matches_are_no_useful_answer_at_threshold_two(self):
        labels = [{"query_id":"q", "doc_id":"a", "grade":1}]
        result = evaluate(labels,[{"query_id":"q", "ranking":["a"]}],min_relevant_grade=2)
        self.assertIsNone(result['macro']['ndcg_at_k'])
        self.assertEqual(result['macro']['correct_abstention_rate'],0)

    def test_grade_order_matters_for_ndcg(self):
        labels = [{"query_id": "q", "doc_id": d, "grade": g} for d, g in [("a", 3), ("b", 1)]]
        good = evaluate(labels, [{"query_id": "q", "ranking": ["a", "b"]}])
        bad = evaluate(labels, [{"query_id": "q", "ranking": ["b", "a"]}])
        self.assertEqual(good["macro"]["ndcg_at_k"], 1)
        self.assertEqual(good["macro"]["precision_at_k"], bad["macro"]["precision_at_k"])
        self.assertGreater(good["macro"]["ndcg_at_k"], bad["macro"]["ndcg_at_k"])

    def test_missing_run_counts_as_zero(self):
        result = evaluate([{"query_id": "q", "doc_id": "a", "grade": 1}], [])
        self.assertEqual(result["macro"]["recall_at_k"], 0)
        self.assertEqual(result["missing_run_count"], 1)
        self.assertIsNone(result["latency_p95_ms"])

    def test_abstention_separate_and_missing_is_not_abstention(self):
        labels = [{"query_id": q, "doc_id": "x", "grade": 0} for q in ["empty", "bad", "missing"]]
        result = evaluate(labels, [
            {"query_id": "empty", "ranking": []},
            {"query_id": "bad", "ranking": ["x"]},
        ])
        self.assertEqual(result["macro"]["correct_abstention_rate"], 1 / 3)
        self.assertIsNone(result["macro"]["ndcg_at_k"])
        self.assertEqual(result["ranking_query_count"], 0)

    def test_unknown_labels_reported_or_rejected(self):
        labels = [{"query_id": "q", "doc_id": "a", "grade": 1}]
        run = [{"query_id": "q", "ranking": ["a", "unknown"]}]
        self.assertEqual(evaluate(labels, run)["unjudged_fraction_at_k"], 0.5)
        with self.assertRaisesRegex(ValueError, "unjudged"):
            evaluate(labels, run, unjudged_policy="error")

    def test_duplicates_and_unknown_queries_rejected(self):
        labels = [{"query_id": "q", "doc_id": "a", "grade": 1}]
        for run in [
            [{"query_id": "q", "ranking": ["a", "a"]}],
            [{"query_id": "q", "ranking": []}, {"query_id": "q", "ranking": []}],
            [{"query_id": "other", "ranking": []}],
        ]:
            with self.subTest(run=run), self.assertRaises(ValueError):
                evaluate(labels, run)
        with self.assertRaisesRegex(ValueError, "duplicate judgment"):
            evaluate(labels + labels, [])

    def test_metrics_use_cutoff(self):
        labels = [{"query_id": "q", "doc_id": "a", "grade": 1}]
        result = evaluate(labels, [{"query_id": "q", "ranking": ["x", "y", "a"]}], k=2)
        self.assertEqual(result["macro"]["recall_at_k"], 0)

    def test_invalid_values_rejected(self):
        valid = [{"query_id": "q", "doc_id": "a", "grade": 1}]
        for grade in [True, -1, 4, 1.5]:
            with self.subTest(grade=grade), self.assertRaises(ValueError):
                evaluate([{**valid[0], "grade": grade}], [])
        for latency in [True, -1, float("nan"), float("inf")]:
            with self.subTest(latency=latency), self.assertRaises(ValueError):
                evaluate(valid, [{"query_id": "q", "ranking": [], "latency_ms": latency}])
        for k in [0, -1, True, 1.5]:
            with self.subTest(k=k), self.assertRaises(ValueError):
                evaluate(valid, [], k=k)

    def test_latency_is_measured_samples_only(self):
        labels = [{"query_id": q, "doc_id": "a", "grade": 1} for q in ["a", "b", "c"]]
        runs = [
            {"query_id": "a", "ranking": ["a"], "latency_ms": 10},
            {"query_id": "b", "ranking": ["a"], "latency_ms": 30},
        ]
        result = evaluate(labels, runs)
        self.assertEqual(result["latency_sample_count"], 2)
        self.assertEqual(result["latency_p50_ms"], 10)
        self.assertEqual(result["latency_p95_ms"], 30)
        self.assertAlmostEqual(result["macro"]["recall_at_k"], 2 / 3)


if __name__ == "__main__":
    unittest.main()
