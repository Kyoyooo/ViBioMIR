"""Stream corpus text; keep compact per-row metrics in a local SQLite artifact."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq

from .common import (BLOCK_PAGE, HTML_TAG, domain_of, language_hint, normalize,
                     parquet_batches, write_csv, write_json)

FLAGS = {
    "missing_or_invalid_id": 1, "empty_text": 2, "short_text": 4,
    "missing_title": 8, "invalid_url": 16, "non_2xx_status": 32,
    "text_len_mismatch": 64, "html_in_text": 128, "replacement_character": 256,
    "possible_block_page": 512, "invalid_blocks": 1024, "missing_text_column": 2048,
}
METRIC_COLUMNS = ["doc_id", "member", "domain", "status", "url", "text_hash",
                  "n_chars", "n_ws_tokens", "n_lines", "language_hint", "method",
                  "source", "flags", "n_blocks", "shard"]


def valid_id(value):
    return isinstance(value, int) and not isinstance(value, bool)


def metrics(row, member, shard, short_chars, inspect_blocks):
    text = row.get("text") or ""
    if not isinstance(text, str):
        raise ValueError(f"Non-string text in {shard}")
    normalized = normalize(text)
    url = row.get("url") or ""
    status = str(row.get("status") or "missing")
    doc_id = row.get("id")
    flags = 0
    checks = {
        "missing_or_invalid_id": not valid_id(doc_id),
        "missing_text_column": "text" not in row,
        "empty_text": not normalized,
        "short_text": bool(normalized) and len(text) < short_chars,
        "missing_title": not str(row.get("title") or "").strip(),
        "invalid_url": not url.lower().startswith(("https://", "http://")) or not domain_of(url),
        "non_2xx_status": not (status.isdigit() and 200 <= int(status) < 300),
        "text_len_mismatch": row.get("text_len") is not None and row["text_len"] != len(text),
        "html_in_text": bool(HTML_TAG.search(text)),
        "replacement_character": "\ufffd" in text,
        # Only short texts: mentions inside long medical articles are too ambiguous.
        "possible_block_page": len(text) < 3000 and bool(BLOCK_PAGE.search(text)),
    }
    n_blocks = None
    if inspect_blocks and row.get("blocks"):
        try:
            blocks = json.loads(row["blocks"]) if isinstance(row["blocks"], str) else row["blocks"]
            if not isinstance(blocks, list) or any(
                not isinstance(b, list) or len(b) != 2
                or not all(isinstance(x, str) for x in b) for b in blocks
            ):
                raise ValueError("Expected a list of [kind, text] pairs")
            n_blocks = len(blocks)
        except (ValueError, TypeError):
            checks["invalid_blocks"] = True
    for key, failed in checks.items():
        if failed:
            flags |= FLAGS[key]
    return (doc_id if valid_id(doc_id) else None, member,
            row.get("domain") or domain_of(url), status, url,
            hashlib.sha256(normalized.encode("utf-8")).digest() if normalized else None,
            len(text), len(text.split()), len(text.splitlines()), language_hint(text),
            row.get("method") or "missing", row.get("source") or "missing", flags,
            n_blocks, shard)


def export_query(db, out, name, sql, params=()):
    cursor = db.execute(sql, params)
    write_csv(out / name, [d[0] for d in cursor.description], cursor)


def distribution(db, column, where="1"):
    count, minimum, maximum, mean = db.execute(
        f"SELECT COUNT(*),MIN({column}),MAX({column}),AVG({column}) FROM documents WHERE {where}"
    ).fetchone()
    result = {"count": count, "min": minimum, "max": maximum, "mean": mean}
    for name, q in [("p25", .25), ("p50", .5), ("p75", .75), ("p90", .9), ("p95", .95), ("p99", .99)]:
        result[name] = db.execute(
            f"SELECT {column} FROM documents WHERE {where} ORDER BY {column} LIMIT 1 OFFSET ?",
            (int((count - 1) * q),)
        ).fetchone()[0] if count else None
    return result


def crawler_reconciliation(db, root, out):
    results = []
    for path in sorted(root.rglob("state_*.sqlite3")):
        member = path.stem.removeprefix("state_")
        # ATTACH URI preserves the read-only/immutable source connection mode.
        uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
        db.execute("ATTACH DATABASE ? AS crawl", (uri,))
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM crawl.sqlite_master WHERE type='table'")}
            if "crawl_state" not in tables:
                continue
            result = {"member": member, "database": str(path)}
            result["state_ids"] = db.execute("SELECT COUNT(*) FROM crawl.crawl_state").fetchone()[0]
            result["state_ids_without_shard"] = db.execute(
                "SELECT COUNT(*) FROM crawl.crawl_state s WHERE NOT EXISTS "
                "(SELECT 1 FROM documents d WHERE d.doc_id=s.id AND d.member=?)", (member,)
            ).fetchone()[0]
            result["shard_ids_without_state"] = db.execute(
                "SELECT COUNT(DISTINCT d.doc_id) FROM documents d WHERE d.member=? AND NOT EXISTS "
                "(SELECT 1 FROM crawl.crawl_state s WHERE s.id=d.doc_id)", (member,)
            ).fetchone()[0]
            result["state_2xx_ids_without_nonempty_2xx_text"] = db.execute(
                "SELECT COUNT(*) FROM crawl.crawl_state s WHERE CAST(s.status AS INTEGER) BETWEEN 200 AND 299 "
                "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.doc_id=s.id AND d.member=? "
                "AND d.text_hash IS NOT NULL AND CAST(d.status AS INTEGER) BETWEEN 200 AND 299)", (member,)
            ).fetchone()[0]
            results.append(result)
        finally:
            db.execute("DETACH DATABASE crawl")
    write_json(out / "crawl_reconciliation.json", results)
    return results


def profile_corpus(data_dir, output_dir, batch_size=512, max_files=None,
                   short_chars=200, inspect_blocks=False):
    root, out = Path(data_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "metrics.sqlite3").exists():
        raise FileExistsError(f"{out / 'metrics.sqlite3'} already exists; choose a new --output-dir")
    files = []
    errors = []
    for path in sorted(root.rglob("*.parquet")):
        try:
            with pq.ParquetFile(path) as pf:
                names = set(pf.schema_arrow.names)
            if "text" in names or "shards" in path.relative_to(root).parts:
                files.append(path)
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})
    available = len(files)
    if max_files is not None:
        files = files[:max_files]
    selected = ["id", "domain", "url", "status", "text_len", "title", "text", "method", "source"]
    if inspect_blocks:
        selected.append("blocks")
    db = sqlite3.connect(out / "metrics.sqlite3", uri=True)
    try:
        db.execute("PRAGMA cache_size=-32768")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("""CREATE TABLE documents (
            doc_id INTEGER, member TEXT, domain TEXT, status TEXT, url TEXT,
            text_hash BLOB, n_chars INTEGER, n_ws_tokens INTEGER, n_lines INTEGER,
            language_hint TEXT, method TEXT, source TEXT, flags INTEGER,
            n_blocks INTEGER, shard TEXT)""")
        insert = "INSERT INTO documents VALUES (" + ",".join("?" for _ in METRIC_COLUMNS) + ")"
        for i, path in enumerate(files, 1):
            parts = path.relative_to(root).parts
            member = parts[parts.index("shards") + 1] if "shards" in parts else "unassigned"
            shard = str(path.relative_to(root))
            try:
                # A failed shard is rolled back completely; error is exposed in summary.
                with db:
                    for batch in parquet_batches(path, selected, batch_size):
                        db.executemany(insert, (metrics(row, member, shard, short_chars, inspect_blocks)
                                                for row in batch.to_pylist()))
            except Exception as exc:
                errors.append({"file": str(path), "error": str(exc)})
            if i % 50 == 0 or i == len(files):
                print(f"Corpus: {i}/{len(files)} shards", flush=True)
        print("Aggregating metrics and duplicate groups…", flush=True)
        for column in ["doc_id,member", "text_hash", "url", "n_chars", "n_ws_tokens"]:
            name = column.replace(",", "_")
            db.execute(f"CREATE INDEX idx_{name} ON documents ({column})")
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        quality = {k: db.execute("SELECT COUNT(*) FROM documents WHERE flags & ? != 0", (v,)).fetchone()[0]
                   for k, v in FLAGS.items() if inspect_blocks or k != "invalid_blocks"}
        summary = {"rows": count, "unique_doc_ids": db.execute("SELECT COUNT(DISTINCT doc_id) FROM documents").fetchone()[0],
                   "available_corpus_files": available, "selected_corpus_files": len(files),
                   "partial": len(files) < available or bool(errors), "errors": errors,
                   "short_chars": short_chars, "inspect_blocks": inspect_blocks,
                   "quality_flags": quality, "duplicates": {},
                   "characters": distribution(db, "n_chars"),
                   "whitespace_units": distribution(db, "n_ws_tokens"),
                   "language_hints": dict(db.execute("SELECT language_hint,COUNT(*) FROM documents GROUP BY language_hint"))}
        for key, column, condition in [
            ("id", "doc_id", "doc_id IS NOT NULL"),
            ("url", "url", "url != ''"),
            ("normalized_text_sha256", "text_hash", "text_hash IS NOT NULL")
        ]:
            groups, extra = db.execute(
                f"SELECT COUNT(*),COALESCE(SUM(n-1),0) FROM "
                f"(SELECT COUNT(*) AS n FROM documents WHERE {condition} GROUP BY {column} HAVING COUNT(*)>1)"
            ).fetchone()
            summary["duplicates"][key] = {"groups": groups, "extra_rows": extra}
            display = f"hex({column})" if column == "text_hash" else column
            export_query(db, out, f"duplicate_{key}.csv",
                f"SELECT {display} AS duplicate_key,COUNT(*) AS rows,COUNT(DISTINCT doc_id) AS distinct_ids,"
                f"MIN(doc_id) AS example_doc_id FROM documents WHERE {condition} GROUP BY {column} "
                f"HAVING COUNT(*)>1 ORDER BY rows DESC,duplicate_key LIMIT 1000")
        summary["ids_with_conflicting_text"] = db.execute(
            "SELECT COUNT(*) FROM (SELECT doc_id FROM documents WHERE doc_id IS NOT NULL "
            "GROUP BY doc_id HAVING COUNT(DISTINCT text_hash)>1)"
        ).fetchone()[0]
        for key in ["domain", "member", "status", "method", "source", "language_hint"]:
            export_query(db, out, f"by_{key}.csv",
                f"SELECT {key},COUNT(*) AS rows,COUNT(DISTINCT doc_id) AS unique_ids,"
                "SUM(text_hash IS NOT NULL) AS nonempty_rows,AVG(n_chars) AS mean_chars,"
                "SUM(CAST(status AS INTEGER) BETWEEN 200 AND 299) AS http_2xx_rows "
                f"FROM documents GROUP BY {key} ORDER BY rows DESC,{key}")
        export_query(db, out, "by_domain_status.csv",
            "SELECT domain,status,COUNT(*) AS rows FROM documents GROUP BY domain,status ORDER BY rows DESC")
        export_query(db, out, "by_domain_language.csv",
            "SELECT domain,language_hint,COUNT(*) AS rows FROM documents GROUP BY domain,language_hint ORDER BY rows DESC")
        export_query(db, out, "length_histogram.csv", """SELECT
            CASE WHEN n_chars=0 THEN '0' WHEN n_chars<200 THEN '1-199'
            WHEN n_chars<1000 THEN '200-999' WHEN n_chars<3000 THEN '1000-2999'
            WHEN n_chars<10000 THEN '3000-9999' ELSE '10000+' END AS character_range,
            COUNT(*) AS rows FROM documents GROUP BY character_range ORDER BY MIN(n_chars)""")
        examples = []
        for flag, bit in FLAGS.items():
            if flag == "invalid_blocks" and not inspect_blocks:
                continue
            examples.extend((flag, *row) for row in db.execute(
                "SELECT doc_id,domain,status,n_chars,url,shard FROM documents WHERE flags & ? != 0 LIMIT 20", (bit,)))
        write_csv(out / "quality_examples.csv", ["flag", "doc_id", "domain", "status", "n_chars", "url", "shard"], examples)
        summary["crawler_reconciliation"] = crawler_reconciliation(db, root, out)
        write_json(out / "corpus_summary.json", summary)
        return summary
    finally:
        db.close()
