"""Offline QA sampling/export/review fixtures. Owner executes these manually."""
import csv
import json
from pathlib import Path
import sqlite3
import unittest

import test_stage5_pipeline as fixtures
from vibiomir.chunk_pipeline import validate_chunks
from vibiomir.io import canonical_json, text_hash, write_json
from vibiomir.review_common import frozen_sample, wilson, write_csv
from vibiomir.stage5_review import FIELDS, catalog, diagnostics, export_cases, sample, summarize_reviews


class ReviewToolsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        with sqlite3.connect(f.stage3 / "document_store.sqlite3") as db:
            db.execute("ALTER TABLE versions ADD COLUMN source_url TEXT")
            db.execute("ALTER TABLE versions ADD COLUMN representative_record_id TEXT")
            parent = json.loads(db.execute("SELECT payload FROM versions").fetchone()[0])
            parent.update(source_url="https://example.org/article", source_fidelity="legacy_extracted", html_available=False)
            db.execute("UPDATE versions SET source_url=?,representative_record_id=?,payload=?",
                       (parent["source_url"], "record", canonical_json(parent)))
        f.prepare()
        f.build()
        validate_chunks(f.inputs, f.output, f.tokenizer, "fixture-tokenizer")
        self.config = {"input_dir": str(f.inputs), "stage3_dir": str(f.stage3), "stage4_dir": str(f.stage4),
                       "data_dir": None, "qa_dir": str(f.root / "qa"), "seed": 7,
                       "random_documents": 1, "risk_documents": 1, "minimum_per_domain": 3,
                       "minimum_per_language_hint": 3, "minimum_per_type": 3,
                       "second_review_fraction": 1.0, "proposed_main_content_target": 0.98,
                       "stores": {"structure": str(f.output), "fixed": str(f.root / "missing-fixed")}}
        self.qa = Path(self.config["qa_dir"])

    def prepare_review(self):
        catalog(self.config)
        value = sample(self.config)
        export_cases(self.config)
        return value

    def filled_reviews(self, *, same_person=False):
        rows = []
        for role, person in (("primary", "one"), ("secondary", "one" if same_person else "two")):
            rows.append({**dict.fromkeys(FIELDS, ""), "doc_id": 1, "role": role, "reviewer": person,
                         "language_reviewed": "en", "main_content": "pass", "meaning_preserved": "pass",
                         "chunk_structure": "pass", "decision": "accept"})
        path = self.qa / "reviews.csv"
        write_csv(path, FIELDS, rows)
        reviewers = self.qa / "reviewers.json"
        write_json(reviewers, {"one": {"languages": ["en"], "clinical_qualified": False},
                               "two": {"languages": ["en"], "clinical_qualified": True}})
        return path, reviewers

    def test_catalog_pause_resume_and_reproducible_separate_cohorts(self):
        catalog(self.config, stop_after_batches=1)
        self.assertEqual(json.loads((self.qa / "catalog_manifest.json").read_text())["state"], "paused")
        catalog(self.config, resume=True)
        first = sample(self.config)
        self.assertEqual(first, sample(self.config))
        self.assertEqual(first["eligible_population"], 1)
        self.assertEqual(first["random_sample"], 1)
        self.assertEqual(first["selection"][0]["cohorts"][0], "random")
        self.assertEqual(json.loads((self.qa / "selected_doc_ids.json").read_text()), [1])
        with self.assertRaisesRegex(ValueError, "seed"):
            sample({**self.config, "seed": 9})

    def test_export_and_clinical_second_review_preserve_sources(self):
        before = (self.fixture.output / "manifest.json").read_bytes()
        self.prepare_review()
        path, reviewers = self.filled_reviews()
        report = summarize_reviews(self.config, path, reviewers)
        self.assertTrue(report["review_gate_passed"])
        self.assertEqual(report["clinical_confirmed"], 1)
        self.assertEqual(report["random_main_content_rate"], 1)
        self.assertEqual(before, (self.fixture.output / "manifest.json").read_bytes())
        case = json.loads((self.qa / "cases/1.json").read_text())
        self.assertIn("No fever", case["parent"]["evidence_text"])
        self.assertIn("structure", case["strategies"])

    def test_independence_competence_and_changed_html_rejected(self):
        self.prepare_review()
        path, reviewers = self.filled_reviews(same_person=True)
        with self.assertRaisesRegex(ValueError, "Independent"):
            summarize_reviews(self.config, path, reviewers)
        path, reviewers = self.filled_reviews()
        registry = json.loads(reviewers.read_text())
        registry["two"]["languages"] = ["zh"]
        write_json(reviewers, registry)
        with self.assertRaisesRegex(ValueError, "competence"):
            summarize_reviews(self.config, path, reviewers)
        path, reviewers = self.filled_reviews()
        with (self.qa / "cases/1.html").open("a") as stream:
            stream.write("altered")
        with self.assertRaisesRegex(ValueError, "page changed"):
            summarize_reviews(self.config, path, reviewers)

    def test_edited_random_membership_cannot_be_summarized(self):
        self.prepare_review()
        path = self.qa / "sample.json"
        value = json.loads(path.read_text())
        value["selection"][0]["cohorts"] = ["risk"]
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "edited"):
            frozen_sample(path)

    def test_blank_review_reports_pending_without_manufacturing_quality(self):
        self.prepare_review()
        _, reviewers = self.filled_reviews()
        report = summarize_reviews(self.config, self.qa / "reviews_template.csv", reviewers)
        self.assertIsNone(report["random_main_content_rate"])
        self.assertFalse(report["review_gate_passed"])
        self.assertEqual(report["missing_required_reviews"], 1)

    def test_wilson_and_clinical_flags_do_not_normalize_evidence(self):
        self.assertIsNone(wilson(0, 0))
        interval = wilson(98, 100)
        self.assertLess(interval[0], 0.98)
        self.assertGreater(interval[1], 0.98)
        from test_stage5_chunking import packet
        source = packet("Không dùng 5 mg. 无发热。 No fever.\n")
        before = canonical_json(source)
        _, flags, _ = diagnostics(source)
        self.assertIn("dose_or_unit", flags)
        self.assertIn("negation_or_condition", flags)
        self.assertEqual(canonical_json(source), before)


if __name__ == "__main__":
    unittest.main()
