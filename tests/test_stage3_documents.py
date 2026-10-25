"""Synthetic end-to-end contracts, audit compatibility, resume and corruption cases."""
import json
from contextlib import closing
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from exploration.audit_checks import inspect_record
from vibiomir.documents import AUDIT_COLUMNS, build_documents, load_config, prepare_record, _database, _save
from vibiomir.io import file_hash, text_hash, write_json
from vibiomir.validate_documents import iter_table, validate_documents


def record(doc_id=1, text="Không dùng 50 mg. 肾结石治疗. No fever.", **changes):
    value = {"id": doc_id, "url": f"https://example.org/{doc_id}", "status": "200",
             "text": text, "blocks": json.dumps([["p", text]], ensure_ascii=False),
             "title": None, "desc": None, "html_trim": "", "ts": "2025-01-01"}
    value.update(changes)
    return value


def fixture(root, rows, *, row_group_size=1):
    """Use the actual stage-2 inspector to construct audit-compatible diagnostics."""
    data, audit = root / "Data", root / "audit"
    data.mkdir(parents=True)
    audit.mkdir()
    path = data / "ViBioMIR/shards/A/example.org/part.parquet"
    path.parent.mkdir(parents=True)
    # Explicit union of columns also tests metadata-only and missing-field cases.
    keys = sorted({key for row in rows for key in row}) or ["id", "url", "text"]
    materialized = [{key: row.get(key) for key in keys} for row in rows]
    table = pa.Table.from_pylist(materialized) if rows else pa.table({"id": pa.array([], type=pa.int64()),
        "url": pa.array([], type=pa.string()), "text": pa.array([], type=pa.string())})
    pq.write_table(table, path, row_group_size=row_group_size)
    checksum, relative = file_hash(path), path.relative_to(data).as_posix()
    diagnostics = []
    with pq.ParquetFile(path) as pf:
        groups = [pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups)]
        for group in range(pf.num_row_groups):
            for offset, row in enumerate(pf.read_row_group(group).to_pylist()):
                inspected = inspect_record(row, relative, checksum, group, offset)[0]
                diagnostics.append({key: json.dumps(inspected["flags"]) if key == "flags_json"
                    else inspected[key] for key in AUDIT_COLUMNS})
    schema = pa.schema([(key, pa.int64() if key in {"doc_id", "row_group", "row_offset"} else pa.string())
                        for key in AUDIT_COLUMNS])
    pq.write_table(pa.Table.from_pylist(diagnostics, schema=schema), audit / "record_audit.parquet")
    write_json(audit / "input_manifest.json", {"files": [{"path": relative, "selected": True,
        "sha256": checksum, "metadata_rows": len(rows), "row_group_rows": groups}]})
    write_json(audit / "scan_status.json", {"run_state": "finished", "partial": False})
    return data, audit


class DocumentStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, rows, **kwargs):
        data, audit = fixture(self.root, rows)
        store = self.root / "store"
        manifest = build_documents(data, audit, store, **kwargs)
        return data, audit, store, manifest

    def table(self, store, manifest, name):
        return list(iter_table(store, manifest, name))

    def test_sql_input_persistence_keeps_locators_payload_and_quarantined_rows(self):
        """Regression: input INSERT must match the seven-column checkpoint schema."""
        with closing(_database(self.root / "checkpoint.sqlite3")) as db:
            prepared = []
            for offset, row in enumerate([record(1), record("invalid-id")]):
                diagnostic = inspect_record(row, "fixture.parquet", "a" * 64, 0, offset)[0]
                diagnostic["text_sha256"] = diagnostic["verbatim_sha256"]
                diagnostic["flags_json"] = json.dumps(diagnostic["flags"])
                values = prepare_record(row, diagnostic, "fixture.parquet", "a" * 64, 0, offset, load_config())
                with db:
                    _save(db, *values)
                prepared.append(values[0])
            persisted = db.execute("SELECT * FROM inputs ORDER BY row_offset").fetchall()
            self.assertEqual(len(persisted), 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM versions").fetchone()[0], 1)
            for saved, expected in zip(persisted, prepared):
                self.assertEqual(json.loads(saved["payload"]), expected)
                self.assertEqual(saved["shard"], expected["source_shard"])
                self.assertEqual(saved["record_id"], expected["record_id"])
                self.assertEqual(saved["version_id"], expected["version_id"])
            self.assertIsNone(persisted[1]["doc_id"])
            self.assertIsNone(persisted[1]["version_id"])
            self.assertEqual(prepared[1]["import_state"], "quarantine_invalid_id")

    def test_retries_collapse_but_metadata_and_content_versions_survive(self):
        base = record()
        rows = [base, dict(base, ts="2026-01-01", status="403"),
                dict(base, title="Source title"), record(text="Different original content.")]
        data, _, store, manifest = self.build(rows, batch_size=1)
        self.assertEqual(manifest["state"], "complete_provisional")
        self.assertEqual(manifest["stats"]["input_records"], 4)
        self.assertEqual(manifest["stats"]["document_versions"], 3)
        versions = self.table(store, manifest, "document_versions")
        self.assertEqual(sorted(v["input_record_count"] for v in versions), [1, 1, 2])
        self.assertEqual({v["evidence_text"] for v in versions}, {row["text"] for row in rows})
        self.assertTrue(all(v["official_match"] == "unverified" for v in versions))
        self.assertFalse(manifest["ready_for_retrieval"])
        coverage = json.loads((store / "metadata_coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(coverage["total_versions"], 3)
        self.assertEqual(coverage["fields"]["title"]["selected"], 1)
        reviews = self.table(store, manifest, "version_review")
        self.assertEqual(len(reviews), 3)
        self.assertEqual(sum(r["decision"] == "selected_provisionally" for r in reviews), 1)
        self.assertTrue(all(not r["is_final"] and json.loads(r["reason_json"]) for r in reviews))
        self.assertEqual(validate_documents(store, data_dir=data)["validation"], "passed")

    def test_offsets_do_not_normalize_unicode_entities_or_repeated_blocks(self):
        text = "Điều trị\nKhông dùng ５０ mg &amp; nước.\n重复\n重复"
        blocks = [["h", "Điều trị"], ["p", "Không dùng 50 mg & nước."], ["p", "重复"], ["p", "重复"]]
        _, _, store, manifest = self.build([record(text=text, blocks=json.dumps(blocks, ensure_ascii=False))])
        output = self.table(store, manifest, "blocks")
        self.assertEqual(output[1]["mapping_state"], "unmapped_exact")
        self.assertIsNone(output[1]["start"])
        for block in output:
            if block["mapping_state"] == "exact":
                self.assertEqual(text[block["start"]:block["end"]], block["block_text"])
        self.assertLess(output[2]["start"], output[3]["start"])
        self.assertEqual(json.loads(output[3]["heading_path_json"]), ["Điều trị"])
        self.assertEqual(self.table(store, manifest, "document_versions")[0]["evidence_text"], text)
        validate_documents(store)

    def test_short_and_non_2xx_text_are_candidates_while_placeholders_stay_in_history(self):
        _, _, store, manifest = self.build([
            record(1, text="Không dùng 50 mg.", status="403"),
            record(2, text="404 Not Found\nnginx", status="200"),
            record(3, text="\ufffd" * 50, final_url="https://example.org/x.png"),
            record(4, text="肾结石治疗。Footer captcha.")])
        selected = {r["doc_id"]: r for r in self.table(store, manifest, "document_selection")}
        self.assertIsNotNone(selected[1]["selected_version_id"])
        self.assertIsNotNone(selected[4]["selected_version_id"])
        for doc_id in (2, 3):
            self.assertIsNone(selected[doc_id]["selected_version_id"])
            self.assertEqual(selected[doc_id]["availability"], "quarantine")
        self.assertEqual(manifest["stats"]["document_versions"], 4)
        validate_documents(store)

    def test_selection_prefers_exact_structure_over_length_or_crawl_time(self):
        structured = record(text="Valid source body.", ts="2020-01-01")
        longer = record(text="Repeated unrelated material. " * 50, blocks='[["p","missing block"]]', ts="2026-01-01")
        _, _, store, manifest = self.build([longer, structured])
        selected = self.table(store, manifest, "document_selection")[0]
        versions = {r["version_id"]: r for r in self.table(store, manifest, "document_versions")}
        self.assertEqual(versions[selected["selected_version_id"]]["evidence_text"], structured["text"])
        self.assertFalse(selected["is_final"])
        self.assertTrue(selected["needs_review"])

    def test_duplicate_content_keeps_distinct_document_ids_and_aliases(self):
        _, _, store, manifest = self.build([record(1, text="Ａ &amp; B"), record(2, text="a & b")])
        self.assertEqual(len(self.table(store, manifest, "document_selection")), 2)
        aliases = self.table(store, manifest, "document_aliases")
        self.assertEqual({a["doc_id"] for a in aliases}, {1, 2})
        self.assertEqual(len({a["normalized_sha256"] for a in aliases}), 1)
        self.assertEqual(len({a["text_sha256"] for a in aliases}), 2)
        validate_documents(store)

    def test_metadata_only_and_invalid_ids_are_retained(self):
        data, _, store, manifest = self.build([{"id": 1, "url": "https://example.org/1", "title": "Metadata only"}])
        selected = self.table(store, manifest, "document_selection")[0]
        self.assertEqual(selected["availability"], "unavailable")
        self.assertIsNone(selected["selected_version_id"])
        self.assertEqual(self.table(store, manifest, "document_versions")[0]["text_state"], "missing_column")
        validate_documents(store, data_dir=data)
        bad_root = self.root / "invalid"
        bad_root.mkdir()
        data, audit = fixture(bad_root, [record(doc_id="not-an-integer")])
        bad_store = bad_root / "store"
        bad = build_documents(data, audit, bad_store)
        self.assertEqual(bad["stats"]["invalid_id_records"], 1)
        self.assertEqual(bad["stats"]["document_versions"], 0)
        self.assertEqual(self.table(bad_store, bad, "input_records")[0]["import_state"], "quarantine_invalid_id")
        validate_documents(bad_store)

    def test_pause_resume_and_relocated_inputs_match_uninterrupted_version_ids(self):
        data, audit, store, paused = self.build([record(1), record(2), record(1)], stop_after_groups=1)
        self.assertEqual(paused["state"], "paused")
        self.assertFalse(paused["exports_ready"])
        with self.assertRaises(ValueError):
            validate_documents(store)
        relocated = self.root / "relocated"
        shutil.copytree(data, relocated)
        resumed = build_documents(relocated, audit, store, resume=True, batch_size=2)
        validate_documents(store, data_dir=relocated)
        other = self.root / "uninterrupted"
        full = build_documents(data, audit, other, batch_size=3)
        self.assertEqual(self.table(store, resumed, "document_versions"), self.table(other, full, "document_versions"))
        self.assertEqual(resumed["stats"]["input_records"], 3)
        with self.assertRaises(FileExistsError):
            build_documents(data, audit, store)

    def test_source_and_audit_mutations_fail_before_reusing_checkpoint(self):
        data, audit, store, _ = self.build([record(1), record(2)], stop_after_groups=1)
        status = audit / "scan_status.json"
        original = status.read_text(encoding="utf-8")
        status.write_text(original + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Incompatible"):
            build_documents(data, audit, store, resume=True)
        status.write_text(original, encoding="utf-8")
        path = next(data.rglob("*.parquet"))
        with path.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            build_documents(data, audit, store, resume=True)

    def test_keyboard_interrupt_rolls_back_only_the_current_group_then_resumes(self):
        data, audit = fixture(self.root, [record(1), record(2)])
        store = self.root / "store"
        calls = 0
        def interrupt_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return prepare_record(*args, **kwargs)
        with patch("vibiomir.documents.prepare_record", side_effect=interrupt_second):
            stopped = build_documents(data, audit, store)
        self.assertEqual(stopped["state"], "interrupted")
        self.assertEqual(stopped["stats"]["input_records"], 1)
        resumed = build_documents(data, audit, store, resume=True)
        self.assertEqual(resumed["stats"]["input_records"], 2)
        validate_documents(store)

    def test_corrupt_audit_row_rolls_back_its_group_and_retry_keeps_no_partial_rows(self):
        data, audit = fixture(self.root, [record(1), record(2)], row_group_size=2)
        path = audit / "record_audit.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        rows[-1]["verbatim_sha256"] = "wrong"
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
        store = self.root / "store"
        partial = build_documents(data, audit, store)
        self.assertEqual(partial["state"], "partial")
        self.assertEqual(partial["stats"]["input_records"], 0)
        self.assertEqual(partial["stats"]["row_groups"][0]["state"], "failed")
        repeated = build_documents(data, audit, store, resume=True)
        self.assertEqual(repeated["stats"]["input_records"], 0)

    def test_dry_run_does_not_create_output_and_config_changes_require_new_store(self):
        data, audit = fixture(self.root, [record()])
        store = self.root / "store"
        preview = build_documents(data, audit, store, dry_run=True)
        self.assertEqual(preview["expected_input_records"], 1)
        self.assertFalse(store.exists())
        build_documents(data, audit, store)
        config = self.root / "config.json"
        write_json(config, {"selection": {"prefer_exact_blocks": False}})
        with self.assertRaisesRegex(ValueError, "Incompatible"):
            build_documents(data, audit, store, config_path=config, resume=True)

    def test_validator_detects_tampered_evidence_even_with_updated_part_checksum(self):
        _, _, store, manifest = self.build([record()])
        part = manifest["tables"]["document_versions"]["parts"][0]
        path = store / part["path"]
        table = pq.read_table(path)
        values = table.to_pylist()
        values[0]["evidence_text"] = "Invented evidence"
        pq.write_table(pa.Table.from_pylist(values, schema=table.schema), path)
        part["sha256"] = file_hash(path)
        write_json(store / "run_manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "checkpoint content"):
            validate_documents(store)

    def test_empty_shard_produces_typed_empty_table_manifests(self):
        _, _, store, manifest = self.build([])
        self.assertEqual(manifest["state"], "complete_provisional")
        self.assertTrue(all(t["rows"] == 0 and "schema" in t for t in manifest["tables"].values()))
        validate_documents(store)

    def test_input_locator_mismatch_and_invalid_configuration_are_rejected(self):
        row = record()
        diagnostic = {"record_id": "wrong", "doc_id": 1, "source_sha256": "abc",
                      "text_sha256": text_hash(row["text"]), "shard": "part.parquet", "row_group": 0, "row_offset": 0}
        with self.assertRaisesRegex(ValueError, "audit identity"):
            prepare_record(row, diagnostic, "part.parquet", "abc", 0, 0, load_config())
        config = self.root / "bad.json"
        write_json(config, {"export": {"batch_rows": True}})
        with self.assertRaises(ValueError):
            load_config(config)


if __name__ == "__main__":
    unittest.main()
