"""Manual, offline integration tests for batches, resume, CLI and validation."""
import copy
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from vibiomir.__main__ import main
from vibiomir.chunk_batches import batches, read_json, read_rows
from vibiomir.chunk_pipeline import build_chunks, prepare_inputs, validate_chunks
from vibiomir.io import file_hash, text_hash, write_json
from test_stage5_chunking import CharacterTokenizer, CONFIG


class Stage5PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage3, self.stage4 = self.root / "s3", self.root / "s4"
        self.stage3.mkdir()
        self.stage4.mkdir()
        for path in (self.stage3, self.stage4):
            (path / ".store.lock").touch()
        with sqlite3.connect(self.stage3 / "document_store.sqlite3") as db:
            db.execute("CREATE TABLE versions(version_id TEXT PRIMARY KEY,payload TEXT)")
            parent = {"doc_id": 1, "version_id": "raw", "evidence_text": "No fever.\n" * 40,
                      "blocks_raw_json": '[["p","No fever."]]'}
            db.execute("INSERT INTO versions VALUES (?,?)", ("raw", json.dumps(parent)))
        write_json(self.stage3 / "run_manifest.json", {"fixture": True})
        write_json(self.stage3 / "VALIDATION.json", {"validation": "passed"})
        with sqlite3.connect(self.stage4 / "cleaning.sqlite3") as db:
            db.execute("CREATE TABLE jobs(doc_id INTEGER PRIMARY KEY,state TEXT,selection_json TEXT)")
            db.execute("CREATE TABLE results(derived_version_id TEXT PRIMARY KEY,payload TEXT)")
            for doc_id in (1, 2, 3):
                selection = {"selected_derived_version_id": "derived" if doc_id == 1 else None,
                             "selected_parent_id": "raw" if doc_id == 1 else None,
                             "availability": "content_candidate" if doc_id == 1 else "unavailable"}
                db.execute("INSERT INTO jobs VALUES (?,?,?)", (doc_id, "complete", json.dumps(selection)))
            version = {"doc_id": 1, "derived_version_id": "derived", "parent_version_id": "raw",
                       "availability": "content_candidate", "metadata_json": '{"fields":{}}'}
            region = {**{k: version[k] for k in ("doc_id", "derived_version_id", "parent_version_id")},
                      "region_id": "r", "kind": "legacy_text", "source_kind": "stage3_text",
                      "source_locator_json": '{"column":"text"}', "source_start": 0,
                      "source_end": len(parent["evidence_text"]), "text": parent["evidence_text"],
                      "text_sha256": text_hash(parent["evidence_text"])}
            db.execute("INSERT INTO results VALUES (?,?)", ("derived", json.dumps({"version": version, "regions": [region]})))
        write_json(self.stage4 / "run_manifest.json", {"state": "complete_provisional", "mode": "apply",
            "signature": {"parent": {"manifest_sha256": file_hash(self.stage3 / "run_manifest.json"),
                                     "validation_sha256": file_hash(self.stage3 / "VALIDATION.json")}},
            "tables": {"document_selection": {"rows": 3}}})
        write_json(self.stage4 / "VALIDATION.json", {"validation": "passed", "source_slices_verified": True})
        self.inputs, self.output = self.root / "inputs", self.root / "chunks"
        self.tokenizer = CharacterTokenizer()

    def prepare(self, **kwargs):
        return prepare_inputs(self.stage4, self.stage3, self.inputs, batch_documents=1, **kwargs)

    def build(self, **kwargs):
        return build_chunks(self.inputs, self.output, self.tokenizer, "fixture-tokenizer", config=CONFIG, **kwargs)

    def test_prepare_resume_build_resume_and_zero_chunk_accounting(self):
        self.assertEqual(self.prepare(stop_after_batches=1)["state"], "paused")
        self.assertEqual(self.prepare(resume=True)["documents"], 3)
        self.assertEqual(self.build(stop_after_batches=1)["state"], "paused")
        self.assertEqual(self.build(resume=True)["state"], "complete")
        result = validate_chunks(self.inputs, self.output, self.tokenizer, "fixture-tokenizer")
        self.assertEqual(result["documents"], 3)
        self.assertEqual(result["documents_without_chunks"], 2)
        self.assertFalse(result["ready_for_retrieval"])
        fresh = self.root / "fresh"
        build_chunks(self.inputs, fresh, self.tokenizer, "fixture-tokenizer", config=CONFIG)
        rows = lambda p: [row for path, _ in batches(p) for row in read_rows(path)]
        self.assertEqual(rows(self.output), rows(fresh))

    def test_changed_config_and_completed_directory_fail_with_actionable_errors(self):
        self.prepare()
        self.build()
        with self.assertRaisesRegex(ValueError, "--resume"):
            self.build()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.build(resume=True, strategy="fixed")
        with self.assertRaisesRegex(ValueError, "Cannot resume"):
            build_chunks(self.inputs, self.root / "missing", self.tokenizer, "fixture-tokenizer", resume=True)

    def test_committed_batch_survives_interruption_before_manifest_finish(self):
        self.prepare()
        from vibiomir.chunk_pipeline import commit_batch as real_commit
        def interrupted(*args, **kwargs):
            real_commit(*args, **kwargs)
            raise KeyboardInterrupt()
        with patch("vibiomir.chunk_pipeline.commit_batch", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.build()
        self.assertEqual(self.build(resume=True)["documents"], 3)

    def test_checksum_tampering_and_rehashed_wrong_chunk_both_fail(self):
        self.prepare()
        self.build()
        batch = self.output / "batch-000000"
        path = batch / "documents.jsonl.gz"
        rows = list(read_rows(batch))
        rows[0]["chunks"][0]["chunk_text"] = "Wrong source"
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(rows[0]) + "\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_chunks(self.inputs, self.output, self.tokenizer, "fixture-tokenizer")
        descriptor = read_json(batch / "batch.json")
        descriptor["sha256"] = file_hash(path)
        write_json(batch / "batch.json", descriptor)
        with self.assertRaisesRegex(ValueError, "source text"):
            validate_chunks(self.inputs, self.output, self.tokenizer, "fixture-tokenizer")

    def test_dry_run_and_wrong_stage3_parent_fail_before_output_created(self):
        manifest = read_json(self.stage4 / "run_manifest.json")
        changed = copy.deepcopy(manifest)
        changed["mode"] = "dry-run"
        write_json(self.stage4 / "run_manifest.json", changed)
        with self.assertRaisesRegex(ValueError, "apply"):
            self.prepare()
        self.assertFalse(self.inputs.exists())
        write_json(self.stage4 / "run_manifest.json", manifest)
        write_json(self.stage3 / "VALIDATION.json", {"changed": True})
        with self.assertRaisesRegex(ValueError, "parent"):
            self.prepare()

    def test_uncommitted_pending_directory_is_ignored(self):
        self.prepare()
        self.build(stop_after_batches=1)
        (self.output / ".pending-interrupted").mkdir()
        (self.output / ".pending-interrupted" / "broken").write_text("incomplete")
        self.assertEqual(self.build(resume=True)["state"], "complete")

    def test_cli_checks_missing_inputs_before_loading_models(self):
        with patch("vibiomir.chunk_models.load_tokenizer") as load:
            with self.assertRaises(SystemExit):
                main(["build-chunks", "--input-dir", str(self.inputs), "--output-dir", str(self.output),
                      "--tokenizer-dir", str(self.root / "missing-model")])
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
