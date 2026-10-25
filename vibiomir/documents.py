"""CPU-only, resumable document import. No cleaning, model inference, or network."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

from exploration.common import domain_of, normalize, parquet_batches
from .document_contracts import (IMPORT_REVISION, VERSION_SCHEMA, INPUT_SCHEMA, BLOCK_SCHEMA,
    SELECTION_SCHEMA, GROUP_SCHEMA, ALIAS_SCHEMA, REVIEW_SCHEMA, valid_doc_id, valid_url, parse_blocks, mapped_blocks)
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .metadata import extract_metadata

DEFAULT_CONFIG = {
    "metadata": {"body_candidates": True, "select_body_candidates": False, "date_locales": {}},
    "selection": {"mode": "provisional", "prefer_exact_blocks": True},
    "export": {"batch_rows": 128, "rows_per_file": 50000}}
AUDIT_COLUMNS = ["record_id", "doc_id", "shard", "source_sha256", "row_group", "row_offset",
                 "verbatim_sha256", "flags_json", "disposition", "language_hint", "doc_type_hint"]


def load_config(path=None):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    supplied = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    if not isinstance(supplied, dict) or set(supplied) - set(config):
        raise ValueError("Unknown or invalid stage-3 configuration section")
    for section, values in supplied.items():
        if not isinstance(values, dict) or set(values) - set(config[section]):
            raise ValueError("Unknown or invalid configuration key in " + section)
        config[section].update(values)
    for key in ("body_candidates", "select_body_candidates"):
        if not isinstance(config["metadata"][key], bool):
            raise ValueError(key + " must be boolean")
    locales = config["metadata"]["date_locales"]
    if not isinstance(locales, dict) or any(not isinstance(k, str) or v not in {"vi", "zh", "en", "unknown"} for k, v in locales.items()):
        raise ValueError("Invalid date_locales mapping")
    if config["selection"]["mode"] != "provisional" or not isinstance(config["selection"]["prefer_exact_blocks"], bool):
        raise ValueError("Only provisional selection is supported in stage 3")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in config["export"].values()):
        raise ValueError("Export row limits must be positive integers")
    return config


def _database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA cache_size=-32768")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS audit_cache(record_id TEXT PRIMARY KEY,doc_id INTEGER,shard TEXT,
      source_sha256 TEXT,row_group INTEGER,row_offset INTEGER,text_sha256 TEXT,flags_json TEXT,
      disposition TEXT,language_hint TEXT,doc_type_hint TEXT);
    CREATE TABLE IF NOT EXISTS files(shard TEXT PRIMARY KEY,state TEXT,error TEXT,final_sha256 TEXT);
    CREATE TABLE IF NOT EXISTS groups(shard TEXT,group_id INTEGER,rows INTEGER,state TEXT,error TEXT,
      PRIMARY KEY(shard,group_id));
    CREATE TABLE IF NOT EXISTS versions(version_id TEXT PRIMARY KEY,doc_id INTEGER,source_url TEXT,
      text_sha256 TEXT,normalized_sha256 TEXT,has_text INTEGER,blocked INTEGER,rank_json TEXT,
      representative_record_id TEXT,payload TEXT);
    CREATE INDEX IF NOT EXISTS versions_doc ON versions(doc_id,version_id);
    CREATE INDEX IF NOT EXISTS versions_content ON versions(normalized_sha256);
    CREATE TABLE IF NOT EXISTS inputs(record_id TEXT PRIMARY KEY,doc_id INTEGER,version_id TEXT,
      shard TEXT,row_group INTEGER,row_offset INTEGER,payload TEXT,
      UNIQUE(shard,row_group,row_offset));
    CREATE INDEX IF NOT EXISTS inputs_version ON inputs(version_id,record_id);
    CREATE INDEX IF NOT EXISTS inputs_doc ON inputs(doc_id);
    ''')
    return db


def _cache_audit(db, audit_parquet, selected, batch_size):
    if db.execute("SELECT value FROM meta WHERE key='audit_cache_ready'").fetchone():
        return
    paths = {item["path"] for item in selected}
    with pq.ParquetFile(audit_parquet) as pf:
        if set(AUDIT_COLUMNS) - set(pf.schema_arrow.names):
            raise ValueError("Audit record Parquet is missing required diagnostic columns")
    with db:
        db.execute("DELETE FROM audit_cache")
        for batch in parquet_batches(audit_parquet, AUDIT_COLUMNS, batch_size):
            rows = [r for r in batch.to_pylist() if r["shard"] in paths]
            db.executemany("INSERT INTO audit_cache VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ([r[k] for k in AUDIT_COLUMNS] for r in rows))
        expected = sum(sum(item["row_group_rows"]) for item in selected)
        actual = db.execute("SELECT COUNT(*) FROM audit_cache").fetchone()[0]
        if actual != expected:
            raise ValueError(f"Audit cache/input row-count mismatch: {actual} versus {expected}")
        db.execute("INSERT OR REPLACE INTO meta VALUES ('audit_cache_ready',?)", (str(actual),))


def _text_state(row):
    if "text" not in row:
        return "missing_column"
    if row["text"] is None:
        return "null"
    if not isinstance(row["text"], str):
        return "invalid_type"
    return "present" if row["text"].strip() else "empty"


def prepare_record(row, diagnostic, shard, checksum, group_id, offset, config):
    """Build one immutable version proposal and an independently retained input record."""
    record_id = text_hash(f"{checksum}\0{shard}\0{group_id}\0{offset}")
    doc_id = row.get("id") if valid_doc_id(row.get("id")) else None
    text = row.get("text") if isinstance(row.get("text"), str) else None
    text_sha = text_hash(text) if isinstance(text, str) and text.strip() else None
    if (diagnostic["record_id"] != record_id or diagnostic["doc_id"] != doc_id or
            diagnostic["source_sha256"] != checksum or diagnostic["text_sha256"] != text_sha or
            diagnostic["shard"] != shard or diagnostic["row_group"] != group_id or diagnostic["row_offset"] != offset):
        raise ValueError("Source row does not match its audit identity/text hash")
    raw_id_json = canonical_json(row.get("id"))
    raw_metadata = {k: v for k, v in row.items() if k not in {"text", "blocks", "html_trim"}}
    flags = json.loads(diagnostic["flags_json"] or "[]")
    source_url = row.get("url") if isinstance(row.get("url"), str) else None
    input_row = {"record_id": record_id, "doc_id": doc_id, "raw_doc_id_json": raw_id_json,
        "version_id": None, "source_shard": shard, "source_sha256": checksum,
        "row_group": group_id, "row_offset": offset, "source_url": source_url,
        "status": str(row.get("status") or "missing"), "crawl_metadata_json": canonical_json(raw_metadata),
        "audit_flags_json": canonical_json(flags), "audit_disposition": diagnostic["disposition"],
        "import_state": "quarantine_invalid_id" if doc_id is None else "imported"}
    if doc_id is None:
        return input_row, None, None
    raw_blocks = row.get("blocks")
    blocks_json = raw_blocks if isinstance(raw_blocks, str) else canonical_json(raw_blocks) if raw_blocks is not None else None
    blocks, block_state = parse_blocks(raw_blocks)
    mappings = list(mapped_blocks(text, blocks))
    exact = sum(b["mapping_state"] == "exact" for b in mappings)
    total_chars = sum(len(value) for _, value in blocks)
    mapped_chars = sum(len(b["block_text"]) for b in mappings if b["mapping_state"] == "exact")
    html_value = row.get("html_trim")
    html_available = isinstance(html_value, str) and bool(html_value.strip())
    domain = domain_of(source_url or "")
    metadata = extract_metadata(row, domain, config["metadata"])
    source_metadata = {k: v for k, v in row.items() if k not in {"text", "blocks", "html_trim", "ts", "status"}}
    identity = {"doc_id": doc_id, "evidence_text": text, "text_state": _text_state(row),
        "blocks_raw_json": blocks_json, "blocks_column_present": "blocks" in row,
        "html_sha256": text_hash(html_value) if isinstance(html_value, str) else None,
        "html_column_present": "html_trim" in row, "source_metadata": source_metadata,
        "import_revision": IMPORT_REVISION, "metadata_policy": config["metadata"],
        "extracted_metadata": metadata,
        "mapping_signature": [[b["ordinal"], b["kind"], b["start"], b["end"], b["mapping_state"]] for b in mappings]}
    version_id = text_hash(canonical_json(identity))
    input_row["version_id"] = version_id
    quality = set(flags) - {"non_2xx_status"}
    quality.add("main_content_not_validated")
    if block_state == "invalid":
        quality.add("invalid_blocks")
    if exact < sum(bool(value) for _, value in blocks):
        quality.add("unmapped_exact_blocks")
    if any(field["conflict"] for field in metadata["fields"].values()):
        quality.add("metadata_conflicts_retained")
    compact = normalize(text or "")
    soft_error = bool(re.fullmatch(r"(?:404 not found|403 forbidden|502 bad gateway|503 service unavailable)(?: (?:nginx|apache|openresty)[\w/. -]*)?", compact))
    final_url = row.get("final_url") if isinstance(row.get("final_url"), str) else None
    try:
        image_redirect = bool(final_url and urlsplit(final_url).path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")))
    except ValueError:
        image_redirect = False
    binary_signal = bool(image_redirect and text and text.count("\ufffd") >= max(3, len(text) * .05))
    if soft_error:
        quality.add("exact_soft_error_placeholder")
    if binary_signal:
        quality.add("binary_redirect_as_text_candidate")
    has_text = _text_state(row) == "present"
    blocked = soft_error or binary_signal or not valid_url(source_url)
    critical = len(quality & {"replacement_character", "invalid_blocks", "invalid_text_type", "headings_without_body", "blocks_text_mismatch"})
    weak = len(quality & {"possible_ui_text", "repeated_lines", "possible_block_page", "possible_title_body_mismatch"})
    status = input_row["status"]
    success = status.isdigit() and 200 <= int(status) < 300
    coverage = mapped_chars / max(total_chars, 1) if config["selection"]["prefer_exact_blocks"] else 0
    metadata_count = sum(f["value"] is not None for f in metadata["fields"].values())
    rank = [int(has_text and valid_url(source_url)), int(not blocked), -critical, round(coverage, 8), int(success), -weak, metadata_count]
    payload = {"version_id": version_id, "doc_id": doc_id, "source_url": source_url,
        "final_url": final_url, "evidence_text": text, "text_state": _text_state(row),
        "text_sha256": text_sha, "normalized_sha256": text_hash(compact) if compact else None,
        "blocks_raw_json": blocks_json, "blocks_state": block_state, "html_available": html_available,
        "html_sha256": identity["html_sha256"], "metadata_json": canonical_json(metadata),
        "source_metadata_json": canonical_json(source_metadata), "source_fidelity": "legacy_extracted",
        "content_scope": "partial" if has_text else "metadata_only", "import_revision": IMPORT_REVISION,
        "n_blocks": len(blocks), "n_exact_blocks": exact, "quality_flags_json": canonical_json(sorted(quality)),
        "language_hint": diagnostic["language_hint"], "doc_type_hint": diagnostic["doc_type_hint"], "official_match": "unverified"}
    return input_row, payload, {"rank": rank, "blocked": blocked, "has_text": has_text}


def _save(db, input_row, version, quality):
    if version is not None:
        old = db.execute("SELECT rank_json,representative_record_id FROM versions WHERE version_id=?", (version["version_id"],)).fetchone()
        if old is None:
            db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (version["version_id"], version["doc_id"], version["source_url"], version["text_sha256"], version["normalized_sha256"],
                 int(quality["has_text"]), int(quality["blocked"]), canonical_json(quality["rank"]), input_row["record_id"], canonical_json(version)))
        elif (quality["rank"] > json.loads(old["rank_json"]) or
              (quality["rank"] == json.loads(old["rank_json"]) and input_row["record_id"] < old["representative_record_id"])):
            db.execute("UPDATE versions SET rank_json=?,representative_record_id=? WHERE version_id=?",
                       (canonical_json(quality["rank"]), input_row["record_id"], version["version_id"]))
    db.execute("""INSERT INTO inputs
        (record_id,doc_id,version_id,shard,row_group,row_offset,payload)
        VALUES (?,?,?,?,?,?,?)""", (input_row["record_id"], input_row["doc_id"], input_row["version_id"],
        input_row["source_shard"], input_row["row_group"], input_row["row_offset"], canonical_json(input_row)))


class PartWriter:
    """Bounded batches, bounded part sizes, and atomic completion of each part."""

    def __init__(self, root, name, schema, batch_rows, rows_per_file):
        self.root, self.name, self.schema = Path(root), name, schema
        self.directory = self.root / name
        self.directory.mkdir(exist_ok=True)
        self.batch_rows, self.rows_per_file = batch_rows, rows_per_file
        self.buffer, self.parts = [], []
        self.writer, self.temporary, self.part_rows = None, None, 0

    def push(self, row):
        self.buffer.append(row)
        if len(self.buffer) >= self.batch_rows:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        table = pa.Table.from_pylist(self.buffer, schema=self.schema)
        self.buffer = []
        while table.num_rows:
            if self.writer is None:
                self.temporary = self.directory / f"part-{len(self.parts):06d}.parquet.tmp"
                self.writer = pq.ParquetWriter(self.temporary, self.schema, compression="zstd")
            size = min(table.num_rows, self.rows_per_file - self.part_rows)
            self.writer.write_table(table.slice(0, size))
            self.part_rows += size
            table = table.slice(size)
            if self.part_rows == self.rows_per_file:
                self._finish()

    def _finish(self):
        self.writer.close()
        final = self.temporary.with_suffix("")
        self.temporary.replace(final)
        self.parts.append({"path": final.relative_to(self.root).as_posix(), "rows": self.part_rows,
                           "sha256": file_hash(final), "bytes": final.stat().st_size})
        self.writer, self.temporary, self.part_rows = None, None, 0

    def close(self):
        self.flush()
        if self.writer is not None:
            self._finish()
        return {"rows": sum(p["rows"] for p in self.parts), "parts": self.parts, "schema": str(self.schema)}


def _export(db, out, config):
    export = config["export"]
    make = lambda name, schema: PartWriter(out, name, schema, export["batch_rows"], export["rows_per_file"])
    tables = {}
    writer = make("input_records", INPUT_SCHEMA)
    for row in db.execute("SELECT payload FROM inputs ORDER BY shard,row_group,row_offset"):
        writer.push(json.loads(row[0]))
    tables["input_records"] = writer.close()
    writer, block_writer = make("document_versions", VERSION_SCHEMA), make("blocks", BLOCK_SCHEMA)
    metadata_coverage = {"scope": "unique_document_versions", "total_versions": 0, "fields": {}}
    for row in db.execute('''SELECT v.payload,v.representative_record_id,COUNT(i.record_id) AS n
                            FROM versions v JOIN inputs i ON i.version_id=v.version_id
                            GROUP BY v.version_id ORDER BY v.doc_id,v.version_id'''):
        value = json.loads(row["payload"])
        value.update(input_record_count=row["n"], representative_record_id=row["representative_record_id"])
        metadata_coverage["total_versions"] += 1
        for field, details in json.loads(value["metadata_json"])["fields"].items():
            summary = metadata_coverage["fields"].setdefault(field, {"selected": 0, "missing": 0,
                "with_candidates": 0, "conflicts": 0, "selected_origins": {}})
            summary["selected" if details["value"] is not None else "missing"] += 1
            summary["with_candidates"] += bool(details["candidates"])
            summary["conflicts"] += details["conflict"]
            if details["selected"]:
                origin = details["selected"]["origin"]
                summary["selected_origins"][origin] = summary["selected_origins"].get(origin, 0) + 1
        writer.push(value)
        blocks, _ = parse_blocks(value["blocks_raw_json"])
        for block in mapped_blocks(value["evidence_text"], blocks, value["doc_id"], value["version_id"]):
            block_writer.push(block)
    tables["document_versions"], tables["blocks"] = writer.close(), block_writer.close()
    write_json(Path(out) / "metadata_coverage.json", metadata_coverage)
    writer = make("document_selection", SELECTION_SCHEMA)
    review_writer = make("version_review", REVIEW_SCHEMA)
    cursor = db.execute("SELECT * FROM versions ORDER BY doc_id,version_id")
    current, choices = None, []

    def emit(doc_id, versions):
        viable = [v for v in versions if v["has_text"] and not v["blocked"]]
        viable.sort(key=lambda v: v["version_id"])
        best = max(viable, key=lambda v: json.loads(v["rank_json"])) if viable else None
        different = len({v["normalized_sha256"] for v in versions if v["normalized_sha256"]})
        reasons = ["provisional_only", "main_content_qa_pending"]
        if different > 1:
            reasons.append("conflicting_content_versions")
        if best is None:
            reasons.append("no_eligible_content_candidate")
        for version in versions:
            chosen = best is not None and version["version_id"] == best["version_id"]
            reason = (["chosen_by_provisional_quality_rank"] if chosen else
                      ["no_nonempty_source_text"] if not version["has_text"] else
                      ["exact_placeholder_binary_redirect_or_invalid_url"] if version["blocked"] else
                      ["lower_quality_rank_or_stable_hash_tiebreak"])
            review_writer.push({"doc_id": doc_id, "version_id": version["version_id"],
                "decision": "selected_provisionally" if chosen else "retained_alternative",
                "rank_json": version["rank_json"], "reason_json": canonical_json(reason + ["main_content_qa_pending"]),
                "is_final": False})
        writer.push({"doc_id": doc_id, "selected_version_id": best["version_id"] if best else None,
            "version_count": len(versions), "normalized_text_versions": different,
            "availability": "usable_candidate" if best else "quarantine" if any(v["has_text"] for v in versions) else "unavailable",
            "selection_status": "provisional_review_required", "is_final": False, "needs_review": True,
            "reason_json": canonical_json(reasons), "official_match": "unverified"})

    for row in cursor:
        if current is not None and row["doc_id"] != current:
            emit(current, choices)
            choices = []
        current = row["doc_id"]
        choices.append(row)
    if current is not None:
        emit(current, choices)
    tables["document_selection"] = writer.close()
    tables["version_review"] = review_writer.close()
    writer = make("content_groups", GROUP_SCHEMA)
    for row in db.execute('''SELECT normalized_sha256,COUNT(*) AS version_count,COUNT(DISTINCT doc_id) AS doc_count
                            FROM versions WHERE normalized_sha256 IS NOT NULL GROUP BY normalized_sha256 ORDER BY normalized_sha256'''):
        writer.push(dict(row))
    tables["content_groups"] = writer.close()
    writer = make("document_aliases", ALIAS_SCHEMA)
    for row in db.execute('''SELECT v.normalized_sha256,v.doc_id,v.version_id,v.text_sha256
                            FROM versions v JOIN (SELECT normalized_sha256 FROM versions WHERE normalized_sha256 IS NOT NULL
                            GROUP BY normalized_sha256 HAVING COUNT(*)>1) g ON g.normalized_sha256=v.normalized_sha256
                            ORDER BY v.normalized_sha256,v.doc_id,v.version_id'''):
        writer.push({**dict(row), "group_kind": "normalized_content_candidate_not_identity_merge"})
    tables["document_aliases"] = writer.close()
    return tables


def build_documents(data_dir, audit_dir, output_dir, *, config_path=None, batch_size=16,
                    max_files=None, shard_paths=None, resume=False, stop_after_groups=None, dry_run=False):
    """Import only after audit-source identity checks; outputs remain provisional."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("batch-size must be a positive integer")
    if max_files is not None and (not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1):
        raise ValueError("max-files must be a positive integer")
    if stop_after_groups is not None and (not isinstance(stop_after_groups, int) or isinstance(stop_after_groups, bool) or stop_after_groups < 1):
        raise ValueError("stop-after-groups must be a positive integer")
    root, audit, out = Path(data_dir).resolve(), Path(audit_dir).resolve(), Path(output_dir).resolve()
    if not root.is_dir() or out == root or root in out.parents or out == audit or audit in out.parents:
        raise ValueError("Data must exist; store output must be outside source data and audit directories")
    config = load_config(config_path)
    required = [audit / name for name in ("input_manifest.json", "scan_status.json", "record_audit.parquet")]
    if not all(p.is_file() for p in required):
        raise ValueError("Audit input requires manifest, scan status, and record_audit.parquet")
    manifest, status = [json.loads(p.read_text(encoding="utf-8")) for p in required[:2]]
    if status.get("partial") or status.get("run_state") != "finished":
        raise ValueError("A finished, non-partial source audit is required")
    available = [f for f in manifest["files"] if f.get("selected")]
    if any(f.get("metadata_rows") is None or f.get("sha256") is None for f in available):
        raise ValueError("Audit inputs include unknown counts/checksums")
    paths = {f["path"] for f in available}
    if shard_paths is not None:
        if not isinstance(shard_paths, list) or not shard_paths or not all(isinstance(p, str) for p in shard_paths) or set(shard_paths) - paths:
            raise ValueError("Shard list must contain known relative audit paths")
        available = [f for f in available if f["path"] in set(shard_paths)]
    selected = sorted(available, key=lambda f: f["path"])
    if max_files is not None:
        selected = selected[:max_files]
    if not selected:
        raise ValueError("No audited source files were selected")
    if any(sum(f["row_group_rows"]) != f["metadata_rows"] for f in selected):
        raise ValueError("Audit file and row-group counts are inconsistent")
    for f in selected:
        path = (root / f["path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("Invalid or missing source path: " + f["path"])
        if file_hash(path) != f["sha256"]:
            raise ValueError("Source checksum differs from audit: " + f["path"])
    source_code = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    signature = {"config": config, "import_revision": IMPORT_REVISION,
        "code": source_code, "normalization_helper_sha256": file_hash(Path(__file__).parent.parent / "exploration/common.py"),
        "python": list(sys.version_info[:3]), "unicode": unicodedata.unidata_version, "pyarrow": pa.__version__,
        "audit_files": {p.name: file_hash(p) for p in required}, "selected_paths": [f["path"] for f in selected]}
    run = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "fingerprint": text_hash(canonical_json(signature)),
           "signature": signature, "data_root_at_creation": str(root), "audit_root_at_creation": str(audit),
           "scope": "all_audited_sources" if len(selected) == len(paths) else "selected_sources_only",
           "source_files": selected, "gpu_required": False, "ready_for_retrieval": False, "state": "initializing"}
    if dry_run:
        return {**run, "expected_input_records": sum(f["metadata_rows"] for f in selected)}
    if resume and not ((out / "run_manifest.json").is_file() and (out / "document_store.sqlite3").is_file()):
        raise ValueError("Resume requires the original store manifest and database")
    if not resume and out.exists() and any(out.iterdir()):
        raise FileExistsError("Choose a new empty store output directory")
    out.mkdir(parents=True, exist_ok=True)
    with writer_lock(out):
        if resume:
            old = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
            if old["fingerprint"] != run["fingerprint"]:
                raise ValueError("Incompatible source/audit/code/environment/config; start a new store")
            run = old
        elif any(p.name != ".store.lock" for p in out.iterdir()):
            raise FileExistsError("Output changed before acquiring the writer lock")
        write_json(out / "run_manifest.json", run)
        validation = out / "VALIDATION.json"
        if validation.exists():
            validation.unlink()
        db = _database(out / "document_store.sqlite3")
        state, attempts = "importing", 0
        try:
            _cache_audit(db, required[2], selected, batch_size=512)
            for index, item in enumerate(selected, 1):
                shard, path = item["path"], root / item["path"]
                previous = db.execute("SELECT state FROM files WHERE shard=?", (shard,)).fetchone()
                if previous and previous["state"] == "complete":
                    continue
                with db:
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,'importing',NULL,NULL)", (shard,))
                for group_id, expected in enumerate(item["row_group_rows"]):
                    group = db.execute("SELECT state FROM groups WHERE shard=? AND group_id=?", (shard, group_id)).fetchone()
                    if group and group["state"] == "complete":
                        continue
                    offset = 0
                    try:
                        with db:
                            for batch in parquet_batches(path, batch_size=batch_size, row_groups=[group_id]):
                                for row in batch.to_pylist():
                                    rid = text_hash(f"{item['sha256']}\0{shard}\0{group_id}\0{offset}")
                                    diagnostic = db.execute("SELECT * FROM audit_cache WHERE record_id=?", (rid,)).fetchone()
                                    if diagnostic is None:
                                        raise ValueError("Missing audit record for " + rid)
                                    prepared = prepare_record(row, diagnostic, shard, item["sha256"], group_id, offset, config)
                                    _save(db, *prepared)
                                    offset += 1
                            if offset != expected:
                                raise ValueError("Source row-group count does not match audit metadata")
                            db.execute("INSERT OR REPLACE INTO groups VALUES (?,?,?,'complete',NULL)", (shard, group_id, offset))
                    except Exception as exc:
                        with db:
                            db.execute("INSERT OR REPLACE INTO groups VALUES (?,?,0,'failed',?)", (shard, group_id, f"{type(exc).__name__}: {exc}"))
                        print(f"Failed row group {shard}#{group_id}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                    attempts += 1
                    if stop_after_groups is not None and attempts >= stop_after_groups:
                        state = "paused"
                        break
                final_hash = file_hash(path)
                with db:
                    if final_hash != item["sha256"]:
                        file_state = "unstable_input"
                        db.execute("DELETE FROM inputs WHERE shard=?", (shard,))
                        db.execute("DELETE FROM groups WHERE shard=?", (shard,))
                        db.execute("DELETE FROM versions WHERE version_id NOT IN (SELECT version_id FROM inputs WHERE version_id IS NOT NULL)")
                        db.execute("UPDATE files SET state='unstable_input',error='Source changed during import',final_sha256=? WHERE shard=?", (final_hash, shard))
                    else:
                        count = db.execute("SELECT COUNT(*) FROM groups WHERE shard=? AND state='complete'", (shard,)).fetchone()[0]
                        file_state = "complete" if count == len(item["row_group_rows"]) else "partial"
                        db.execute("UPDATE files SET state=?,final_sha256=? WHERE shard=?", (file_state, final_hash, shard))
                print(f"Documents: {index}/{len(selected)} source files checked; file state={file_state}", flush=True)
                if state == "paused":
                    break
            complete = db.execute("SELECT COUNT(*) FROM files WHERE state='complete'").fetchone()[0] == len(selected)
            if complete:
                state = "exporting"
                write_json(out / "run_manifest.json", {**run, "state": state})
                tables = _export(db, out, config)
                state = "complete_provisional"
                run["tables"] = tables
                run["auxiliary_files"] = {"metadata_coverage.json": file_hash(out / "metadata_coverage.json")}
            elif state != "paused":
                state = "partial"
        except KeyboardInterrupt:
            db.rollback()
            state = "interrupted"
        except Exception:
            db.rollback()
            state = "failed"
            raise
        finally:
            try:
                stats = {"input_records": db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0],
                    "document_versions": db.execute("SELECT COUNT(*) FROM versions").fetchone()[0],
                    "distinct_document_ids": db.execute("SELECT COUNT(DISTINCT doc_id) FROM inputs").fetchone()[0],
                    "invalid_id_records": db.execute("SELECT COUNT(*) FROM inputs WHERE doc_id IS NULL").fetchone()[0],
                    "files": [dict(row) for row in db.execute("SELECT * FROM files ORDER BY shard")],
                    "row_groups": [dict(row) for row in db.execute("SELECT * FROM groups ORDER BY shard,group_id")]}
                run.update(state=state, stats=stats, ready_for_retrieval=False,
                    exports_ready=state == "complete_provisional", gpu_required=False,
                    human_qa_status="pending", selection_policy="provisional_not_longest_or_latest")
                write_json(out / "run_manifest.json", run)
                _write_report(out, run)
            finally:
                db.close()
        return run


def _write_report(out, run):
    stats = run["stats"]
    lines = ["# Stage 3 document preparation", "", f"State: `{run['state']}`; scope: `{run['scope']}`.", "",
        "CPU only. Source text was imported verbatim; no cleaning, recrawling, model inference or indexing was performed.",
        "Selections are provisional. Metadata candidates and main content still require review; this is not a final retrieval-ready corpus.", "",
        "| Metric | Value |", "| --- | ---: |",
        *[f"| {key} | {stats[key]} |" for key in ("input_records", "document_versions", "distinct_document_ids", "invalid_id_records")], "",
        "Use `python -m vibiomir validate-documents --store-dir <this-directory>` after successful export.", "",
        "The SQLite file is the resume checkpoint. Keep source shards and the audit input files; retained HTML is referenced, not copied into every version.", ""]
    lines += ["`metadata_coverage.json` counts selected/missing/candidate/conflicting metadata per unique version.",
              "`version_review/` retains each provisional decision, quality rank and reason. Rank order: nonempty valid URL, not blocked, fewer critical flags, exact block coverage, HTTP success, fewer weak flags, explicit metadata count.", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
