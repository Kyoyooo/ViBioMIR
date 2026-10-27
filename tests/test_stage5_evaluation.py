"""Golden local scoring, exact LCS and query-macro evaluation tests."""
import copy
from itertools import product
import math
import unittest

from vibiomir.retrieval_evaluation import lcs, lcs_reference, normalize, paired_bootstrap, prediction_map, score
from vibiomir.reference_bm25 import ANALYZER, BM25, analyze, select_prediction


def question(query_id=1, **overrides):
    return {"id": query_id, "query": "triệu chứng", "split": "dev", "case_group": f"case{query_id}",
            "relevant_docs": [10], "judged_docs": [10, 20, 30], "gold_languages": ["vi"],
            "relevant_chunks": [{"doc_id": 10, "chunk_text": "a b c d e"}], **overrides}


class EvaluationTest(unittest.TestCase):
    def test_bitset_lcs_matches_reference_exhaustively_with_repetitions(self):
        sequences = [sequence for n in range(5) for sequence in product(("a", "b"), repeat=n)]
        for left in sequences:
            for right in sequences:
                self.assertEqual(lcs(left, right), lcs_reference(left, right))

    def test_normalization_order_and_40_percent_subsequence_boundary(self):
        self.assertEqual(normalize(" Ａ&amp;Ｂ\u00a0 C "), "a&b c")
        gold = {"queries": [question()]}
        prediction = [{"id": 1, "relevant_docs": [10], "relevant_chunks": [{"doc_id": 10, "chunk_text": "a unrelated c"}]}]
        self.assertEqual(score(gold, prediction)["macro"]["chunk"]["f2"], 1)
        prediction[0]["relevant_chunks"][0]["chunk_text"] = "a unrelated"
        self.assertEqual(score(gold, prediction)["macro"]["chunk"]["f2"], 0)

    def test_many_to_many_counts_each_reference_once_and_deduplicates_predictions(self):
        q = question(relevant_chunks=[{"doc_id": 10, "chunk_text": "a b c d e"}, {"doc_id": 10, "chunk_text": "a b c x y"}])
        prediction = [{"id": 1, "relevant_docs": [10, 10], "relevant_chunks": [{"doc_id": 10, "chunk_text": "a b"}] * 3}]
        report = score({"queries": [q]}, prediction)
        self.assertEqual(report["per_query"][0]["predicted_chunks"], 1)
        self.assertEqual(report["macro"]["chunk"], {"precision": 1, "recall": 1, "f2": 1})
        prediction[0]["relevant_docs"] = [20]
        prediction[0]["relevant_chunks"] = [{"doc_id": 20, "chunk_text": "a b c d e"}]
        self.assertEqual(score({"queries": [q]}, prediction)["macro"]["chunk"]["recall"], 0)

    def test_macro_f2_is_average_of_query_f2_and_zero_cases_are_explicit(self):
        gold = {"queries": [question(), question(2, relevant_docs=[10, 20], relevant_chunks=[])]}
        predictions = [{"id": 1, "relevant_docs": [10, 20], "relevant_chunks": []},
                       {"id": 2, "relevant_docs": [10], "relevant_chunks": []}]
        report = score(gold, predictions)
        self.assertAlmostEqual(report["macro"]["document"]["f2"], (5 / 6 + 5 / 9) / 2)
        self.assertNotAlmostEqual(report["macro"]["document"]["f2"], 0.75)
        self.assertEqual(report["goldless_chunk_queries"], 1)
        self.assertEqual(report["macro"]["chunk"]["f2"], 0)
        self.assertEqual(report["slices"]["gold_languages"]["vi"]["queries"], 2)

    def test_proxy_profiles_and_holdout_unjudged_checks(self):
        gold = {"queries": [question(relevant_chunks=[{"doc_id": 10, "chunk_text": "肾结石治疗"}])]}
        predictions = [{"id": 1, "relevant_docs": [10], "relevant_chunks": [{"doc_id": 10, "chunk_text": "肾结石"}]}]
        self.assertEqual(score(gold, predictions)["macro"]["chunk"]["f2"], 0)
        self.assertEqual(score(gold, predictions, profile="cjk_character_sensitivity_v1")["macro"]["chunk"]["f2"], 1)
        gold["queries"][0]["split"] = "holdout"
        predictions[0]["relevant_docs"].append(99)
        with self.assertRaisesRegex(ValueError, "Unjudged holdout"):
            score(gold, predictions, split="holdout")

    def test_missing_queries_bad_order_and_empty_gold_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "every selected"):
            score({"queries": [question()]}, [])
        with self.assertRaisesRegex(ValueError, "chunk_order"):
            prediction_map([{"id": 1, "relevant_docs": [10], "relevant_chunks": [{"doc_id": 10, "chunk_text": "a", "chunk_order": -1}]}])
        with self.assertRaisesRegex(ValueError, "Gold evidence"):
            score({"queries": [question(relevant_chunks=[{"doc_id": 10, "chunk_text": " "}])]}, [{"id": 1, "relevant_docs": [], "relevant_chunks": []}])

    def test_paired_bootstrap_is_reproducible(self):
        gold = {"queries": [question()]}
        a = score(gold, [{"id": 1, "relevant_docs": [], "relevant_chunks": []}])
        b = score(gold, [{"id": 1, "relevant_docs": [10], "relevant_chunks": []}])
        result = paired_bootstrap(a, b, repetitions=40, seed=3)
        self.assertEqual(result, paired_bootstrap(a, b, repetitions=40, seed=3))
        self.assertEqual(result["right_minus_left"]["document"]["bootstrap_95"], [1, 1])

    def test_reference_bm25_hand_calculation_and_han_analysis(self):
        chunks = [{"doc_id": 10, "chunk_id": "a", "retrieval_text": "fever fever", "chunk_text": "fever fever"},
                  {"doc_id": 20, "chunk_id": "b", "retrieval_text": "fever pain", "chunk_text": "fever pain"}]
        index = BM25(chunks)
        ranked = index.search("fever")
        self.assertEqual(ranked[0][0], 0)
        self.assertAlmostEqual(ranked[0][1], math.log(1.2) * 2 * 2.2 / (2 + 1.2))
        self.assertEqual(index.search("absent"), [])
        self.assertIn("zh2:肾结", analyze("肾结石"))
        prediction = select_prediction(1, ranked, chunks, doc_cap=1, chunk_cap=1)
        self.assertEqual(prediction["relevant_docs"], [10])
        self.assertEqual(prediction["relevant_chunks"][0]["chunk_text"], chunks[0]["chunk_text"])


if __name__ == "__main__":
    unittest.main()
