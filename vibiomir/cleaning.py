"""Manual CPU stage 4: offline recovery, cleaning proposals and resumable exports."""
from contextlib import closing
from datetime import datetime, timezone
from itertools import groupby
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata

import pyarrow as pa

from exploration.common import domain_of

from .cleaning_contracts import TABLES
from .cleaning_rules import REVISION, check_mode, load_cleaning_config, rulebook_hash
from .cleaning_source import RawHTMLLoader, parent_version, stage3_source, verify_stage3_signature
from .cleaning_transform import process_version
from .documents import PartWriter
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock


def database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA cache_size=-32768")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript('''
      CREATE TABLE IF NOT EXISTS results(parent_version_id TEXT PRIMARY KEY,derived_version_id TEXT UNIQUE,
        doc_id INTEGER,availability TEXT,method TEXT,removed_chars INTEGER,kept_chars INTEGER,
        quarantined_chars INTEGER,vetoed_candidates INTEGER,payload TEXT);
      CREATE INDEX IF NOT EXISTS results_doc ON results(doc_id,parent_version_id);
      CREATE TABLE IF NOT EXISTS jobs(doc_id INTEGER PRIMARY KEY,state TEXT,error TEXT,selection_json TEXT);
      CREATE TABLE IF NOT EXISTS raw_sources(shard TEXT PRIMARY KEY,sha256 TEXT);
    ''')
    return db


def save_result(db, result):
    v = result["version"]
    db.execute('''INSERT INTO results
        (parent_version_id,derived_version_id,doc_id,availability,method,removed_chars,kept_chars,quarantined_chars,vetoed_candidates,payload)
        VALUES (?,?,?,?,?,?,?,?,?,?)''', (v["parent_version_id"], v["derived_version_id"], v["doc_id"], v["availability"],
        v["extraction_method"], v["removed_chars"], v["kept_chars"], v["quarantined_chars"],
        json.loads(v["diagnostics_json"])["vetoed_removal_candidates"], canonical_json(result)))
    for region in result["regions"] + result["transformations"]:
        if region["source_kind"] == "html_text_node":
            locator = json.loads(region["source_locator_json"])
            db.execute("INSERT OR IGNORE INTO raw_sources(shard,sha256) VALUES (?,?)",
                       (locator["source_shard"], locator["source_sha256"]))


def selection_for(doc_id, results, source_rows):
    eligible = sorted((r for r in results if r["version"]["content_candidate"]), key=lambda r: r["version"]["parent_version_id"])
    best = max(eligible, key=lambda r: r["rank"]) if eligible else None
    old_eligible = sorted((r for r in source_rows if r["has_text"] and not r["blocked"]), key=lambda r: r["version_id"])
    old = max(old_eligible, key=lambda r: json.loads(r["rank_json"])) if old_eligible else None
    old_id = old["version_id"] if old else None
    selected_parent = best["version"]["parent_version_id"] if best else None
    changed = old_id != selected_parent
    reasons = ["stage4_provisional_rank", "main_content_and_clinical_qa_pending"]
    if changed:
        reasons.append("parent_choice_changed_after_offline_processing")
    if not best:
        reasons.append("no_safe_content_candidate")
    return {"doc_id": doc_id, "selected_derived_version_id": best["version"]["derived_version_id"] if best else None,
        "parent_version_count": len(results), "stage3_selected_parent_id": old_id, "selected_parent_id": selected_parent,
        "selection_changed": changed, "availability": "content_candidate" if best else
        "quarantine" if any(r["version"]["availability"] == "quarantine" for r in results) else "unavailable",
        "reason_json": canonical_json(reasons), "is_final": False, "needs_review": True}


def selected_query(source, record_ids=None, max_documents=None):
    if record_ids is not None and max_documents is not None:
        raise ValueError("Choose record-ids or max-documents, not both")
    if max_documents is not None and (type(max_documents) is not int or max_documents < 1):
        raise ValueError("max-documents must be positive")
    if record_ids is not None:
        if not isinstance(record_ids, list) or not record_ids or len(record_ids) > 10000 or any(not isinstance(r, str) for r in record_ids):
            raise ValueError("record-ids must be a nonempty list of at most 10000 record IDs")
        ids = set()
        for rid in sorted(set(record_ids)):
            row = source.execute("SELECT doc_id FROM inputs WHERE record_id=?", (rid,)).fetchone()
            if row is None or row[0] is None:
                raise ValueError("Pilot record ID is absent or quarantined in this parent store: " + rid)
            ids.add(row[0])
        source.execute("CREATE TEMP TABLE selected_docs(doc_id INTEGER PRIMARY KEY)")
        source.executemany("INSERT INTO selected_docs VALUES (?)", ((doc_id,) for doc_id in sorted(ids)))
        where = "WHERE doc_id IN (SELECT doc_id FROM selected_docs)"
    elif max_documents is not None:
        where = f"WHERE doc_id IN (SELECT DISTINCT doc_id FROM versions ORDER BY doc_id LIMIT {max_documents})"
    else:
        where = ""
    rows = source.execute("SELECT COUNT(*),COUNT(DISTINCT doc_id) FROM versions " + where).fetchone()
    return "SELECT * FROM versions " + where + " ORDER BY doc_id,version_id", {"parent_versions": rows[0], "documents": rows[1]}


def implementation_signature():
    root = Path(__file__).parent
    names = ("cleaning.py", "cleaning_rules.py", "cleaning_source.py", "cleaning_transform.py", "cleaning_contracts.py",
             "validate_cleaning.py", "documents.py", "document_contracts.py", "io.py")
    return {**{name: file_hash(root / name) for name in names},
            "exploration/common.py": file_hash(root.parent / "exploration/common.py")}


def summarize(db):
    totals = db.execute("SELECT COUNT(*),COALESCE(SUM(removed_chars),0),COALESCE(SUM(kept_chars),0),COALESCE(SUM(quarantined_chars),0) FROM results").fetchone()
    summary = {"parent_versions_processed": totals[0], "removed_source_chars": totals[1], "kept_source_chars": totals[2],
        "quarantined_source_chars": totals[3], "documents_complete": db.execute("SELECT COUNT(*) FROM jobs WHERE state='complete'").fetchone()[0],
        "documents_failed": db.execute("SELECT COUNT(*) FROM jobs WHERE state='failed'").fetchone()[0],
        "by_extraction_method": {r[0]: r[1] for r in db.execute("SELECT method,COUNT(*) FROM results GROUP BY method")},
        "by_availability": {r[0]: r[1] for r in db.execute("SELECT availability,COUNT(*) FROM results GROUP BY availability")},
        "failed_examples": [dict(r) for r in db.execute("SELECT doc_id,error FROM jobs WHERE state='failed' ORDER BY doc_id LIMIT 20")]}
    return summary


def export_results(db, output, config):
    writers = {name: PartWriter(output, name, schema, config["export_batch_rows"], config["export_rows_per_file"])
               for name, schema in TABLES.items()}
    rule_counts = {}
    flags, changed_selections, domains = {}, 0, {}
    for row in db.execute("SELECT payload FROM results ORDER BY doc_id,parent_version_id"):
        result = json.loads(row[0])
        writers["cleaning_versions"].push(result["version"])
        v = result["version"]
        bucket = domains.setdefault(domain_of(v["source_url"] or ""), {"versions": 0, "changed_versions": 0,
            "recovered_versions": 0, "noncandidate_versions": 0, "removed_chars": 0, "vetoed_versions": 0})
        bucket["versions"] += 1
        bucket["changed_versions"] += v["removed_chars"] > 0
        bucket["removed_chars"] += v["removed_chars"]
        bucket["recovered_versions"] += v["extraction_method"].startswith("recovered_")
        bucket["noncandidate_versions"] += not v["content_candidate"]
        bucket["vetoed_versions"] += json.loads(v["diagnostics_json"])["vetoed_removal_candidates"] > 0
        for flag in json.loads(result["version"]["quality_flags_json"]):
            flags[flag] = flags.get(flag, 0) + 1
        for name, key in (("evidence_regions", "regions"), ("transformations", "transformations")):
            for value in result[key]:
                writers[name].push(value)
                if key == "transformations":
                    group = rule_counts.setdefault(value["rule_id"], {})
                    group[value["decision"]] = group.get(value["decision"], 0) + 1
    for row in db.execute("SELECT selection_json FROM jobs WHERE state='complete' ORDER BY doc_id"):
        value = json.loads(row[0])
        changed_selections += value["selection_changed"]
        writers["document_selection"].push(value)
    tables = {name: writer.close() for name, writer in writers.items()}
    with (Path(output) / "review_samples.jsonl").open("w", encoding="utf-8") as stream:
        # Separate deterministic queues: changed, recovery and unavailable, not a population estimate.
        for category, where in (("changed", "removed_chars>0"),
                ("vetoed_for_review", "vetoed_candidates>0"),
                ("offline_recovery", "method IN ('recovered_legacy_blocks','recovered_html_article')"),
                ("unavailable_or_quarantine", "availability!='content_candidate'"),
                ("unchanged_counterexamples", "removed_chars=0 AND availability='content_candidate'")):
            query = "SELECT payload FROM results WHERE " + where + " ORDER BY derived_version_id LIMIT ?"
            for row in db.execute(query, (config["review_sample_limit"],)):
                result = json.loads(row[0])
                value = {"category": category, "version": result["version"],
                    "region_examples": [{**r, "text": r["text"][:1000], "normalized_text": r["normalized_text"][:1000],
                                         "preview_only": True} for r in result["regions"][:3]],
                    "transformation_examples": [{**t, "text": t["text"][:1000], "preview_only": True} for t in result["transformations"][:5]],
                    "review_status": "pending", "sampling": "deterministic_targeted_not_random"}
                stream.write(canonical_json(value) + "\n")
    write_json(Path(output) / "processing_summary.json", {**summarize(db), "rules": rule_counts,
        "quality_flags": flags, "selection_changes": changed_selections, "by_domain": domains,
        "normalization": "comparison_view_only_evidence_regions_remain_verbatim",
        "metadata": "inherited_unchanged_from_stage3_missing_abstracts_authors_dates_remain_unknown"})
    return tables


def build_cleaned_documents(store_dir, data_dir, output_dir, *, config_path=None, mode="dry-run",
                            record_ids=None, max_documents=None, stop_after_documents=None, resume=False, dry_run=False):
    """dry_run=True is preflight only; mode=dry-run writes proposed derived regions."""
    config = load_cleaning_config(config_path)
    check_mode(config, mode)
    if stop_after_documents is not None and (type(stop_after_documents) is not int or stop_after_documents < 1):
        raise ValueError("stop-after-documents must be positive")
    parent_root, data_root, out = Path(store_dir).resolve(), Path(data_dir).resolve(), Path(output_dir).resolve()
    if not data_root.is_dir() or out in {parent_root, data_root} or parent_root in out.parents or data_root in out.parents:
        raise ValueError("Output must be separate from the parent store and raw Data directory")
    if not resume and out.exists() and any(out.iterdir()):
        raise FileExistsError("Choose a new empty stage-4 output directory")
    if resume and not ((out / "run_manifest.json").is_file() and (out / "cleaning.sqlite3").is_file()):
        raise ValueError("Resume needs the original stage-4 manifest and database")
    print("Stage 4: verifying the completed stage-3 checkpoint; hashing large inputs can take time", flush=True)
    with stage3_source(parent_root) as (_, source, parent_manifest, parent_signature):
        query, expected = selected_query(source, record_ids, max_documents)
        if not expected["documents"]:
            raise ValueError("No source document versions were selected")
        print(f"Stage 4: {expected['documents']} documents / {expected['parent_versions']} parent versions; mode={mode}", flush=True)
        signature = {"parent": parent_signature, "code": implementation_signature(), "revision": REVISION,
            "config": config, "mode": mode, "record_ids": sorted(set(record_ids)) if record_ids is not None else None,
            "max_documents": max_documents, "python": list(sys.version_info[:3]),
            "unicode": unicodedata.unidata_version, "pyarrow": pa.__version__}
        run = {"stage": 4, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "fingerprint": text_hash(canonical_json(signature)), "signature": signature, "expected": expected,
            "parent_store_at_creation": str(parent_root), "data_root_at_creation": str(data_root),
            "scope": "all_parent_documents" if record_ids is None and max_documents is None else "selected_documents",
            "mode": mode, "gpu_required": False, "ready_for_retrieval": False, "human_qa_status": "pending",
            "rulebook_sha256": rulebook_hash(config), "state": "initializing", "exports_ready": False}
        if dry_run:
            return {**run, "state": "preflight_only"}
        out.mkdir(parents=True, exist_ok=True)
        with writer_lock(out):
            if resume:
                old = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
                if old["fingerprint"] != run["fingerprint"]:
                    raise ValueError("Stage-4 input/code/config/environment changed; start a new output directory")
                run = old
            elif any(p.name != ".store.lock" for p in out.iterdir()):
                raise FileExistsError("Output changed before acquiring the stage-4 lock")
            run.update(state="processing", exports_ready=False)
            write_json(out / "run_manifest.json", run)
            if (out / "VALIDATION.json").exists():
                (out / "VALIDATION.json").unlink()
            state, attempted = "processing", 0
            with closing(database(out / "cleaning.sqlite3")) as db:
                loader = RawHTMLLoader(source, parent_manifest, data_root)
                try:
                    loader.verify_files(dict(db.execute("SELECT shard,sha256 FROM raw_sources")))
                    for doc_id, group in groupby(source.execute(query), key=lambda r: r["doc_id"]):
                        rows = list(group)
                        previous = db.execute("SELECT state FROM jobs WHERE doc_id=?", (doc_id,)).fetchone()
                        if previous and previous[0] == "complete":
                            continue
                        try:
                            with db:
                                results = []
                                for row in rows:
                                    result = process_version(parent_version(row), config, mode=mode, html_loader=loader)
                                    result["rank"] += json.loads(row["rank_json"])
                                    save_result(db, result)
                                    results.append(result)
                                selection = selection_for(doc_id, results, rows)
                                db.execute("INSERT OR REPLACE INTO jobs(doc_id,state,error,selection_json) VALUES (?,'complete',NULL,?)",
                                           (doc_id, canonical_json(selection)))
                        except Exception as exc:
                            with db:
                                db.execute("INSERT OR REPLACE INTO jobs(doc_id,state,error,selection_json) VALUES (?,'failed',?,NULL)",
                                           (doc_id, f"{type(exc).__name__}: {exc}"))
                            print(f"Failed document {doc_id}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                        attempted += 1
                        if attempted % 1000 == 0:
                            print(f"Stage 4: {attempted} document attempts in this invocation", flush=True)
                        if stop_after_documents is not None and attempted >= stop_after_documents:
                            state = "paused"
                            break
                    summary = summarize(db)
                    complete = summary["documents_complete"] == expected["documents"] and summary["parent_versions_processed"] == expected["parent_versions"]
                    loader.verify_files(dict(db.execute("SELECT shard,sha256 FROM raw_sources")))
                    if complete:
                        state = "exporting"
                        write_json(out / "run_manifest.json", {**run, "state": state})
                        run["tables"] = export_results(db, out, config)
                        run["auxiliary_files"] = {name: file_hash(out / name) for name in ("processing_summary.json", "review_samples.jsonl")}
                        loader.verify_files(dict(db.execute("SELECT shard,sha256 FROM raw_sources")))
                        verify_stage3_signature(parent_root, parent_signature)
                        state = "complete_dry_run" if mode == "dry-run" else "complete_provisional"
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
                    run.update(state=state, stats=summarize(db),
                        raw_sources=dict(db.execute("SELECT shard,sha256 FROM raw_sources")),
                        exports_ready=state in {"complete_dry_run", "complete_provisional"})
                    write_json(out / "run_manifest.json", run)
                    write_report(out, run)
            return run


def write_report(out, run):
    lines = ["# Stage 4 offline cleaning", "", f"State: `{run['state']}`; mode: `{run['mode']}`; scope: `{run['scope']}`.", "",
        "CPU local processing. Parent stage-3 artifacts and raw sources are read-only. No recrawl, GPU, indexing or generated medical evidence.",
        "All selections remain provisional; independent main-content/clinical QA is pending.", "",
        "| Metric | Value |", "| --- | ---: |"]
    for key in ("parent_versions_processed", "documents_complete", "documents_failed", "kept_source_chars", "removed_source_chars", "quarantined_source_chars"):
        lines.append(f"| {key} | {run['stats'][key]} |")
    lines += ["", "Inspect `processing_summary.json` and `review_samples.jsonl` after successful export.",
        "Dry-run evidence regions are proposed outputs, not approved production evidence.",
        "Each evidence region maps to one contiguous original source slice. Never concatenate across removed spans and claim a continuous original chunk.",
        "Metadata is inherited unchanged; descriptions do not become abstracts and missing authors/dates remain unknown.",
        "Review configuration rule status and run tests manually before an apply run in a separate output directory.", ""]
    if run["stats"]["failed_examples"]:
        lines += ["## Failed document examples", "", *[f"- `{r['doc_id']}`: {r['error']}" for r in run["stats"]["failed_examples"]], ""]
    (Path(out) / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
