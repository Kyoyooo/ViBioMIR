"""Resumable offline S00 audit. Usage: python -m exploration.audit --help."""
import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from itertools import zip_longest
import json
import multiprocessing
from pathlib import Path
import sqlite3
import time

import pyarrow
import pyarrow.parquet as pq

from .audit_checks import AUDIT_VERSION, digest, dumps, inspect_record
from .common import parquet_batches

COLUMNS = ["id", "domain", "url", "status", "final_url", "ts", "text_len", "ver", "source",
           "title", "desc", "canonical", "method", "blocks", "text", "authors",
           "published_date", "updated_date", "category", "abstract"]


def sha256_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def discover_inputs(root):
    """Catalog every local file; never open source SQLite or expand archives."""
    items = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        item = {"path": relative, "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                "kind": "other", "metadata_rows": None, "row_group_rows": [], "schema": None,
                "columns": [], "sha256": None, "state": "cataloged_only", "error": None}
        if path.is_symlink():
            item.update(kind="symlink", state="not_inspected", error="Symlinks are not followed as corpus inputs")
        elif path.suffix.lower() == ".parquet":
            item["kind"] = "corpus"
            try:
                with pq.ParquetFile(path) as pf:
                    item.update(metadata_rows=pf.metadata.num_rows,
                        row_group_rows=[pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups)],
                        schema=str(pf.schema_arrow), columns=pf.schema_arrow.names, state="footer_read")
                    if "shards" not in path.parts and not {"text", "blocks", "status"}.intersection(pf.schema_arrow.names):
                        item["kind"] = "reference_parquet"
            except Exception as exc:
                item.update(state="footer_failed", error=f"{type(exc).__name__}: {exc}")
        elif path.suffix in (".sqlite3", ".db"):
            item.update(kind="sqlite", state="not_inspected",
                        read_policy="Metadata catalog only; source SQLite is never opened",
                        wal_present=Path(str(path) + "-wal").exists())
        elif path.suffix in (".zip", ".gz", ".tar", ".tgz"):
            item.update(kind="archive", state="not_expanded")
        elif path.suffix == ".json":
            item["kind"] = "json"
        items.append(item)
    return items


def build_manifest(root, shard_paths=None, max_files=None, short_chars=200, sample_size=600, seed=42):
    items = discover_inputs(root)
    eligible = [x["path"] for x in items if x["kind"] == "corpus"]
    selected = list(eligible)
    if shard_paths is not None:
        selected = sorted(set(shard_paths))
        if not selected or set(selected) - set(eligible):
            raise ValueError("Shard selection must contain discovered corpus paths relative to --data-dir")
    if max_files is not None:
        selected = selected[:max_files]
    if not eligible:
        raise ValueError("No corpus Parquet files found in the input scope")
    selection = set(selected)
    for item in items:
        item["selected"] = item["path"] in selection
        if item["selected"]:
            path = root / item["path"]
            before = path.stat()
            item["sha256"] = sha256_file(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"Input changed during fingerprinting: {item['path']}")
    code_paths = [Path(__file__), Path(__file__).with_name("audit_checks.py"),
                  Path(__file__).with_name("audit_report.py"), Path(__file__).with_name("common.py"),
                  Path(__file__).with_name("corpus.py")]
    code_hash = digest(dumps({p.name: sha256_file(p) for p in code_paths}))
    config = {"audit_version": AUDIT_VERSION, "audit_code_sha256": code_hash,
              "pyarrow_version": pyarrow.__version__, "short_chars": short_chars,
              "sample_size": sample_size, "seed": seed, "selected_paths": selected}
    return {"created_at_utc": datetime.now(timezone.utc).isoformat(), "data_dir": str(root),
        "config": config, "config_sha256": digest(dumps(config)), "files": items,
        "discovered_corpus_files": len(eligible), "selected_corpus_files": len(selected),
        "scope": "all_local_corpus" if len(selected) == len(eligible) else "selected_shards_only",
        "fingerprint_scope": "SHA-256 for selected corpus files; size/mtime/schema catalog for others",
        "source_network_access": False, "source_sqlite_policy": "catalog_only_no_source_connection"}


@contextmanager
def output_lock(out):
    with (out / ".audit.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another writer holds this audit output directory") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def open_database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA cache_size=-32768")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS files (
      shard TEXT PRIMARY KEY, state TEXT NOT NULL, error TEXT, final_sha256 TEXT);
    CREATE TABLE IF NOT EXISTS row_groups (
      shard TEXT, group_id INTEGER, rows_read INTEGER, state TEXT, error TEXT,
      PRIMARY KEY(shard,group_id));
    CREATE TABLE IF NOT EXISTS records (
      record_id TEXT PRIMARY KEY, shard TEXT, row_group INTEGER, row_offset INTEGER,
      doc_id INTEGER, domain TEXT, status TEXT, verbatim_sha256 TEXT, normalized_sha256 TEXT,
      disposition TEXT, language_hint TEXT, n_chars INTEGER, sample_key TEXT, details TEXT,
      UNIQUE(shard,row_group,row_offset));
    CREATE TABLE IF NOT EXISTS template_occurrences (
      shard TEXT, row_group INTEGER, domain TEXT, signature TEXT, kind TEXT, example TEXT,
      records INTEGER, PRIMARY KEY(shard,row_group,domain,signature));
    CREATE INDEX IF NOT EXISTS idx_records_doc ON records(doc_id);
    CREATE INDEX IF NOT EXISTS idx_records_text ON records(normalized_sha256);
    CREATE INDEX IF NOT EXISTS idx_records_domain_sample ON records(domain,sample_key);
    """)
    return db


def iter_group(path, group_id, batch_size, has_html):
    """Read large HTML separately, keeping row alignment and original row offsets."""
    base = parquet_batches(path, COLUMNS, batch_size, [group_id])
    if not has_html:
        for batch in base:
            yield batch.to_pylist()
        return
    markup = parquet_batches(path, ["html_trim"], batch_size, [group_id])
    for left, right in zip_longest(base, markup):
        if left is None or right is None or left.num_rows != right.num_rows:
            raise ValueError("HTML and content batches lost row alignment")
        rows = left.to_pylist()
        for row, value in zip(rows, right.column(0).to_pylist()):
            row["html_trim"] = value
        yield rows


def failed_record(row, shard, checksum, group_id, offset, exc):
    """Retain a locator when a single record's inspection fails unexpectedly."""
    return {"record_id": digest(f"{checksum}\0{shard}\0{group_id}\0{offset}"), "shard": shard,
        "source_sha256": checksum, "row_group": group_id, "row_offset": offset,
        "doc_id": None, "doc_id_raw": str(row.get("id")), "domain": "unknown",
        "url": str(row.get("url") or ""), "status": str(row.get("status") or "missing"),
        "n_chars": None, "disposition": "needs_review", "language_hint": "unknown",
        "flags": ["record_processing_error"], "reasons": [f"{type(exc).__name__}: {exc}"[:500]],
        "review_status": "unreviewed", "snippet": str(row.get("text") or "")[:600]}


def save_record(db, record, seed):
    fields = ["record_id", "shard", "row_group", "row_offset", "doc_id", "domain", "status",
              "verbatim_sha256", "normalized_sha256", "disposition", "language_hint", "n_chars"]
    db.execute("INSERT INTO records VALUES (" + ",".join("?" for _ in range(14)) + ")",
               [record.get(k) for k in fields] + [digest(f"{seed}:{record['record_id']}"), dumps(record)])


def inspect_batch(rows, shard, checksum, group_id, offset, short_chars):
    """Pure worker computation; source reads and checkpoint writes stay in the parent."""
    results = []
    for index, row in enumerate(rows, offset):
        try:
            results.append(inspect_record(row, shard, checksum, group_id, index, short_chars))
        except Exception as exc:
            results.append((failed_record(row, shard, checksum, group_id, index, exc), []))
    return results


def prepared_batches(path, group_id, batch_size, has_html, shard, checksum, short_chars, executor, workers):
    batches = iter_group(path, group_id, batch_size, has_html)
    offset = 0
    pending = deque()
    for rows in batches:
        args = (rows, shard, checksum, group_id, offset, short_chars)
        offset += len(rows)
        if executor is None:
            yield inspect_batch(*args)
        else:
            pending.append(executor.submit(inspect_batch, *args))
            if len(pending) >= workers * 2:
                yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def run_audit(data_dir, output_dir, *, shard_paths=None, max_files=None, batch_size=32,
              short_chars=200, sample_size=600, seed=42, resume=False, stop_after_groups=None,
              dry_run=False, workers=1):
    """Audit selected local shards, committing complete row groups atomically."""
    started = time.monotonic()
    if min(batch_size, short_chars, sample_size, workers) < 1 or (max_files is not None and max_files < 1):
        raise ValueError("Batch size, thresholds, sample size, and max-files must be positive")
    if stop_after_groups is not None and stop_after_groups < 1:
        raise ValueError("stop-after-groups must be positive")
    root, out = Path(data_dir).resolve(), Path(output_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"Input directory does not exist: {root}")
    if out == root or root in out.parents:
        raise ValueError("Audit output must be outside the source data directory")
    manifest = build_manifest(root, shard_paths, max_files, short_chars, sample_size, seed)
    if dry_run:
        return manifest
    if not resume and out.exists() and any(out.iterdir()):
        raise FileExistsError("Choose an empty output directory, or use --resume for the same audit")
    if resume and not ((out / "input_manifest.json").is_file() and (out / "audit.sqlite3").is_file()):
        raise ValueError("--resume requires an existing audit manifest and database")
    out.mkdir(parents=True, exist_ok=True)
    with output_lock(out):
        if not resume and any(p.name != ".audit.lock" for p in out.iterdir()):
            raise FileExistsError("Output was populated before the writer lock was acquired; choose a new directory")
        if resume:
            previous = json.loads((out / "input_manifest.json").read_text())
            old_selected = {x["path"]: x["sha256"] for x in previous["files"] if x["selected"]}
            new_selected = {x["path"]: x["sha256"] for x in manifest["files"] if x["selected"]}
            if (previous["data_dir"] != manifest["data_dir"] or
                    previous["config_sha256"] != manifest["config_sha256"] or old_selected != new_selected):
                raise ValueError("Input/config/code fingerprint changed; start a new audit directory")
            if [x["path"] for x in previous["files"]] != [x["path"] for x in manifest["files"]]:
                raise ValueError("Input inventory membership changed; start a new audit directory")
            manifest = previous
        else:
            atomic_json(out / "input_manifest.json", manifest)
        db = open_database(out / "audit.sqlite3")
        executor = (ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
                    if workers > 1 else None)
        state, completed_this_call = "finished", 0
        try:
            selected = [x for x in manifest["files"] if x["selected"]]
            for index, item in enumerate(selected, 1):
                shard, checksum = item["path"], item["sha256"]
                path = root / shard
                prior = db.execute("SELECT state FROM files WHERE shard=?", (shard,)).fetchone()
                if prior and prior["state"] == "complete":
                    continue
                if sha256_file(path) != checksum:
                    raise ValueError(f"Input changed before scanning: {shard}")
                with db:
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                               (shard, "scanning", None, None))
                if item["error"]:
                    with db:
                        db.execute("UPDATE files SET state='failed',error=? WHERE shard=?", (item["error"], shard))
                    continue
                for group_id, expected_rows in enumerate(item["row_group_rows"]):
                    previous_group = db.execute("SELECT state FROM row_groups WHERE shard=? AND group_id=?",
                                                (shard, group_id)).fetchone()
                    if previous_group and previous_group["state"] == "complete":
                        continue
                    offset = 0
                    try:
                        with db:
                            db.execute("DELETE FROM records WHERE shard=? AND row_group=?", (shard, group_id))
                            db.execute("DELETE FROM template_occurrences WHERE shard=? AND row_group=?", (shard, group_id))
                            for results in prepared_batches(path, group_id, batch_size, "html_trim" in item["columns"],
                                    shard, checksum, short_chars, executor, workers):
                                for record, templates in results:
                                    save_record(db, record, seed)
                                    db.executemany("""INSERT INTO template_occurrences VALUES (?,?,?,?,?,?,1)
                                      ON CONFLICT(shard,row_group,domain,signature) DO UPDATE SET records=records+1""",
                                        [(shard, group_id, *t) for t in templates])
                                    offset += 1
                            if offset != expected_rows:
                                raise ValueError(f"Row count mismatch: expected {expected_rows}, read {offset}")
                            db.execute("INSERT OR REPLACE INTO row_groups VALUES (?,?,?,'complete',NULL)",
                                       (shard, group_id, offset))
                    except Exception as exc:
                        with db:
                            db.execute("INSERT OR REPLACE INTO row_groups VALUES (?,?,0,'failed',?)",
                                       (shard, group_id, f"{type(exc).__name__}: {exc}"))
                    completed_this_call += 1
                    if stop_after_groups is not None and completed_this_call >= stop_after_groups:
                        state = "paused"
                        break
                final_hash = sha256_file(path)
                if final_hash != checksum:
                    with db:
                        db.execute("DELETE FROM records WHERE shard=?", (shard,))
                        db.execute("DELETE FROM template_occurrences WHERE shard=?", (shard,))
                        db.execute("DELETE FROM row_groups WHERE shard=?", (shard,))
                        db.execute("UPDATE files SET state='unstable_input',error=?,final_sha256=? WHERE shard=?",
                                   ("Checksum changed during scan; derived rows discarded", final_hash, shard))
                else:
                    successful = db.execute("SELECT COUNT(*) FROM row_groups WHERE shard=? AND state='complete'", (shard,)).fetchone()[0]
                    file_state = "complete" if successful == len(item["row_group_rows"]) else "partial"
                    with db:
                        db.execute("UPDATE files SET state=?,final_sha256=? WHERE shard=?", (file_state, final_hash, shard))
                print(f"Audit: {index}/{len(selected)} selected shards; {shard}", flush=True)
                if state == "paused":
                    break
        except KeyboardInterrupt:
            db.rollback()
            state = "interrupted"
        except Exception:
            db.rollback()
            state = "failed"
            raise
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            from .audit_report import render_audit
            try:
                summary = render_audit(db, out, manifest, state, time.monotonic() - started)
            finally:
                db.close()
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline, resumable S00 content audit; source files stay read-only")
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--shard-list", type=Path, help="JSON array of corpus paths relative to --data-dir")
    parser.add_argument("--max-files", type=int, help="Scan first N sorted corpus files; explicitly partial")
    parser.add_argument("--batch-size", type=int, default=32, help="Rows per batch, not a strict byte/RAM limit")
    parser.add_argument("--short-chars", type=int, default=200, help="Review signal only; never a deletion threshold")
    parser.add_argument("--sample-size", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1, help="CPU inspection processes; checkpoint writes remain single-writer")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after-groups", type=int, help="Commit at most N new row-group attempts, then pause")
    parser.add_argument("--dry-run", action="store_true", help="Inventory/fingerprint selected files without scanning rows or writing outputs")
    args = parser.parse_args(argv)
    try:
        paths = json.loads(args.shard_list.read_text()) if args.shard_list else None
        if paths is not None and (not isinstance(paths, list) or not all(isinstance(p, str) for p in paths)):
            raise ValueError("--shard-list must be a JSON array of relative path strings")
        result = run_audit(args.data_dir, args.output_dir, shard_paths=paths, max_files=args.max_files,
            batch_size=args.batch_size, short_chars=args.short_chars, sample_size=args.sample_size,
            seed=args.seed, resume=args.resume, stop_after_groups=args.stop_after_groups, dry_run=args.dry_run,
            workers=args.workers)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(dumps({k: result[k] for k in ("scope", "discovered_corpus_files", "selected_corpus_files", "config")}))
    else:
        print(f"Audit {result['run_state']}: {result['records_read']} rows; partial={result['partial']}. Report: {args.output_dir / 'REPORT.md'}")


if __name__ == "__main__":
    main()
