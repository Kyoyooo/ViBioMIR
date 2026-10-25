"""Synthetic stage-3-to-stage-4 handoff, persistence, resume and fidelity tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from vibiomir.cleaning import build_cleaned_documents, process_version
from vibiomir.cleaning_rules import load_cleaning_config
from vibiomir.documents import build_documents
from vibiomir.io import file_hash, write_json, writer_lock
from vibiomir.validate_documents import validate_documents
from vibiomir.validate_cleaning import iter_export, validate_cleaned_documents, verify_result
from test_stage3_documents import fixture, record
from test_stage4_transform import parent, config

ROOT = Path(__file__).resolve().parents[1]


class CleaningPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "config.json"
        settings = load_cleaning_config(ROOT / "configs/cleaning_stage4.json")
        settings.update(export_batch_rows=2, export_rows_per_file=2, review_sample_limit=2)
        write_json(self.profile, settings)

    def source(self, rows):
        data, audit = fixture(self.root, rows)
        stage3 = self.root / "stage3"
        build_documents(data, audit, stage3)
        validate_documents(stage3, data_dir=data)
        return data, stage3

    def run_clean(self, data, source, name="stage4", **kwargs):
        output = self.root / name
        result = build_cleaned_documents(source, data, output, config_path=self.profile, **kwargs)
        return output, result

    def table(self, output, manifest, name):
        return list(iter_export(output, manifest, name))

    def test_full_history_export_preserves_parent_store_and_sparse_metadata(self):
        body = "Clinical question and original source context.\n" * 5 + "投诉\nAnswer."
        rows = [record(1, text=body, url="https://120ask.com/q"),
                record(1, text="", blocks='[["p","Recovered original answer."]]'),
                record(2, text="404 Not Found\nnginx"), record(3, text="", blocks="", desc="Description only")]
        data, source = self.source(rows)
        before = {p.name: file_hash(p) for p in (source / "document_store.sqlite3", source / "run_manifest.json", source / "VALIDATION.json")}
        output, run = self.run_clean(data, source)
        self.assertEqual(run["state"], "complete_dry_run")
        self.assertEqual(run["tables"]["cleaning_versions"]["rows"], 4)
        self.assertEqual(run["tables"]["document_selection"]["rows"], 3)
        self.assertFalse(run["ready_for_retrieval"])
        versions = self.table(output, run, "cleaning_versions")
        self.assertEqual({v["availability"] for v in versions}, {"content_candidate", "quarantine", "unavailable"})
        self.assertTrue(all(json.loads(v["metadata_json"])["fields"]["abstract"]["value"] is None for v in versions))
        checked = validate_cleaned_documents(output, source, data)
        self.assertTrue(checked["source_slices_verified"])
        self.assertEqual(checked["human_qa"], "pending")
        self.assertEqual(before, {p.name: file_hash(p) for p in (source / "document_store.sqlite3", source / "run_manifest.json", source / "VALIDATION.json")})

    def test_missing_text_can_gain_a_candidate_without_inventing_metadata(self):
        data, source = self.source([record(1, text="", blocks='[["p","Original recovered body; no 5 mg."]]')])
        output, run = self.run_clean(data, source)
        selection = self.table(output, run, "document_selection")[0]
        self.assertIsNone(selection["stage3_selected_parent_id"])
        self.assertIsNotNone(selection["selected_parent_id"])
        self.assertTrue(selection["selection_changed"])
        self.assertFalse(selection["is_final"])
        validate_cleaned_documents(output, source, data)

    def test_real_local_html_row_is_checksum_verified_and_offsets_reference_raw_html(self):
        html = "<html><nav>Menu</nav><article><h1>肾结石</h1><p>No fever; dose 5 mg.</p></article></html>"
        data, source = self.source([record(1, text="", blocks="", html_trim=html)])
        output, run = self.run_clean(data, source)
        self.assertEqual(run["state"], "complete_dry_run", run.get("stats", {}).get("failed_examples"))
        self.assertTrue(run["exports_ready"])
        self.assertEqual(self.table(output, run, "cleaning_versions")[0]["extraction_method"], "recovered_html_article")
        regions = self.table(output, run, "evidence_regions")
        for region in regions:
            self.assertEqual(html[region["source_start"]:region["source_end"]], region["text"])
            self.assertIn("source_shard", json.loads(region["source_locator_json"]))
        self.assertEqual(validate_cleaned_documents(output, source, data)["raw_html_sources_verified"], 1)

    def test_changed_raw_html_source_fails_the_document_without_silent_fallback(self):
        data, source = self.source([record(1, text="", blocks="", html_trim="<article>Original body.</article>")])
        path = next(data.rglob("*.parquet"))
        with path.open("ab") as stream:
            stream.write(b"changed")
        output, run = self.run_clean(data, source)
        self.assertEqual(run["state"], "partial")
        self.assertEqual(run["stats"]["parent_versions_processed"], 0)
        self.assertIn("checksum", run["stats"]["failed_examples"][0]["error"])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validate_cleaned_documents(output, source, data)

    def test_paused_resume_matches_uninterrupted_outputs_and_handles_small_parts(self):
        data, source = self.source([record(1), record(2), record(3)])
        output, paused = self.run_clean(data, source, stop_after_documents=1)
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(paused["stats"]["documents_complete"], 1)
        self.assertFalse(paused["exports_ready"])
        _, resumed = self.run_clean(data, source, resume=True)
        other, whole = self.run_clean(data, source, name="whole")
        for name in resumed["tables"]:
            self.assertEqual(self.table(output, resumed, name), self.table(other, whole, name))
        self.assertGreater(len(resumed["tables"]["cleaning_versions"]["parts"]), 1)
        validate_cleaned_documents(output, source, data)
        with self.assertRaises(FileExistsError):
            self.run_clean(data, source)

    def test_interrupt_mid_document_rolls_back_all_its_versions(self):
        data, source = self.source([record(1), record(1, text="Different version."), record(2)])
        calls = 0
        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return process_version(*args, **kwargs)
        with patch("vibiomir.cleaning.process_version", side_effect=interrupted):
            _, stopped = self.run_clean(data, source)
        self.assertEqual(stopped["state"], "interrupted")
        self.assertEqual(stopped["stats"]["parent_versions_processed"], 0)
        output, resumed = self.run_clean(data, source, resume=True)
        self.assertEqual(resumed["stats"]["parent_versions_processed"], 3)
        validate_cleaned_documents(output, source, data)

    def test_record_subset_selects_all_versions_for_the_matching_document(self):
        data, source = self.source([record(1), record(1, text="Second source version."), record(2)])
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect((source / "document_store.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            rid = db.execute("SELECT record_id FROM inputs WHERE doc_id=1 LIMIT 1").fetchone()[0]
        output, run = self.run_clean(data, source, record_ids=[rid])
        self.assertEqual(run["scope"], "selected_documents")
        self.assertEqual(run["expected"], {"parent_versions": 2, "documents": 1})
        validate_cleaned_documents(output, source, data)
        with self.assertRaisesRegex(ValueError, "absent"):
            self.run_clean(data, source, name="unknown", record_ids=["not-a-source-record"])

    def test_preflight_creates_no_store_and_fingerprint_rejects_changed_config(self):
        data, source = self.source([record(1), record(2)])
        output, preview = self.run_clean(data, source, dry_run=True)
        self.assertEqual(preview["state"], "preflight_only")
        self.assertFalse(output.exists())
        self.run_clean(data, source, stop_after_documents=1)
        cfg = json.loads(self.profile.read_text())
        cfg["max_removed_fraction"] = .4
        write_json(self.profile, cfg)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_clean(data, source, resume=True)

    def test_apply_uses_separate_reviewed_profile_and_never_marks_final(self):
        data, source = self.source([record(1)])
        with self.assertRaisesRegex(ValueError, "reviewed rules"):
            self.run_clean(data, source, mode="apply")
        cfg = json.loads(self.profile.read_text())
        for rule in cfg["rules"]:
            rule["review_status"] = "approved"
        write_json(self.profile, cfg)
        output, run = self.run_clean(data, source, mode="apply")
        self.assertEqual(run["state"], "complete_provisional")
        self.assertFalse(self.table(output, run, "document_selection")[0]["is_final"])
        self.assertFalse(validate_cleaned_documents(output, source, data)["ready_for_retrieval"])

    def test_export_corruption_is_detected_even_if_manifest_part_hash_is_updated(self):
        data, source = self.source([record(1)])
        output, run = self.run_clean(data, source)
        part = run["tables"]["evidence_regions"]["parts"][0]
        path = output / part["path"]
        table = pq.read_table(path)
        rows = table.to_pylist()
        rows[0]["text"] = "Invented source evidence"
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
        part["sha256"] = file_hash(path)
        write_json(output / "run_manifest.json", run)
        with self.assertRaisesRegex(ValueError, "child rows"):
            validate_cleaned_documents(output, source, data)

    def test_independent_verifier_rejects_fabricated_source_offsets(self):
        original = parent("Không dùng 50 mg. Original clinical context.")
        result = process_version(original, config())
        corrupted = copy.deepcopy(result)
        corrupted["regions"][0]["source_start"] = 1
        with self.assertRaisesRegex(ValueError, "exact original source slice"):
            verify_result(corrupted, original, config(), None)

    def test_busy_or_unvalidated_parent_is_not_processed(self):
        data, source = self.source([record(1)])
        with writer_lock(source):
            with self.assertRaisesRegex(ValueError, "busy"):
                self.run_clean(data, source)
        (source / "VALIDATION.json").unlink()
        with self.assertRaisesRegex(ValueError, "validated"):
            self.run_clean(data, source)


if __name__ == "__main__":
    unittest.main()
