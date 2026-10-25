"""Produce a readable Markdown report and standalone SVG charts from metrics."""
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import read_only_db


def table(columns, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(columns) + " |",
                       "| " + " | ".join("---" for _ in columns) + " |"] +
                     ["| " + " | ".join(cell(x) for x in row) + " |" for row in rows])


def chart(csv_path, svg_path, title, label_column, top=15):
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))[:top]
    width, height = 960, 90 + 30 * len(rows)
    maximum = max((int(r["rows"]) for r in rows), default=1) or 1
    items = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             f'<title>{html.escape(title)}</title>', '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="24" y="32" font-family="sans-serif" font-size="20">{html.escape(title)}</text>']
    for i, row in enumerate(rows):
        y, value = 65 + 30 * i, int(row["rows"])
        label = html.escape(row[label_column][:43])
        items.extend([f'<text x="24" y="{y + 15}" font-family="sans-serif" font-size="12">{label}</text>',
                      f'<rect x="345" y="{y}" width="{480 * value / maximum:.1f}" height="21" fill="#2563eb"/>',
                      f'<text x="{355 + 480 * value / maximum:.1f}" y="{y + 15}" font-family="sans-serif" font-size="12">{value:,}</text>'])
    svg_path.write_text("\n".join(items + ["</svg>"]), encoding="utf-8")


def build_report(output_dir):
    out = Path(output_dir)
    def load(name):
        p = out / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    inv, corpus, refs = (load(n) for n in ["inventory.json", "corpus_summary.json", "references_summary.json"])
    sections = ["# ViBioMIR data exploration report", "",
                f"Generated at (Vietnam time): {datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(timespec='seconds')}", "",
                "Local data analysis; no data downloads, model inference, or API calls. "
                "This report is rendered from saved analysis artifacts; its generation time is not a new corpus scan timestamp.", ""]
    if inv:
        sections += ["## Inventory", "", f"Source: `{inv['data_dir']}`.", "",
            table(["Metric", "Value"], [("Parquet files", inv["parquet_files"]),
                ("Parquet size (bytes)", inv["parquet_bytes"]),
                ("Metadata row count (all Parquet files)", inv["parquet_rows"]),
                ("Schema variants", len(inv["schemas"])), ("Inventory errors", len(inv["errors"]))]), "",
            "Schema and error details: [inventory.json](inventory.json); file inventory: [files.csv](files.csv).", ""]
    if corpus:
        sections += ["## Corpus", "", "**PARTIAL ANALYSIS**" if corpus["partial"] else "All discovered corpus shards were scanned.", "",
            table(["Metric", "Value"], [("Selected shards", corpus["selected_corpus_files"]),
                ("Discovered shards", corpus["available_corpus_files"]), ("Successfully read rows", corpus["rows"]),
                ("Distinct document IDs", corpus["unique_doc_ids"]),
                ("IDs with conflicting text versions", corpus["ids_with_conflicting_text"]),
                ("Shard read errors", len(corpus["errors"]))]), "",
            "### Quality (flags may overlap)", "",
            table(["Flag", "Rows"], corpus["quality_flags"].items()), "",
            "`short_text` uses the raw character count of nonempty text, with threshold " + str(corpus["short_chars"]) + ". "
            "`text_len_mismatch` compares `text_len` with Python `len(text)`. HTML/block-page flags are heuristics requiring sample review.", "",
            "Examples for reviewing the original records: [quality_examples.csv](quality_examples.csv).", "",
            "### Length distributions", "",
            table(["Unit", "Min", "P50", "P90", "P99", "Max", "Mean"],
                  [(name, *[corpus[key][k] for k in ["min", "p50", "p90", "p99", "max", "mean"]])
                   for name, key in [("Characters", "characters"), ("Whitespace units", "whitespace_units")]]), "",
            "Quantiles use the element at floor((n−1)×q) in the sorted sequence. Whitespace units "
            "**are not model/LCS tokens** and should not be used to compare information volume between Chinese and Vietnamese/English.", "",
            "### Duplicate records", "", table(["Key", "Duplicate groups", "Extra rows"],
                [(k, v["groups"], v["extra_rows"]) for k, v in corpus["duplicates"].items()]), "",
            "Extra rows = sum(group size − 1). SHA-256 hashes cover the entire text after "
            "NFKC → HTML unescape → lowercase → whitespace collapse. Empty text is excluded from content duplicate counts. "
            "URLs are compared verbatim without URL normalization. Content matches are exact after normalization; near-duplicates are not detected. "
            "Duplicate CSVs contain at most 1,000 groups; JSON totals cover the entire scanned scope.", "",
            "### Language hints", "", table(["Heuristic", "Rows"], corpus["language_hints"].items()), "",
            "The heuristic examines at most the first 4,000 characters: CJK share ≥20% → `cjk_likely_zh`; "
            "Vietnamese-specific diacritics → `vi_diacritics`; substantial ASCII Latin text → `latin_undetermined`. "
            "CJK does not confirm Chinese; Latin does not confirm English. "
            "No language identification model is used, and domains are not treated as language labels.", "",
            "### Crawl-state reconciliation", "",
            table(["Group", "State IDs", "State IDs without shards", "Shard IDs without state", "HTTP 2xx IDs without nonempty 2xx text"],
                  [(r["member"], r["state_ids"], r["state_ids_without_shard"], r["shard_ids_without_state"],
                    r["state_2xx_ids_without_nonempty_2xx_text"]) for r in corpus["crawler_reconciliation"]]), "",
            "IDs are reconciled within each A/B/C group. The crawl-state SQLite database is not the official document list; "
            "HTTP 2xx does not establish content correctness. Missing-shard counts from a sample scan do not describe the full corpus. "
            "A `last_run` log describes only the latest run.", ""]
        if corpus["inspect_blocks"]:
            with read_only_db(out / "metrics.sqlite3") as db:
                block_stats = db.execute(
                    "SELECT COUNT(n_blocks),SUM(n_blocks),AVG(n_blocks),MAX(n_blocks) FROM documents"
                ).fetchone()
            sections += ["### Content block structure", "",
                table(["Metric", "Value"], zip(
                    ["Rows with valid blocks", "Total blocks", "Mean blocks per row with valid blocks", "Maximum blocks"],
                    block_stats)), "",
                "Crawler content blocks are not automatically retrieval chunks. A valid structure is a list of "
                "string pairs `[block_kind, text]`; EDA does not modify block text.", ""]
        for csv_name, svg_name, title, label in [
            ("by_domain.csv", "top_domains.svg", "Top domains by corpus rows", "domain"),
            ("by_status.csv", "status.svg", "HTTP status by corpus rows", "status"),
            ("length_histogram.csv", "lengths.svg", "Text length in characters", "character_range"),
        ]:
            chart(out / csv_name, out / svg_name, title, label)
            sections += [f"![{title}]({svg_name})", ""]
    sections += ["## Queries and official document list", ""]
    official_source = refs.get("official_source")
    if official_source:
        sections += [f"Source: [{official_source['repository']}]"
                     f"(https://huggingface.co/datasets/{official_source['repository']}); "
                     f"revision `{official_source['revision']}`. "
                     f"Retrieved at: {official_source['retrieved_at_vietnam']}. "
                     f"Local manifest: `{official_source['manifest']}`.", ""]
    for key, title in [("queries", "Queries"), ("official_links", "Document list")]:
        value = refs.get(key, {})
        sections += [f"### {title}", ""]
        if not value or value.get("status") == "missing":
            sections += [f"Missing `{value.get('expected_file', key)}`. This section cannot be checked yet.", ""]
        else:
            sections += [table(["Metric", "Value"], [(k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
                                                       for k, v in value.items()]), ""]
    official_links = refs.get("official_links", {})
    if "nonempty_2xx_id_coverage" in official_links:
        ratio = official_links["nonempty_2xx_id_coverage"]
        if ratio is not None:
            sections += [f"Coverage of IDs with at least one nonempty HTTP 2xx text version: **{ratio:.2%}**. "
                         "This is acquisition coverage; it does not establish content correctness or retrieval Recall.", ""]
    coverage_path = out / "official_coverage_by_domain.csv"
    if coverage_path.exists() and official_links.get("status") != "missing":
        with coverage_path.open(encoding="utf-8-sig", newline="") as stream:
            coverage = list(csv.DictReader(stream))
        missing = [r for r in coverage if int(r["ids_without_shard"]) > 0][:15]
        sections += ["### Domains with missing IDs relative to the official list", "",
            table(["Domain", "Official IDs", "IDs without shards", "IDs with nonempty HTTP 2xx text"],
                  [(r["domain"], r["official_ids"], r["ids_without_shard"],
                    r["ids_with_nonempty_2xx_text"]) for r in missing]), "",
            "Up to 15 domains, ranked by missing ID count. Full statistics: "
            "[official_coverage_by_domain.csv](official_coverage_by_domain.csv). "
            "Source URLs without verbatim matches: [official_url_mismatch_examples.csv](official_url_mismatch_examples.csv).", ""]
    query_units = refs.get("queries", {}).get("whitespace_units")
    if query_units:
        sections += ["Query length here uses `str.split()`, with quantiles at floor((n−1)×q). "
                     "The dataset card reports a word-count range of 5–283; the analyzed file yields "
                     f"{query_units['min']}–{query_units['max']} "
                     "under this counting method. The published tokenization method is unspecified; query text is preserved.", ""]
    references_ready = all(refs.get(k, {}).get("file") for k in ["queries", "official_links"])
    next_step = ("- Review missing IDs and sources without usable text in the official coverage statistics."
                 if references_ready else
                 "- Provide `query.parquet` and `links_corpus_vi_en_zh.parquet` to check queries and official ID coverage.")
    sections += ["## Limitations and next steps", "",
        "- Review quality flags by domain before filtering; EDA does not remove or modify source records.",
        next_step,
        "- Preserve original IDs when handling duplicate content across documents.",
        "- Reference relevance labels are unavailable, so Precision/Recall/F2 and retrieval quality are not assessed.",
        "- `--inspect-blocks` adds block counts and JSON/structure flags; empty blocks are not treated as malformed JSON.", "",
        "Detailed data: [corpus_summary.json](corpus_summary.json), "
        "[references_summary.json](references_summary.json), [metrics.sqlite3](metrics.sqlite3).", ""]
    (out / "REPORT.md").write_text("\n".join(sections), encoding="utf-8")
