"""CPU fixed orchestration, comparison resume and bounded pilot contracts."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_stage5_review_tools as review_fixtures
from test_stage5_chunking import CONFIG
from vibiomir.chunk_pipeline import build_chunks, validate_chunks
from vibiomir.io import write_json
from vibiomir.reference_bm25 import load_subset
from vibiomir.stage5_experiments import compare_stores, full_fixed, prepare_pilots


class ExperimentToolsTest(unittest.TestCase):
    def setUp(self):
        self.review = review_fixtures.ReviewToolsTest("test_blank_review_reports_pending_without_manufacturing_quality")
        self.review.setUp()
        self.addCleanup(self.review.doCleanups)
        self.f = self.review.fixture
        self.config = {**self.review.config, "tokenizer_dir": str(self.f.root / "tokenizer"),
            "chunk_config": str(self.f.root / "chunk_config.json"), "workers": 1,
            "stores": {**self.review.config["stores"], "semantic": str(self.f.root / "missing-semantic")},
            "fixed_report_dir": str(self.f.root / "fixed-report"), "fixed_validation_dir": str(self.f.root / "fixed-validation"),
            "pilot_dir": str(self.f.root / "pilots")}
        write_json(Path(self.config["chunk_config"]), CONFIG)

    def fixed(self):
        path = Path(self.config["stores"]["fixed"])
        build_chunks(self.f.inputs, path, self.f.tokenizer, "fixture-tokenizer", strategy="fixed", config=CONFIG)
        validate_chunks(self.f.inputs, path, self.f.tokenizer, "fixture-tokenizer")
        return path

    def test_full_fixed_pauses_resumes_and_validates_with_one_command(self):
        def validate(input_dir, output_dir, tokenizer_dir, checkpoint_dir, **kwargs):
            return validate_chunks(input_dir, output_dir, self.f.tokenizer, "fixture-tokenizer")
        with patch("vibiomir.chunk_parallel.load_tokenizer", return_value=(self.f.tokenizer, "fixture-tokenizer")), \
             patch("vibiomir.stage5_experiments.validate_parallel", side_effect=validate):
            self.assertEqual(full_fixed(self.config, stop_after_batches=1)["state"], "paused")
            result = full_fixed(self.config)
            self.assertEqual(result["validation"], "passed")
            self.assertFalse(result["gpu_used"])

    def test_comparison_uses_same_documents_and_does_not_double_count_resume(self):
        fixed = self.fixed()
        stores = {"fixed": fixed, "structure": self.f.output}
        output = self.f.root / "comparison"
        first = compare_stores(self.f.inputs, stores, output, exact_duplicates=True)
        second = compare_stores(self.f.inputs, stores, output, exact_duplicates=True)
        self.assertEqual(first, second)
        self.assertTrue(first["controlled_chunker_comparison"])
        self.assertFalse(first["retrieval_quality_measured"])
        for result in first["strategies"].values():
            self.assertEqual(result["counts"]["documents"], 3)
            self.assertEqual(result["counts"]["documents_without_chunks"], 2)
            self.assertIsNotNone(result["duplicate_groups"])
        with self.assertRaisesRegex(ValueError, "changed"):
            compare_stores(self.f.inputs, stores, output, selected_ids=[1])

    def test_unknown_comparison_subset_rejected(self):
        fixed = self.fixed()
        with self.assertRaisesRegex(ValueError, "not found"):
            compare_stores(self.f.inputs, {"fixed": fixed, "structure": self.f.output}, self.f.root / "bad", selected_ids=[99])

    def test_pilots_require_reviews_or_explicit_debug_flag_and_bind_configs(self):
        self.review.prepare_review()
        with self.assertRaisesRegex(ValueError, "Only 0"):
            prepare_pilots(self.config, documents=1)
        plan = prepare_pilots(self.config, documents=1, allow_unreviewed=True)
        self.assertEqual(plan, prepare_pilots(self.config, documents=1, allow_unreviewed=True))
        self.assertTrue(plan["allow_unreviewed"])
        self.assertEqual(plan["documents"], 1)
        self.assertEqual(sum(v["gpu_required"] for v in plan["variants"]), 3)
        self.assertTrue((Path(self.config["pilot_dir"]) / "workflow.json").exists())

    def test_reference_index_refuses_to_truncate_and_covers_declared_subset(self):
        self.review.prepare_review()
        with self.assertRaisesRegex(ValueError, "Nothing was truncated"):
            load_subset(self.config, "structure", [1], max_chunks=1)
        chunks, _, _, _ = load_subset(self.config, "structure", [1, 2], max_chunks=1000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual({c["doc_id"] for c in chunks}, {1})


if __name__ == "__main__":
    unittest.main()
