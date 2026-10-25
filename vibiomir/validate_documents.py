"""Validate a completed provisional store without claiming human/clinical QA."""
from contextlib import closing
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from exploration.common import normalize
from .document_contracts import (valid_doc_id, parse_blocks, mapped_blocks,
    INPUT_SCHEMA, VERSION_SCHEMA, BLOCK_SCHEMA, SELECTION_SCHEMA, GROUP_SCHEMA, ALIAS_SCHEMA, REVIEW_SCHEMA)
from .io import file_hash, text_hash, write_json, writer_lock


def iter_table(store, manifest, name):
    for part in manifest["tables"][name]["parts"]:
        path = (store / part["path"]).resolve()
        if store not in path.parents:
            raise ValueError("Export path escapes the store")
        for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
            yield from batch.to_pylist()


def validate_documents(store_dir, *, data_dir=None, write_report=True):
    """Check hashes, counts, relationships, exact source slices, and provisional state."""
    store = Path(store_dir).resolve()
    with writer_lock(store):
        return _validate_documents(store, data_dir=data_dir, write_report=write_report)


def _validate_documents(store, *, data_dir=None, write_report=True):
    manifest = json.loads((store / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("state") != "complete_provisional" or not manifest.get("exports_ready"):
        raise ValueError("A completed export is required; resume the build first")
    if manifest.get("ready_for_retrieval") is not False or manifest.get("gpu_required") is not False:
        raise ValueError("Invalid stage-3 readiness/resource claims")
    required = {"input_records", "document_versions", "blocks", "document_selection", "content_groups", "document_aliases", "version_review"}
    schemas = dict(input_records=INPUT_SCHEMA, document_versions=VERSION_SCHEMA, blocks=BLOCK_SCHEMA,
                   document_selection=SELECTION_SCHEMA, content_groups=GROUP_SCHEMA,
                   document_aliases=ALIAS_SCHEMA, version_review=REVIEW_SCHEMA)
    if set(manifest.get("tables", {})) != required:
        raise ValueError("Missing or unexpected export tables")
    auxiliary = manifest.get("auxiliary_files", {})
    if set(auxiliary) != {"metadata_coverage.json"} or file_hash(store / "metadata_coverage.json") != auxiliary["metadata_coverage.json"]:
        raise ValueError("Metadata coverage report is missing or changed")
    counts = {}
    for name in sorted(required):
        n = 0
        if manifest["tables"][name].get("schema") != str(schemas[name]):
            raise ValueError("Table manifest schema mismatch: " + name)
        seen_paths = set()
        for part in manifest["tables"][name]["parts"]:
            path = (store / part["path"]).resolve()
            if store not in path.parents or part["path"] in seen_paths or not path.is_file():
                raise ValueError("Invalid, missing, or repeated part path")
            seen_paths.add(part["path"])
            if file_hash(path) != part["sha256"]:
                raise ValueError("Export checksum mismatch: " + part["path"])
            with pq.ParquetFile(path) as pf:
                if not pf.schema_arrow.equals(schemas[name], check_metadata=False):
                    raise ValueError("Export schema mismatch: " + name)
                if pf.metadata.num_rows != part["rows"]:
                    raise ValueError("Export row-count mismatch")
                n += pf.metadata.num_rows
        if n != manifest["tables"][name]["rows"]:
            raise ValueError("Table count mismatch: " + name)
        counts[name] = n
    if counts["input_records"] != sum(f["metadata_rows"] for f in manifest["source_files"]):
        raise ValueError("Input count differs from selected audited sources")
    db_path = store / "document_store.sqlite3"
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA cache_size=-32768")
        db.execute("PRAGMA temp_store=FILE")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Checkpoint database integrity check failed")
        if db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] != counts["input_records"]:
            raise ValueError("Input export and checkpoint differ")
        if db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] != counts["document_versions"]:
            raise ValueError("Version export and checkpoint differ")
        if counts["version_review"] != counts["document_versions"]:
            raise ValueError("Every version requires a provisional review decision")
        expected_ids = db.execute("SELECT COUNT(DISTINCT doc_id) FROM inputs").fetchone()[0]
        if expected_ids != counts["document_selection"]:
            raise ValueError("Selection coverage differs from imported document IDs")
        if db.execute("SELECT COUNT(*) FROM inputs i LEFT JOIN versions v ON i.version_id=v.version_id WHERE i.version_id IS NOT NULL AND (v.version_id IS NULL OR v.doc_id!=i.doc_id)").fetchone()[0]:
            raise ValueError("Orphan or mismatched input-to-version relationship")
        db.execute("CREATE TEMP TABLE seen_inputs(record_id TEXT PRIMARY KEY)")
        for row in iter_table(store, manifest, "input_records"):
            try:
                db.execute("INSERT INTO seen_inputs VALUES (?)", (row["record_id"],))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Duplicate exported input record") from exc
            value = db.execute("SELECT payload FROM inputs WHERE record_id=?", (row["record_id"],)).fetchone()
            if value is None or json.loads(value[0]) != row:
                raise ValueError("Exported input differs from its checkpoint record")
        db.execute("CREATE TEMP TABLE seen_versions(version_id TEXT PRIMARY KEY)")
        for row in iter_table(store, manifest, "document_versions"):
            try:
                db.execute("INSERT INTO seen_versions VALUES (?)", (row["version_id"],))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Duplicate exported version") from exc
            stored = db.execute("SELECT payload FROM versions WHERE version_id=?", (row["version_id"],)).fetchone()
            if stored is None or any(row.get(k) != v for k, v in json.loads(stored[0]).items()):
                raise ValueError("Exported version differs from its checkpoint content")
            expected = text_hash(row["evidence_text"]) if isinstance(row["evidence_text"], str) and row["evidence_text"].strip() else None
            if not valid_doc_id(row["doc_id"]) or expected != row["text_sha256"]:
                raise ValueError("Invalid document identity or evidence hash")
            normalized = normalize(row["evidence_text"] or "")
            if row["normalized_sha256"] != (text_hash(normalized) if normalized else None):
                raise ValueError("Normalized grouping hash is inconsistent")
            if row["official_match"] != "unverified":
                raise ValueError("Stage 3 must not imply official submission eligibility")
            metadata = json.loads(row["metadata_json"])
            for field in metadata["fields"].values():
                selected = field["selected"]
                if selected is None:
                    if field["value"] is not None:
                        raise ValueError("Metadata value has no selected source")
                elif selected not in field["candidates"] or not selected["selectable"] or field["value"] != selected["normalized_value"]:
                    raise ValueError("Metadata provenance is inconsistent")
            if row["input_record_count"] != db.execute("SELECT COUNT(*) FROM inputs WHERE version_id=?", (row["version_id"],)).fetchone()[0]:
                raise ValueError("Version record count is inconsistent")
            link = db.execute("SELECT version_id FROM inputs WHERE record_id=?", (row["representative_record_id"],)).fetchone()
            if link is None or link[0] != row["version_id"]:
                raise ValueError("Representative record does not belong to its version")
        cached_version, evidence, cursor = None, None, 0
        def expected_blocks():
            for value in db.execute("SELECT payload FROM versions ORDER BY doc_id,version_id"):
                version = json.loads(value[0])
                blocks, _ = parse_blocks(version["blocks_raw_json"])
                yield from mapped_blocks(version["evidence_text"], blocks, version["doc_id"], version["version_id"])
        for actual, expected_block in zip_longest(iter_table(store, manifest, "blocks"), expected_blocks()):
            if actual != expected_block:
                raise ValueError("Block export differs from retained source blocks/mapping")
            row = actual
            if row["version_id"] != cached_version:
                value = db.execute("SELECT doc_id,payload FROM versions WHERE version_id=?", (row["version_id"],)).fetchone()
                if value is None or value["doc_id"] != row["doc_id"]:
                    raise ValueError("Orphan or mismatched block")
                cached_version, evidence, cursor = row["version_id"], json.loads(value["payload"])["evidence_text"], 0
            if row["mapping_state"] == "exact":
                start, end = row["start"], row["end"]
                if (not isinstance(start, int) or not isinstance(end, int) or start < cursor or
                        not isinstance(evidence, str) or not start <= end <= len(evidence) or evidence[start:end] != row["block_text"]):
                    raise ValueError("Block is not an exact monotonic source slice")
                cursor = end
            elif row["start"] is not None or row["end"] is not None:
                raise ValueError("Unmapped blocks must not invent offsets")
        # External sort/dedup uses SQLite rather than retaining all IDs in RAM.
        db.execute("CREATE TEMP TABLE seen_selections(doc_id INTEGER PRIMARY KEY,selected_version_id TEXT)")
        for row in iter_table(store, manifest, "document_selection"):
            if not valid_doc_id(row["doc_id"]) or row["is_final"] or not row["needs_review"] or row["selection_status"] != "provisional_review_required":
                raise ValueError("Invalid or falsely finalized selection")
            if db.execute("SELECT 1 FROM inputs WHERE doc_id=? LIMIT 1", (row["doc_id"],)).fetchone() is None:
                raise ValueError("Selection document is absent from imported inputs")
            try:
                db.execute("INSERT INTO seen_selections VALUES (?,?)", (row["doc_id"], row["selected_version_id"]))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Duplicate selection document ID") from exc
            if row["selected_version_id"] is not None:
                version = db.execute("SELECT doc_id,has_text,blocked FROM versions WHERE version_id=?", (row["selected_version_id"],)).fetchone()
                if version is None or version["doc_id"] != row["doc_id"] or not version["has_text"] or version["blocked"]:
                    raise ValueError("Invalid selected version")
            expected = db.execute("SELECT COUNT(*),COUNT(DISTINCT normalized_sha256) FROM versions WHERE doc_id=?", (row["doc_id"],)).fetchone()
            if (row["version_count"], row["normalized_text_versions"]) != tuple(expected):
                raise ValueError("Selection version counts are inconsistent")
        expected_groups = db.execute('''SELECT normalized_sha256,COUNT(*) AS version_count,
            COUNT(DISTINCT doc_id) AS doc_count FROM versions WHERE normalized_sha256 IS NOT NULL
            GROUP BY normalized_sha256 ORDER BY normalized_sha256''')
        for actual, expected_group in zip_longest(iter_table(store, manifest, "content_groups"), expected_groups):
            if actual != (dict(expected_group) if expected_group is not None else None):
                raise ValueError("Content groups differ from retained versions")
        expected_aliases = db.execute('''SELECT v.normalized_sha256,v.doc_id,v.version_id,v.text_sha256
            FROM versions v JOIN (SELECT normalized_sha256 FROM versions WHERE normalized_sha256 IS NOT NULL
            GROUP BY normalized_sha256 HAVING COUNT(*)>1) g ON g.normalized_sha256=v.normalized_sha256
            ORDER BY v.normalized_sha256,v.doc_id,v.version_id''')
        def aliases():
            for value in expected_aliases:
                yield {**dict(value), "group_kind": "normalized_content_candidate_not_identity_merge"}
        for actual, expected_alias in zip_longest(iter_table(store, manifest, "document_aliases"), aliases()):
            if actual != expected_alias:
                raise ValueError("Alias coverage differs from retained versions")
        db.execute("CREATE TEMP TABLE seen_review(version_id TEXT PRIMARY KEY)")
        for row in iter_table(store, manifest, "version_review"):
            version = db.execute("SELECT doc_id,rank_json FROM versions WHERE version_id=?", (row["version_id"],)).fetchone()
            if version is None or version["doc_id"] != row["doc_id"] or row["rank_json"] != version["rank_json"] or row["is_final"]:
                raise ValueError("Invalid provisional version review")
            if row["decision"] not in {"selected_provisionally", "retained_alternative"} or not json.loads(row["reason_json"]):
                raise ValueError("Version review requires a decision and reasons")
            selection = db.execute("SELECT selected_version_id FROM seen_selections WHERE doc_id=?", (row["doc_id"],)).fetchone()
            if selection is None or (row["decision"] == "selected_provisionally") != (selection[0] == row["version_id"]):
                raise ValueError("Review decision disagrees with document selection")
            try:
                db.execute("INSERT INTO seen_review VALUES (?)", (row["version_id"],))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Duplicate version review") from exc
    if data_dir is not None:
        root = Path(data_dir).resolve()
        for item in manifest["source_files"]:
            path = (root / item["path"]).resolve()
            if root not in path.parents or file_hash(path) != item["sha256"]:
                raise ValueError("Original source checksum mismatch")
    result = {"validation": "passed", "tables": counts, "source_checksums_verified": data_dir is not None,
              "selection_is_final": False, "human_qa": "pending", "gpu_required": False}
    if write_report:
        write_json(store / "VALIDATION.json", result)
    return result
