"""S00 correctness tests for provenance, failure accounting, and resumable scans."""
import hashlib
import csv
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from exploration import audit
from exploration.audit_checks import inspect_record


def fixture_record(doc_id=1, text=None, **overrides):
    text = text if text is not None else "Kidney stone symptoms and management. " * 10
    row = {"id": doc_id, "url": f"https://example.org/{doc_id}", "domain": "example.org",
           "status": "200", "title": "Kidney stone symptoms", "text": text,
           "text_len": len(text), "blocks": json.dumps([["p", text]]), "html_trim": ""}
    row.update(overrides)
    return row


def inspect(row):
    return inspect_record(row, "shards/A/example.org/a.parquet", "a" * 64, 0, 0)[0]


class AuditChecksTest(unittest.TestCase):
    def test_quality_flags_do_not_delete_short_or_non_2xx_content(self):
        for row in [fixture_record(text="Không dùng 50 mg.", title=None),
                    fixture_record(status="403"), fixture_record(text="Verify you are human")]:
            result = inspect(row)
            self.assertNotEqual(result["disposition"], "unusable_currently")
            self.assertEqual(result["snippet"], row["text"][:600])
        self.assertIn("possible_block_page", inspect(fixture_record(text="Verify you are human"))["flags"])

    def test_missing_content_and_offline_recovery_are_distinct(self):
        empty = {"id": 1, "url": "https://example.org/1", "status": "404"}
        self.assertEqual(inspect(empty)["disposition"], "unusable_currently")
        self.assertEqual(inspect(empty)["text_state"], "missing_column")
        empty["blocks"] = json.dumps([["p", "Nội dung gốc có thể khôi phục."]])
        self.assertEqual(inspect(empty)["disposition"], "needs_reextraction")
        empty["blocks"] = "{broken"
        result = inspect(empty)
        self.assertIn("invalid_blocks", result["flags"])
        self.assertIsNone(result["n_blocks"])
        self.assertFalse(result["has_recovery_material"])

    def test_invalid_types_and_ids_keep_record_locators(self):
        result = inspect({"id": True, "url": 12, "text": 42, "blocks": "[[1,2]]", "title": []})
        self.assertIsNone(result["doc_id"])
        self.assertIn("missing_or_invalid_id", result["flags"])
        self.assertIn("invalid_text_type", result["flags"])
        self.assertIn("invalid_url_type", result["flags"])
        self.assertEqual(result["metadata"]["title"]["state"], "invalid_type")
        self.assertTrue(result["record_id"])

    def test_full_text_scripts_and_verbatim_clinical_spans(self):
        text = "a " * 4500 + "肾结石治疗无发热" * 3000 + " Không dùng 50 mg; not 5 mg."
        result = inspect(fixture_record(text=text))
        self.assertEqual(result["language_hint"], "mixed_script")
        self.assertEqual(result["script_ratios"]["scope"], "entire_text")
        self.assertEqual(result["verbatim_sha256"], hashlib.sha256(text.encode()).hexdigest())
        for signal in result["signals"].values():
            for span in signal["examples"]:
                self.assertEqual(text[span["start"]:span["end"]], span["text"])
        self.assertGreater(result["signals"]["number_unit"]["count"], 0)
        self.assertGreater(result["signals"]["negation"]["count"], 0)

    def test_html_metadata_candidates_do_not_invent_publication_dates(self):
        markup = '''<html><head><title>Kidney stones</title>
        <meta property="article:published_time" content="2020-03-01">
        <script type="application/ld+json">{"author":{"name":"A"},"datePublished":"2020"}</script>
        </head><body><h1>Kidney stones</h1><p>Original content.</p></body></html>'''
        row = fixture_record(text="", blocks="", html_trim=markup, ts=123456)
        result = inspect(row)
        self.assertEqual(result["html_state"], "parsed")
        self.assertEqual(result["disposition"], "needs_reextraction")
        self.assertEqual(result["metadata"]["published_date"]["state"], "missing_column")
        self.assertIn("datePublished", result["metadata"]["html_candidates"]["structured_fields"])
        self.assertNotIn("2020", result["snippet"])

    def test_text_block_mismatch_and_template_candidates_are_explicit(self):
        row = fixture_record(blocks='[["h", "Different content"]]')
        result, candidates = inspect_record(row, "a.parquet", "a", 0, 0)
        self.assertIs(result["blocks_text_match"], False)
        self.assertIn("blocks_text_mismatch", result["flags"])
        self.assertIn("headings_without_body", result["flags"])
        self.assertEqual(result["disposition"], "needs_review")
        self.assertTrue(candidates)


class AuditPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root, self.out = self.base / "Data", self.base / "audit"
        self.folder = self.root / "shards" / "A" / "example.org"
        self.folder.mkdir(parents=True)

    def write_shard(self, rows, name="a.parquet", row_group_size=2):
        path = self.folder / name
        pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=row_group_size)
        return path

    def run_audit(self, **kwargs):
        return audit.run_audit(self.root, self.out, sample_size=8, **kwargs)

    def test_complete_accounting_and_original_files_unchanged(self):
        self.write_shard([fixture_record(1), fixture_record(2), fixture_record(1, text="Different version. " * 20)])
        pq.write_table(pa.table({"id": [3], "url": ["https://example.org/3"], "status": ["403"]}), self.folder / "empty.parquet")
        # Reference Parquet and archives must not inflate corpus counts.
        pq.write_table(pa.table({"id": [99], "query": ["sỏi thận"]}), self.root / "query.parquet")
        (self.root / "archive.zip").write_bytes(b"archive placeholder")
        with closing(sqlite3.connect(self.root / "state.sqlite3")) as db:
            db.execute("CREATE TABLE crawl_state(id INTEGER)")
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.run_audit()
        self.assertFalse(result["partial"])
        self.assertEqual(result["records_read"], 4)
        self.assertEqual(result["conflicting_doc_ids"], 1)
        rows = pq.read_table(self.out / "record_audit.parquet").to_pylist()
        conflict = [r for r in rows if r["doc_id"] == 1]
        self.assertTrue(all(r["disposition"] == "needs_review" for r in conflict))
        self.assertEqual(len({r["record_id"] for r in rows}), 4)
        for name, content in before.items():
            self.assertEqual(Path(name).read_bytes(), content)
        self.assertEqual(set(before), {str(p) for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(result["manual_review_status"], "pending")
        self.assertTrue((self.out / "processing_plan.md").exists())
        with self.assertRaises(FileExistsError):
            self.run_audit()

    def test_pause_resume_and_interrupted_group_match_uninterrupted_output(self):
        self.write_shard([fixture_record(i) for i in range(6)])
        original = audit.inspect_record
        calls = 0
        def interrupt(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise KeyboardInterrupt()
            return original(*args, **kwargs)
        with patch.object(audit, "inspect_record", side_effect=interrupt):
            partial = self.run_audit(batch_size=1)
        self.assertEqual(partial["run_state"], "interrupted")
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["records_read"], 2)
        decisions = self.out / "review_decisions.csv"
        decisions.write_text("human review must survive resume\n")
        complete = self.run_audit(resume=True, batch_size=3)
        self.assertEqual(complete["records_read"], 6)
        self.assertFalse(complete["partial"])
        self.assertEqual(decisions.read_text(), "human review must survive resume\n")
        baseline = self.base / "baseline"
        audit.run_audit(self.root, baseline, sample_size=8, batch_size=2)
        self.assertEqual((baseline / "record_audit.parquet").read_bytes(), (self.out / "record_audit.parquet").read_bytes())
        self.assertEqual((baseline / "review_samples.jsonl").read_bytes(), (self.out / "review_samples.jsonl").read_bytes())
        again = self.run_audit(resume=True)
        self.assertEqual(again["records_read"], 6)

    def test_corrupt_file_has_unknown_count_and_does_not_hide_good_rows(self):
        self.write_shard([fixture_record(1)])
        (self.folder / "broken.parquet").write_bytes(b"not parquet")
        result = self.run_audit()
        self.assertTrue(result["partial"])
        self.assertEqual(result["records_read"], 1)
        self.assertIsNone(result["expected_selected_rows"])
        self.assertEqual(result["selected_files_with_unknown_row_count"], 1)
        self.assertEqual(result["file_states"]["failed"], 1)

    def test_failed_row_group_can_be_retried_without_losing_other_groups(self):
        self.write_shard([fixture_record(i) for i in range(6)])
        original = audit.iter_group
        def fail_middle(path, group_id, *args):
            if group_id == 1:
                raise OSError("synthetic unreadable row group")
            yield from original(path, group_id, *args)
        with patch.object(audit, "iter_group", side_effect=fail_middle):
            partial = self.run_audit()
        self.assertEqual(partial["records_read"], 4)
        self.assertTrue(partial["partial"])
        complete = self.run_audit(resume=True)
        self.assertEqual(complete["records_read"], 6)
        self.assertFalse(complete["partial"])

    def test_changed_input_and_semantic_config_reject_resume(self):
        path = self.write_shard([fixture_record(i) for i in range(4)])
        self.run_audit(stop_after_groups=1)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_audit(resume=True, short_chars=100)
        self.write_shard([fixture_record(999)], name=path.name)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.run_audit(resume=True)

    def test_input_mutation_during_scan_quarantines_all_derived_rows(self):
        path = self.write_shard([fixture_record(i) for i in range(2)])
        original = audit.inspect_record
        changed = False
        def mutate(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                with path.open("ab") as stream:
                    stream.write(b"external writer mutation")
                changed = True
            return result
        with patch.object(audit, "inspect_record", side_effect=mutate):
            result = self.run_audit()
        self.assertTrue(result["partial"])
        self.assertEqual(result["records_read"], 0)
        self.assertEqual(result["file_states"]["unstable_input"], 1)

    def test_record_exception_keeps_locator_and_marks_audit_partial(self):
        self.write_shard([fixture_record(1), fixture_record(2)])
        original = audit.inspect_record
        def fail_one(row, *args, **kwargs):
            if row["id"] == 2:
                raise ValueError("synthetic record error")
            return original(row, *args, **kwargs)
        with patch.object(audit, "inspect_record", side_effect=fail_one):
            result = self.run_audit()
        self.assertEqual(result["records_read"], 2)
        self.assertTrue(result["partial"])
        self.assertEqual(result["quality_flags"]["record_processing_error"], 1)
        rows = pq.read_table(self.out / "record_audit.parquet").to_pylist()
        self.assertTrue(all(r["record_id"] for r in rows))

    def test_dry_run_selection_and_output_guards(self):
        one = self.write_shard([fixture_record(1)])
        self.write_shard([fixture_record(2)], name="b.parquet")
        relative = one.relative_to(self.root).as_posix()
        manifest = self.run_audit(shard_paths=[relative], dry_run=True)
        self.assertFalse(self.out.exists())
        self.assertEqual(manifest["scope"], "selected_shards_only")
        result = self.run_audit(shard_paths=[relative])
        self.assertTrue(result["partial"])
        self.assertTrue(result["selected_scope_complete"])
        self.assertEqual(result["records_read"], 1)
        with self.assertRaises(ValueError):
            audit.run_audit(self.root, self.root / "output")
        with self.assertRaises(ValueError):
            self.run_audit(shard_paths=["../outside.parquet"], dry_run=True)

    def test_resume_extends_review_queue_without_losing_annotations(self):
        self.write_shard([fixture_record(i) for i in range(6)])
        self.run_audit(stop_after_groups=1)
        queue = self.out / "review_decisions.csv"
        with queue.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames, list(reader)
        reviewed_id = rows[0]["record_id"]
        rows[0]["reviewer"], rows[0]["notes"] = "reviewer A", "Keep 5 mg, not 50 mg."
        with queue.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        self.run_audit(resume=True)
        with queue.open(encoding="utf-8-sig", newline="") as stream:
            current = {r["record_id"]: r for r in csv.DictReader(stream)}
        sample_ids = {json.loads(line)["record"]["record_id"]
                      for line in (self.out / "review_samples.jsonl").read_text().splitlines()}
        self.assertTrue(sample_ids <= current.keys())
        self.assertEqual(current[reviewed_id]["reviewer"], "reviewer A")
        self.assertEqual(current[reviewed_id]["notes"], "Keep 5 mg, not 50 mg.")

    def test_source_sqlite_with_live_wal_is_cataloged_without_connections(self):
        self.write_shard([fixture_record()])
        state = self.root / "state.sqlite3"
        db = sqlite3.connect(state)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE data(id INTEGER)")
            db.execute("INSERT INTO data VALUES (1)")
            db.commit()
            before = {p: p.read_bytes() for p in self.root.glob('state.sqlite3*')}
            summary = self.run_audit()
            source = next(x for x in summary["files"] if x["kind"] == "sqlite")
            self.assertTrue(source["wal_present"])
            self.assertEqual(source["state"], "not_inspected")
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
        finally:
            db.close()

    def test_single_writer_lock(self):
        self.out.mkdir()
        with audit.output_lock(self.out):
            with self.assertRaisesRegex(ValueError, "writer"):
                with audit.output_lock(self.out):
                    self.fail("Concurrent writer acquired the lock")

    def test_fresh_output_is_rechecked_after_writer_lock(self):
        self.write_shard([fixture_record()])
        original_lock = audit.output_lock
        @contextmanager
        def competing_writer(out):
            (out / "foreign_output.txt").write_text("another completed writer")
            with original_lock(out):
                yield
        with patch.object(audit, "output_lock", side_effect=competing_writer):
            with self.assertRaises(FileExistsError):
                self.run_audit()
        self.assertEqual((self.out / "foreign_output.txt").read_text(), "another completed writer")
        self.assertFalse((self.out / "input_manifest.json").exists())

    def test_parallel_resume_matches_sequential_record_and_sample_outputs(self):
        self.write_shard([fixture_record(i, text="Không dùng 50 mg. 肾结石 not 5 mg. " * 10) for i in range(8)])
        self.run_audit(stop_after_groups=1, batch_size=1)
        result = self.run_audit(resume=True, batch_size=3, workers=2)
        self.assertEqual(result["records_read"], 8)
        baseline = self.base / "serial"
        audit.run_audit(self.root, baseline, sample_size=8, batch_size=2)
        for name in ("record_audit.parquet", "review_samples.jsonl", "duplicate_versions.parquet"):
            self.assertEqual((baseline / name).read_bytes(), (self.out / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
