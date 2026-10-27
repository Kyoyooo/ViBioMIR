"""Human labels, exact gold source spans, leakage and frozen holdout contracts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from vibiomir.io import canonical_json, file_hash, text_hash, write_json
from vibiomir.local_benchmark import JUDGMENT_FIELDS, SPAN_FIELDS, compile_benchmark, freeze_dev, locate_span, pool_candidates
from vibiomir.review_common import write_csv
from vibiomir.retrieval_evaluation import compare_runs, dev_signature


class BenchmarkToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.packet = {"doc_id": 10, "derived_version_id": "clean", "regions": [
            {"region_id": "r", "text": "Không sốt; liều 5 mg. 肾结石。", "source_start": 9}]}
        write_json(self.evidence / "10.json", self.packet)
        write_json(self.evidence / "manifest.json", {"state": "complete", "identity": {"input": {"signature": "corpus"}},
            "files": {"10": file_hash(self.evidence / "10.json")}})
        self.questions = [{"id": 1, "query": "Người bệnh có sốt không?", "split": "dev", "case_group": "a",
            "origin_doc_ids": [10], "author": "human", "vietnamese_confirmed": True, "source_verified": True,
            "gold_languages": ["vi"], "tags": []}]
        self.q = self.root / "questions.json"
        self.j = self.root / "judgments.csv"
        self.s = self.root / "spans.csv"
        write_json(self.q, self.questions)
        write_csv(self.j, JUDGMENT_FIELDS, [{"query_id": 1, "doc_id": 10, "judged": "yes", "relevant": "yes",
            "annotator": "a", "reviewer": "b", "reason": "verified source"}])
        self.spans = [{"query_id": 1, "doc_id": 10, "derived_version_id": "clean", "region_id": "r",
            "start": 0, "end": 9, "annotator": "a", "reviewer": "b"}]
        write_csv(self.s, SPAN_FIELDS, self.spans)

    def compile(self, output="compiled", **kwargs):
        return compile_benchmark(self.q, self.j, self.s, self.evidence, self.root / output, **kwargs)

    def test_original_spans_and_review_provenance_are_compiled(self):
        self.assertTrue(self.compile()["human_reviewed"])
        benchmark = json.loads((self.root / "compiled/benchmark.json").read_text())
        chunk = benchmark["queries"][0]["relevant_chunks"][0]
        self.assertEqual(chunk["chunk_text"], self.packet["regions"][0]["text"][:9])
        self.assertEqual(chunk["source_start"], 9)
        self.assertEqual(chunk["text_sha256"], text_hash(chunk["chunk_text"]))

    def test_bad_offsets_changed_source_and_cross_split_groups_rejected(self):
        self.spans[0]["end"] = 9999
        write_csv(self.s, SPAN_FIELDS, self.spans)
        with self.assertRaisesRegex(ValueError, "Invalid/empty"):
            self.compile()
        self.spans[0]["end"] = 9
        write_csv(self.s, SPAN_FIELDS, self.spans)
        write_json(self.q, self.questions + [{**self.questions[0], "id": 2, "query": "Có sốt hay không?", "split": "holdout"}])
        with self.assertRaisesRegex(ValueError, "crosses splits"):
            self.compile()
        write_json(self.q, self.questions)
        with (self.evidence / "10.json").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.compile()

    def test_pool_never_assigns_automatic_negatives_and_preserves_existing_file(self):
        predictions = self.root / "predictions.json"
        write_json(predictions, [{"id": 1, "relevant_docs": [20], "relevant_chunks": []}])
        output = self.root / "pool.csv"
        self.assertEqual(pool_candidates(self.q, [predictions], output)["pairs"], 2)
        self.assertIn(",20,", output.read_text())
        self.assertNotIn("yes", output.read_text())
        with self.assertRaisesRegex(ValueError, "exists"):
            pool_candidates(self.q, [predictions], output)

    def test_insufficient_second_review_is_draft_only(self):
        write_csv(self.j, JUDGMENT_FIELDS, [{"query_id": 1, "doc_id": 10, "judged": "yes", "relevant": "yes",
            "annotator": "a", "reviewer": "", "reason": ""}])
        self.spans[0]["reviewer"] = ""
        write_csv(self.s, SPAN_FIELDS, self.spans)
        with self.assertRaisesRegex(ValueError, "20%"):
            self.compile()
        self.assertFalse(self.compile(allow_incomplete_review=True)["human_reviewed"])

    def make_run(self, name, benchmark_path, *, chunker, split="dev", cap=5):
        output = self.root / name
        output.mkdir()
        predictions = [{"id": 1, "relevant_docs": [10], "relevant_chunks": [
            {"doc_id": 10, "chunk_text": self.packet["regions"][0]["text"][:9]}]}]
        write_json(output / "predictions.json", predictions)
        metadata = {"benchmark_sha256": file_hash(benchmark_path), "split": split,
            "predictions_sha256": file_hash(output / "predictions.json"), "corpus_signature": "same-subset",
            "configuration_signature": "pending", "evaluation_policy": {"doc_cap": cap},
            "configuration": {"analyzer": "a", "retriever": "BM25 reference", "k1": 1.2, "b": 0.75,
                              "corpus_signature": "same-subset", "policy": {"doc_cap": cap},
                              "chunker": chunker, "chunk_config": {"target_tokens": 384}, "chunk_signature": name}}
        metadata["configuration_signature"] = text_hash(canonical_json(metadata["configuration"]))
        write_json(output / "run.json", metadata)
        return output

    def test_comparison_reports_paired_queries_and_rejects_changed_caps(self):
        self.compile()
        benchmark = self.root / "compiled/benchmark.json"
        a, b = self.make_run("a", benchmark, chunker="fixed"), self.make_run("b", benchmark, chunker="structure")
        report = compare_runs(benchmark, {"a": a, "b": b}, self.root / "compare", baseline="a", repetitions=10)
        self.assertEqual(report["scores"]["a"]["macro"]["chunk"]["f2"], 1)
        frozen = self.root / "selection.json"
        freeze_dev(self.root / "compare", frozen, ["a", "b"])
        self.assertIn("dev_labels_signature", json.loads(frozen.read_text()))
        metadata = json.loads((b / "run.json").read_text())
        metadata["evaluation_policy"]["doc_cap"] = 99
        metadata["configuration"]["policy"]["doc_cap"] = 99
        metadata["configuration_signature"] = text_hash(canonical_json(metadata["configuration"]))
        write_json(b / "run.json", metadata)
        with self.assertRaisesRegex(ValueError, "Uncontrolled"):
            compare_runs(benchmark, {"a": a, "b": b}, self.root / "badcompare", baseline="a")

    def test_holdout_cannot_run_without_dev_freeze(self):
        self.compile()
        benchmark = self.root / "compiled/benchmark.json"
        with self.assertRaisesRegex(ValueError, "Freeze dev"):
            compare_runs(benchmark, {"a": self.root, "b": self.root}, self.root / "holdout",
                         baseline="a", split="holdout")

    def test_locate_span_reports_exact_unicode_offsets_without_rewriting(self):
        text = self.root / "selection.txt"
        text.write_text("Không sốt\n", encoding="utf-8")
        output = self.root / "offsets.json"
        result = locate_span(self.evidence, 10, text, output, strip_final_newline=True)
        self.assertEqual(result["matches"], 1)
        candidate = json.loads(output.read_text())[0]
        self.assertEqual(self.packet["regions"][0]["text"][candidate["start"]:candidate["end"]], "Không sốt")


if __name__ == "__main__":
    unittest.main()
