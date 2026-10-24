"""Optional EDA for official queries and links, without downloading data."""
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .common import (domain_of, language_hint, normalize, parquet_batches,
                     write_csv, write_json)
from .corpus import valid_id


def resolve_file(root, explicit, name):
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    names = (name,) if isinstance(name, str) else name
    matches = sorted({p for candidate in names for p in Path(root).rglob(candidate)})
    if len(matches) > 1:
        raise ValueError(f"Multiple matching files for {names}; specify an explicit file path")
    return matches[0] if matches else None


def profile_queries(path, out):
    rows, seen, normalized, flags = [], Counter(), Counter(), Counter()
    for batch in parquet_batches(path):
        if not {"id", "query"}.issubset(batch.schema.names):
            raise ValueError(f"{path} must contain id and query columns")
        for row in batch.to_pylist():
            qid, query = row["id"], row["query"]
            if not valid_id(qid):
                flags["invalid_id"] += 1
            else:
                seen[qid] += 1
            if not isinstance(query, str):
                flags["invalid_query_type"] += 1
                query = ""
            norm = normalize(query)
            if not norm:
                flags["empty_query"] += 1
            else:
                normalized[norm] += 1
            rows.append((qid, query, len(query), len(query.split()), language_hint(query)))
    write_csv(out / "query_metrics.csv", ["id", "query", "characters", "whitespace_units", "language_hint"], rows)
    write_csv(out / "duplicate_queries.csv", ["normalized_query", "rows"],
              ((q, n) for q, n in normalized.most_common() if n > 1))
    lengths = sorted(r[2] for r in rows)
    words = sorted(r[3] for r in rows)
    return {"file": str(path), "rows": len(rows), "unique_valid_ids": len(seen),
            "expected_rows": 1200, "has_1200_unique_valid_ids": len(seen) == 1200 and len(rows) == 1200,
            "duplicate_id_groups": sum(n > 1 for n in seen.values()),
            "duplicate_normalized_query_groups": sum(n > 1 for n in normalized.values()),
            "quality_flags": dict(flags), "mean_characters": sum(lengths) / len(lengths) if lengths else None,
            "median_characters_lower": lengths[(len(lengths) - 1) // 2] if lengths else None,
            "max_characters": max(lengths) if lengths else None,
            "whitespace_units": {
                "min": min(words) if words else None,
                "p50_lower": words[int((len(words) - 1) * .5)] if words else None,
                "p95_lower": words[int((len(words) - 1) * .95)] if words else None,
                "max": max(words) if words else None,
            },
            "language_hints": dict(Counter(r[4] for r in rows))}


def profile_links(path, out):
    # Temporary on-disk tables bound RAM for potentially large official corpora.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="vibiomir-links-") as tmp:
        db = sqlite3.connect(str(Path(tmp) / "official.sqlite3"), uri=True)
        try:
            db.execute("CREATE TABLE official (doc_id INTEGER,url TEXT,domain TEXT)")
            total, invalid_ids, invalid_urls = 0, 0, 0
            domains = Counter()
            for batch in parquet_batches(path):
                if not {"id", "url"}.issubset(batch.schema.names):
                    raise ValueError(f"{path} must contain id and url columns")
                records = []
                for row in batch.to_pylist():
                    total += 1
                    url = row["url"] if isinstance(row["url"], str) else ""
                    domain = domain_of(url)
                    domains[domain or "missing"] += 1
                    invalid_urls += not (domain and url.lower().startswith(("http://", "https://")))
                    if valid_id(row["id"]):
                        records.append((row["id"], url, domain or "missing"))
                    else:
                        invalid_ids += 1
                db.executemany("INSERT INTO official VALUES (?,?,?)", records)
                db.commit()
            db.execute("CREATE INDEX idx_official_id ON official(doc_id)")
            unique = db.execute("SELECT COUNT(DISTINCT doc_id) FROM official").fetchone()[0]
            result = {"file": str(path), "rows": total, "unique_valid_ids": unique,
                      "invalid_ids": invalid_ids, "invalid_urls": invalid_urls,
                      "domains": len(domains),
                      "duplicate_id_extra_rows": total - invalid_ids - unique}
            write_csv(out / "official_domains.csv", ["domain", "rows"], domains.most_common())
            metrics_path = out / "metrics.sqlite3"
            if metrics_path.exists():
                db.execute("ATTACH DATABASE ? AS metrics", (metrics_path.resolve().as_uri() + "?mode=ro&immutable=1",))
                db.execute("""CREATE TEMP TABLE coverage AS
                    SELECT o.doc_id,o.domain,
                    EXISTS(SELECT 1 FROM metrics.documents d WHERE d.doc_id=o.doc_id) AS has_shard,
                    EXISTS(SELECT 1 FROM metrics.documents d WHERE d.doc_id=o.doc_id
                        AND d.text_hash IS NOT NULL AND CAST(d.status AS INTEGER) BETWEEN 200 AND 299) AS has_text,
                    EXISTS(SELECT 1 FROM official u JOIN metrics.documents d ON d.doc_id=u.doc_id
                        AND d.url=u.url WHERE u.doc_id=o.doc_id) AS has_matching_url
                    FROM (SELECT doc_id,MIN(domain) AS domain FROM official GROUP BY doc_id) o""")
                shard, text, mismatch = db.execute(
                    "SELECT COALESCE(SUM(has_shard),0),COALESCE(SUM(has_text),0),"
                    "COALESCE(SUM(has_shard AND NOT has_matching_url),0) FROM coverage"
                ).fetchone()
                result["official_ids_with_shard"] = shard
                result["official_ids_with_nonempty_2xx_text"] = text
                result["official_ids_with_only_mismatched_source_urls"] = mismatch
                result["shard_ids_outside_official_corpus"] = db.execute(
                    "SELECT COUNT(DISTINCT d.doc_id) FROM metrics.documents d WHERE NOT EXISTS "
                    "(SELECT 1 FROM official o WHERE o.doc_id=d.doc_id)"
                ).fetchone()[0]
                result["official_ids_without_shard"] = unique - result["official_ids_with_shard"]
                result["nonempty_2xx_id_coverage"] = result["official_ids_with_nonempty_2xx_text"] / unique if unique else None
                cursor = db.execute("""SELECT domain,COUNT(*) AS official_ids,
                    SUM(has_shard) AS ids_with_shard,SUM(has_text) AS ids_with_nonempty_2xx_text,
                    SUM(NOT has_shard) AS ids_without_shard,
                    SUM(has_shard AND NOT has_text) AS ids_with_shard_without_nonempty_2xx_text,
                    SUM(has_shard AND NOT has_matching_url) AS ids_with_mismatched_source_urls,
                    1.0*SUM(has_text)/COUNT(*) AS nonempty_2xx_id_coverage
                    FROM coverage GROUP BY domain ORDER BY ids_without_shard DESC,domain""")
                write_csv(out / "official_coverage_by_domain.csv", [d[0] for d in cursor.description], cursor)
                cursor = db.execute("""SELECT DISTINCT u.doc_id,u.url AS official_url,d.url AS crawled_url
                    FROM official u JOIN coverage c ON c.doc_id=u.doc_id
                    JOIN metrics.documents d ON d.doc_id=u.doc_id
                    WHERE c.has_shard AND NOT c.has_matching_url ORDER BY u.doc_id LIMIT 1000""")
                write_csv(out / "official_url_mismatch_examples.csv", [d[0] for d in cursor.description], cursor)
                cursor = db.execute(
                    "SELECT DISTINCT o.doc_id,o.url FROM official o WHERE NOT EXISTS "
                    "(SELECT 1 FROM metrics.documents d WHERE d.doc_id=o.doc_id) ORDER BY o.doc_id LIMIT 1000")
                write_csv(out / "missing_official_ids_examples.csv", ["doc_id", "url"], cursor)
                result["note"] = ("Coverage refers to the scanned shards; a partial run understates full coverage. "
                                  "Domain coverage counts unique IDs, using MIN(domain) for ambiguous duplicate official IDs. "
                                  "Source URL matching is exact; redirects/encoding differences need manual review.")
            return result
        finally:
            db.close()


def profile_references(data_dir, output_dir, query_file=None, links_file=None):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    query = resolve_file(data_dir, query_file, "query.parquet")
    links = resolve_file(data_dir, links_file, ("links_corpus_vi_en_zh.parquet", "links_corpus.parquet"))
    result = {
        "queries": profile_queries(query, out) if query else {"status": "missing", "expected_file": "query.parquet"},
        "official_links": profile_links(links, out) if links else {
            "status": "missing", "expected_file": "links_corpus_vi_en_zh.parquet (or links_corpus.parquet)"},
    }
    if query and links and query.parent == links.parent:
        manifest_path = query.parent / "provenance.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result["official_source"] = {k: manifest.get(k) for k in
                ["repository", "revision", "retrieved_at_vietnam", "metadata_url"]}
            result["official_source"]["manifest"] = str(manifest_path.resolve())
    write_json(out / "references_summary.json", result)
    return result
