"""Structural/source-fidelity validation of stage-4 outputs; not clinical QA."""
from contextlib import closing
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from exploration.common import normalize
from .cleaning import implementation_signature, selected_query, selection_for, summarize
from .cleaning_contracts import TABLES
from .cleaning_rules import check_mode, clinical_signals, rulebook_hash
from .cleaning_source import RawHTMLLoader, parent_version, stage3_source, verify_stage3_signature
from .cleaning_transform import process_version, source_units
from .document_contracts import parse_blocks
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock


def iter_export(root, manifest, name):
    for part in manifest["tables"][name]["parts"]:
        path = (root / part["path"]).resolve()
        if root not in path.parents:
            raise ValueError("Export path escapes the cleaning store")
        with pq.ParquetFile(path) as pf:
            for batch in pf.iter_batches(batch_size=128):
                yield from batch.to_pylist()


def verify_slice(value, parent, loader):
    locator = json.loads(value["source_locator_json"])
    if locator.get("parent_version_id") != parent["version_id"]:
        raise ValueError("Evidence locator belongs to another parent")
    kind = value["source_kind"]
    if kind == "stage3_text" and locator.get("column") == "text":
        text = parent["evidence_text"]
    elif kind == "legacy_block" and locator.get("column") == "blocks":
        blocks, _ = parse_blocks(parent["blocks_raw_json"])
        index = locator.get("index")
        if type(index) is not int or not 0 <= index < len(blocks):
            raise ValueError("Invalid recovered block locator")
        text = blocks[index][1]
    elif kind == "html_text_node" and locator.get("column") == "html_trim":
        payload = loader(parent)
        if any(locator.get(k) != v for k, v in payload["locator"].items()):
            raise ValueError("HTML provenance differs from its original row")
        text = payload["html"]
    else:
        raise ValueError("Unknown evidence source kind/column")
    start, end = value["source_start"], value["source_end"]
    if type(start) is not int or type(end) is not int or not isinstance(text, str) or not 0 <= start < end <= len(text):
        raise ValueError("Invalid source offsets")
    if text[start:end] != value["text"]:
        raise ValueError("Evidence/transformation is not an exact original source slice")


def verify_result(result, parent, config, loader):
    """Independent slice/partition checks complement deterministic reprocessing."""
    version, regions, changes = result["version"], result["regions"], result["transformations"]
    if version["parent_version_id"] != parent["version_id"] or version["doc_id"] != parent["doc_id"]:
        raise ValueError("Derived identity differs from its parent")
    if version["metadata_json"] != parent["metadata_json"]:
        raise ValueError("Stage 4 must not invent or overwrite inherited metadata")
    if version["is_final"] or not version["needs_review"] or version["official_match"] != "unverified":
        raise ValueError("Stage 4 must remain provisional and retain reference uncertainty")
    units, _, _ = source_units(parent, config, loader)
    partitions = {}
    for kind, values in (("regions", regions), ("transformations", changes)):
        for ordinal, value in enumerate(values):
            if (value["ordinal"] != ordinal or value["derived_version_id"] != version["derived_version_id"] or
                    value["parent_version_id"] != parent["version_id"] or value["doc_id"] != parent["doc_id"]):
                raise ValueError("Invalid child identity/order")
            verify_slice(value, parent, loader)
            if kind == "regions":
                if value["text_sha256"] != text_hash(value["text"]) or value["normalized_text"] != normalize(value["text"]):
                    raise ValueError("Region hash/comparison view mismatch")
            elif value["decision"] not in {"vetoed", "proposed_removal", "applied_removal"}:
                raise ValueError("Unknown transformation decision")
            if kind == "transformations" and value["decision"] == "vetoed":
                continue
            if kind == "transformations" and any(clinical_signals(value["text"]).values()):
                raise ValueError("Removal contains a protected clinical signal")
            key = (value["source_kind"], value["source_locator_json"])
            partitions.setdefault(key, []).append((value["source_start"], value["source_end"]))
    expected_keys = {(u["source_kind"], canonical_json(u["locator"])) for u in units}
    if set(partitions) != expected_keys:
        raise ValueError("Source units and mapped partitions disagree")
    for unit in units:
        intervals = sorted(partitions[(unit["source_kind"], canonical_json(unit["locator"]))])
        cursor = unit["source_start"]
        for start, end in intervals:
            if start != cursor:
                raise ValueError("Kept/removed intervals overlap or leave an unmapped gap")
            cursor = end
        if cursor != unit["source_end"]:
            raise ValueError("Source unit is not completely accounted for")
    kept = sum(len(r["text"]) for r in regions)
    removed = sum(len(t["text"]) for t in changes if t["decision"] != "vetoed")
    if (version["kept_chars"] != kept or version["removed_chars"] != removed or
            version["source_chars"] != kept + removed + version["quarantined_chars"] or
            version["region_count"] != len(regions) or version["transformation_count"] != len(changes)):
        raise ValueError("Source character accounting is inconsistent")


def validate_cleaned_documents(output_dir, store_dir, data_dir, *, write_report=True):
    root = Path(output_dir).resolve()
    with writer_lock(root):
        run = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
        if run.get("stage") != 4 or run.get("state") not in {"complete_dry_run", "complete_provisional"} or not run.get("exports_ready"):
            raise ValueError("Stage-4 export is incomplete; inspect REPORT.md/errors and resume the matching run")
        if run.get("ready_for_retrieval") is not False or run.get("human_qa_status") != "pending":
            raise ValueError("Unsupported final-corpus/QA claim")
        if run["fingerprint"] != text_hash(canonical_json(run["signature"])):
            raise ValueError("Run fingerprint is inconsistent")
        if run["signature"]["code"] != implementation_signature():
            raise ValueError("Validate with the same stage-4 implementation used for this run")
        config, mode = run["signature"]["config"], run["mode"]
        check_mode(config, mode)
        if run["signature"]["mode"] != mode or run["rulebook_sha256"] != rulebook_hash(config):
            raise ValueError("Mode/rulebook differs from the run signature")
        if (mode == "dry-run") != (run["state"] == "complete_dry_run"):
            raise ValueError("Dry-run/apply state mismatch")
        if set(run.get("tables", {})) != set(TABLES):
            raise ValueError("Missing stage-4 export tables")
        counts, paths = {}, set()
        for name, schema in TABLES.items():
            if run["tables"][name]["schema"] != str(schema):
                raise ValueError("Export manifest schema mismatch")
            total = 0
            for part in run["tables"][name]["parts"]:
                path = (root / part["path"]).resolve()
                if root not in path.parents or path in paths or file_hash(path) != part["sha256"]:
                    raise ValueError("Export path/checksum mismatch")
                paths.add(path)
                with pq.ParquetFile(path) as pf:
                    if not pf.schema_arrow.equals(schema, check_metadata=False) or pf.metadata.num_rows != part["rows"]:
                        raise ValueError("Export schema/count mismatch")
                    total += pf.metadata.num_rows
            if total != run["tables"][name]["rows"]:
                raise ValueError("Export table count mismatch")
            counts[name] = total
        if set(run.get("auxiliary_files", {})) != {"processing_summary.json", "review_samples.jsonl"}:
            raise ValueError("Missing review/report artifacts")
        for name, expected in run["auxiliary_files"].items():
            if file_hash(root / name) != expected:
                raise ValueError("Review/report checksum mismatch")
        db_path = root / "cleaning.sqlite3"
        if any(Path(str(db_path) + suffix).exists() for suffix in ("-journal", "-wal")):
            raise ValueError("Close/recover the stage-4 database before validation")
        with stage3_source(store_dir) as (parent_root, source, manifest, signature), closing(
                sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-32768")
            if signature != run["signature"]["parent"]:
                raise ValueError("Parent stage-3 store differs from this run")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Stage-4 checkpoint is corrupt")
            query, expected = selected_query(source, run["signature"]["record_ids"], run["signature"]["max_documents"])
            if expected != run["expected"] or counts["cleaning_versions"] != expected["parent_versions"] or counts["document_selection"] != expected["documents"]:
                raise ValueError("Processed scope differs from the expected parent scope")
            stats = summarize(db)
            if stats["parent_versions_processed"] != counts["cleaning_versions"] or stats["documents_failed"] or stats["documents_complete"] != expected["documents"]:
                raise ValueError("Checkpoint coverage differs from the export")
            if stats != run["stats"] or dict(db.execute("SELECT shard,sha256 FROM raw_sources")) != run["raw_sources"]:
                raise ValueError("Run statistics/raw-source inventory differ from the checkpoint")
            loader = RawHTMLLoader(source, manifest, data_dir)
            loader.verify_files(dict(db.execute("SELECT shard,sha256 FROM raw_sources")))
            for key, table_name in (("regions", "evidence_regions"), ("transformations", "transformations")):
                def checkpoint_children():
                    for row in db.execute("SELECT payload FROM results ORDER BY doc_id,parent_version_id"):
                        yield from json.loads(row[0])[key]
                for actual, saved in zip_longest(iter_export(root, run, table_name), checkpoint_children()):
                    if actual != saved:
                        raise ValueError("Exported child rows differ from the checkpoint")
            source_rows = source.execute(query)
            export_rows = iter_export(root, run, "cleaning_versions")
            for source_row, actual in zip_longest(source_rows, export_rows):
                if source_row is None or actual is None:
                    raise ValueError("Parent/export version coverage mismatch")
                parent = parent_version(source_row)
                entry = db.execute("SELECT payload FROM results WHERE parent_version_id=?", (parent["version_id"],)).fetchone()
                if entry is None:
                    raise ValueError("Missing derived parent version")
                result = json.loads(entry[0])
                if result["version"] != actual:
                    raise ValueError("Version export differs from its checkpoint/order")
                recomputed = process_version(parent, config, mode=mode, html_loader=loader)
                recomputed["rank"] += json.loads(source_row["rank_json"])
                if recomputed != result:
                    raise ValueError("Derived processing is not reproducible from the pinned source/config")
                verify_result(result, parent, config, loader)
            db.execute("CREATE TEMP TABLE seen_selections(doc_id INTEGER PRIMARY KEY)")
            for actual in iter_export(root, run, "document_selection"):
                try:
                    db.execute("INSERT INTO seen_selections VALUES (?)", (actual["doc_id"],))
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Duplicate exported document selection") from exc
                rows = source.execute("SELECT * FROM versions WHERE doc_id=? ORDER BY version_id", (actual["doc_id"],)).fetchall()
                results = [json.loads(r[0]) for r in db.execute("SELECT payload FROM results WHERE doc_id=? ORDER BY parent_version_id", (actual["doc_id"],))]
                saved = db.execute("SELECT selection_json FROM jobs WHERE doc_id=? AND state='complete'", (actual["doc_id"],)).fetchone()
                if saved is None or actual != json.loads(saved[0]) or actual != selection_for(actual["doc_id"], results, rows):
                    raise ValueError("Document selection differs from its version history/quality ranks")
            loader.verify_files(dict(db.execute("SELECT shard,sha256 FROM raw_sources")))
            verify_stage3_signature(parent_root, signature)
        result = {"validation": "passed", "stage": 4, "mode": mode, "scope": run["scope"], "tables": counts,
            "parent_store_checksums_verified": True, "raw_html_sources_verified": len(run["raw_sources"]),
            "all_raw_shards_rehashed": False, "source_slices_verified": True, "human_qa": "pending",
            "selection_is_final": False, "ready_for_retrieval": False, "gpu_required": False}
        if write_report:
            write_json(root / "VALIDATION.json", result)
        return result
