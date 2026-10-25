"""Stream audit exports and produce explicit-scope reports and review packets."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import time

import pyarrow as pa
import pyarrow.parquet as pq

from .audit_checks import dumps
from .common import write_csv
from .report import table

STRING_FIELDS = ("record_id", "shard", "source_sha256", "doc_id_raw", "domain", "url", "status",
                 "text_state", "verbatim_sha256", "normalized_sha256", "language_hint", "doc_type_hint",
                 "block_state", "html_state", "disposition", "snippet", "snippet_origin", "review_status")
INT_FIELDS = ("row_group", "row_offset", "doc_id", "n_chars", "n_ws_tokens", "n_lines", "n_blocks",
              "html_chars", "html_visible_chars")
JSON_FIELDS = ("script_ratios", "reasons", "flags", "metadata", "structure", "signals")
RECORD_SCHEMA = pa.schema([(k, pa.string()) for k in STRING_FIELDS] +
    [(k, pa.int64()) for k in INT_FIELDS] + [("blocks_text_match", pa.bool_()),
    ("has_recovery_material", pa.bool_())] + [(k + "_json", pa.string()) for k in JSON_FIELDS])


def export_record(record):
    result = {k: record.get(k) for k in (*STRING_FIELDS, *INT_FIELDS, "blocks_text_match", "has_recovery_material")}
    result.update({k + "_json": dumps(record.get(k)) for k in JSON_FIELDS})
    return result


def export_sql_parquet(db, out, name, sql, schema):
    temp = out / (name + ".tmp")
    with pq.ParquetWriter(temp, schema, compression="zstd") as writer:
        cursor = db.execute(sql)
        while rows := cursor.fetchmany(512):
            writer.write_table(pa.Table.from_pylist([dict(r) for r in rows], schema=schema))
    temp.replace(out / name)


def allocate_samples(counts, budget):
    """Deterministic domain-stratified allocation; expose undersized budgets."""
    allocation = {d: 0 for d in sorted(counts)}
    remaining = min(budget, sum(counts.values()))
    for _ in range(3):
        for domain in allocation:
            if remaining and allocation[domain] < counts[domain]:
                allocation[domain] += 1
                remaining -= 1
    while remaining:
        candidates = [d for d in allocation if allocation[d] < counts[d]]
        if not candidates:
            break
        # Weighted fair allocation without an O(N-records) in-memory reservoir.
        domain = max(candidates, key=lambda d: (counts[d] / (allocation[d] + 1), d))
        allocation[domain] += 1
        remaining -= 1
    return allocation


def update_review_queue(path, samples):
    """Append new sample IDs without losing prior annotations or custom columns."""
    required = ["record_id", "shard", "row_group", "row_offset", "reviewer", "confirmed_disposition", "notes"]
    unique = {s["record"]["record_id"]: s["record"] for s in samples}
    existing, columns = [], list(required)
    destination = path
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames and "record_id" in reader.fieldnames:
                columns = list(dict.fromkeys([*reader.fieldnames, *required]))
                existing = list(reader)
            else:
                # An unexpected user-edited format remains untouched; provide a separate queue.
                destination = path.with_name("review_decisions_pending.csv")
    seen = {r.get("record_id") for r in existing}
    new_rows = [{"record_id": key, "shard": r["shard"], "row_group": r["row_group"],
                 "row_offset": r["row_offset"], "reviewer": "", "confirmed_disposition": "", "notes": ""}
                for key, r in sorted(unique.items()) if key not in seen]
    if new_rows or not destination.exists():
        temp = destination.with_suffix(".csv.tmp")
        write_csv(temp, columns, ([row.get(k, "") for k in columns] for row in [*existing, *new_rows]))
        temp.replace(destination)
    return destination.name


def render_audit(db, out, manifest, run_state, elapsed):
    """Rebuild derived exports from committed rows; never overwrite human reviews."""
    out = Path(out)
    render_started = time.monotonic()
    file_states = {r["shard"]: dict(r) for r in db.execute("SELECT * FROM files")}
    files = []
    for item in manifest["files"]:
        state = dict(item)
        if item["selected"]:
            state.update(file_states.get(item["path"], {"state": "pending"}))
        files.append(state)
    records_count = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    conflicts = {r[0] for r in db.execute("""SELECT doc_id FROM records WHERE doc_id IS NOT NULL
      GROUP BY doc_id HAVING COUNT(DISTINCT normalized_sha256)>1
      OR COUNT(DISTINCT json_extract(details,'$.url'))>1""")}

    def with_context(row):
        record = json.loads(row["details"])
        if record.get("doc_id") in conflicts:
            record["flags"] = sorted(set(record.get("flags", [])) | {"conflicting_id_versions_or_urls"})
            record["reasons"] = list(record.get("reasons", [])) + ["cross_record_id_conflict_requires_review"]
            record["disposition"] = "needs_review"
        return record

    flags, dispositions, languages, structures = Counter(), Counter(), Counter(), Counter()
    metadata = Counter()
    domain_stats = defaultdict(Counter)
    examples = defaultdict(list)
    flag_examples = defaultdict(list)
    temp = out / "record_audit.parquet.tmp"
    with pq.ParquetWriter(temp, RECORD_SCHEMA, compression="zstd") as writer:
        cursor = db.execute("SELECT details FROM records ORDER BY shard,row_group,row_offset")
        while rows := cursor.fetchmany(256):
            exports = []
            for row in rows:
                record = with_context(row)
                exports.append(export_record(record))
                domain = record["domain"]
                domain_stats[domain]["rows"] += 1
                domain_stats[domain][record["disposition"]] += 1
                domain_stats[domain]["text_present"] += record.get("text_state") == "present"
                dispositions[record["disposition"]] += 1
                languages[record["language_hint"]] += 1
                flags.update(record.get("flags", []))
                for flag in record.get("flags", []):
                    if len(flag_examples[flag]) < 3:
                        flag_examples[flag].append(record)
                if len(examples[record["disposition"]]) < 3:
                    examples[record["disposition"]].append(record)
                for field, info in record.get("metadata", {}).items():
                    if isinstance(info, dict) and "state" in info:
                        metadata[(domain, field, info["state"])] += 1
                for name in ("block_state", "html_state", "doc_type_hint"):
                    structures[(domain, name, str(record.get(name, "unknown")))] += 1
                structures[(domain, "blocks_text_match", str(record.get("blocks_text_match")))] += 1
            writer.write_table(pa.Table.from_pylist(exports, schema=RECORD_SCHEMA))
    temp.replace(out / "record_audit.parquet")
    domain_columns = ["domain", "rows", "text_present", "usable_as_is", "needs_cleaning",
                      "needs_reextraction", "needs_review", "unusable_currently"]
    write_csv(out / "domain_quality.csv", domain_columns,
        ([domain, *[counts[k] for k in domain_columns[1:]]] for domain, counts in sorted(domain_stats.items())))
    write_csv(out / "metadata_profile.csv", ["domain", "field", "state", "rows", "domain_rows"],
              ((*key, count, domain_stats[key[0]]["rows"]) for key, count in sorted(metadata.items())))
    write_csv(out / "structure_profile.csv", ["domain", "dimension", "value", "rows"],
              ((*key, count) for key, count in sorted(structures.items())))
    export_sql_parquet(db, out, "duplicate_versions.parquet", """SELECT doc_id,COUNT(*) AS rows,
      COUNT(DISTINCT normalized_sha256) AS normalized_versions,
      COUNT(DISTINCT json_extract(details,'$.url')) AS source_urls
      FROM records WHERE doc_id IS NOT NULL GROUP BY doc_id HAVING COUNT(*)>1 ORDER BY doc_id""",
      pa.schema([("doc_id", pa.int64()), ("rows", pa.int64()), ("normalized_versions", pa.int64()), ("source_urls", pa.int64())]))
    export_sql_parquet(db, out, "duplicate_content.parquet", """SELECT normalized_sha256,COUNT(*) AS rows,
      COUNT(DISTINCT doc_id) AS distinct_ids FROM records WHERE normalized_sha256 IS NOT NULL
      GROUP BY normalized_sha256 HAVING COUNT(*)>1 ORDER BY normalized_sha256""",
      pa.schema([("normalized_sha256", pa.string()), ("rows", pa.int64()), ("distinct_ids", pa.int64())]))
    templates = db.execute("""SELECT domain,signature,MIN(kind) AS example_kind,MIN(example) AS example_text,
      SUM(records) AS record_occurrences FROM template_occurrences GROUP BY domain,signature
      HAVING SUM(records)>1 ORDER BY record_occurrences DESC,domain,signature LIMIT 1000""")
    write_csv(out / "repeated_block_candidates.csv", ["domain", "signature", "example_kind", "example_text", "record_occurrences"],
              (tuple(r) for r in templates))
    counts = {d: c["rows"] for d, c in domain_stats.items()}
    allocation = allocate_samples(counts, manifest["config"]["sample_size"])
    samples = []
    for domain, n in sorted(allocation.items()):
        for row in db.execute("SELECT details FROM records WHERE domain=? ORDER BY sample_key,record_id LIMIT ?", (domain, n)):
            record = with_context(row)
            samples.append({"sample_kind": "domain_stratified_random", "stratum": domain,
                "stratum_population": counts[domain], "stratum_sample_size": n,
                "inclusion_probability": n / counts[domain], "sampling_weight": counts[domain] / n,
                "record": record})
    for flag, records in sorted(flag_examples.items()):
        for record in records:
            samples.append({"sample_kind": "targeted_flag_example", "flag": flag,
                            "inclusion_probability": None, "sampling_weight": None, "record": record})
    with (out / "review_samples.jsonl").open("w", encoding="utf-8") as stream:
        for sample in samples:
            stream.write(dumps(sample) + "\n")
    review_queue = update_review_queue(out / "review_decisions.csv", samples)
    selected = [x for x in files if x["selected"]]
    all_selected_complete = all(x["state"] == "complete" for x in selected)
    partial = manifest["scope"] != "all_local_corpus" or not all_selected_complete or bool(flags["record_processing_error"])
    unknown_counts = sum(x["metadata_rows"] is None for x in selected)
    summary = {"audit_version": manifest["config"]["audit_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "run_state": run_state,
        "scope": manifest["scope"], "partial": partial, "selected_scope_complete": all_selected_complete,
        "discovered_corpus_files": manifest["discovered_corpus_files"], "selected_corpus_files": len(selected),
        "cataloged_files": len(files), "file_states": dict(Counter(x["state"] for x in selected)),
        "expected_selected_rows": None if unknown_counts else sum(x["metadata_rows"] for x in selected),
        "known_expected_selected_rows": sum(x["metadata_rows"] or 0 for x in selected),
        "selected_files_with_unknown_row_count": unknown_counts, "records_read": records_count,
        "distinct_doc_ids": db.execute("SELECT COUNT(DISTINCT doc_id) FROM records").fetchone()[0],
        "conflicting_doc_ids": len(conflicts), "dispositions": dict(dispositions), "quality_flags": dict(flags),
        "language_hints": dict(languages), "manual_review_status": "pending",
        "random_sample_records": sum(allocation.values()), "sample_allocation": allocation,
        "last_invocation_seconds": round(elapsed + time.monotonic() - render_started, 3),
        "timing_scope": "Includes inventory, scan, and metric exports; excludes final report/status serialization.",
        "review_queue": review_queue,
        "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        "files": files, "row_groups": [dict(r) for r in db.execute("SELECT * FROM row_groups ORDER BY shard,group_id")],
        "limitations": ["Automatic dispositions and language/type hints require human confirmation.",
            "Source SQLite databases are cataloged, never opened; archives are not expanded or double-counted.",
            "Hashes cover selected corpus files; other files have metadata-only inventory.",
            "Row count limits are not byte limits; a very large single row can exceed the normal RAM target.",
            "HTMLParser is tolerant; parsed markup is not proof of complete/valid HTML or correct main content.",
            "Template counts cover short block/line candidates, not all-pairs semantic near-duplicate detection.",
            "Random review sampling is stratified by domain; language/type/disposition coverage must be reviewed in stage 2.",
            "No sources were downloaded, cleaned, re-extracted, embedded, or indexed."]}
    # Atomic status replacement; the SQLite database is the resume authority.
    status_temp = out / "scan_status.json.tmp"
    status_temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    status_temp.replace(out / "scan_status.json")
    proposals = []
    for flag in ("html_in_text", "html_entities", "repeated_lines", "possible_ui_text", "blocks_text_mismatch", "missing_title"):
        if not flags[flag]:
            continue
        proposals.append({"issue_id": "audit_" + flag, "enabled": False, "status": "draft_review_required",
            "flag": flag, "affected_records": flags[flag], "denominator_records": records_count,
            "examples": [r["record_id"] for r in flag_examples[flag]],
            "next_action": "Review original locators and counterexamples before writing a cleaning/extraction rule.",
            "required_regression": "Preserve negation, dose/units, tables, Q&A, and original source mappings."})
    # JSON is a valid YAML subset; no additional parser dependency is required.
    (out / "proposed_rules.yaml").write_text(json.dumps({"status": "draft_unreviewed", "proposals": proposals},
                                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan = ["# Draft processing plan", "", "Automatic suggestions only; human review and full-corpus evidence are pending.", "",
        "No proposed rule is enabled. Do not use this file as a validated bulk-cleaning policy.", "",
        table(["Disposition", "Records", "Next step"], [(key, count, {
            "usable_as_is": "Verify sampled main content and create evidence versions.",
            "needs_cleaning": "Review noise candidates; build before/after fixtures.",
            "needs_reextraction": "Compare stored blocks/HTML with text offline.",
            "needs_review": "Resolve source/version/quality ambiguity.",
            "unusable_currently": "Retain reasons; exclude from current content index without recrawling."}[key])
            for key, count in sorted(dispositions.items())]), "",
        "Each confirmed issue needs domain/type scope, affected counts, positive and negative examples, raw dependency,",
        "CPU/RAM estimate, acceptance fixtures, and rollback conditions. Fill these after reviewing the sample packet.", "",
        "For short-block candidates, repeated medical advice is a required counterexample to automatic template removal.", "",
        "## Draft issues", "", *[f"- `{p['issue_id']}`: {p['affected_records']} / {records_count} records; see example IDs in proposed_rules.yaml."
                                   for p in proposals], ""]
    (out / "processing_plan.md").write_text("\n".join(plan), encoding="utf-8")
    report = ["# ViBioMIR S00 audit report", "", f"Generated: {summary['generated_at_utc']}", "",
        "**PARTIAL CORPUS AUDIT**" if partial else "All discovered corpus rows were automatically inspected.", "",
        "**Human review: pending.** Dispositions, language hints, and processing proposals are provisional.", "",
        table(["Metric", "Value"], [(k, summary[k]) for k in ("run_state", "scope", "discovered_corpus_files",
            "selected_corpus_files", "selected_scope_complete", "expected_selected_rows", "records_read",
            "distinct_doc_ids", "conflicting_doc_ids", "last_invocation_seconds", "process_peak_rss_mib")]), "",
        "## Dispositions", "", table(["Disposition", "Records"], sorted(dispositions.items())), "",
        "## Quality flags", "", table(["Flag", "Records"], sorted(flags.items())), "",
        "## Full-text language hints", "", table(["Hint (not confirmed language)", "Records"], sorted(languages.items())), "",
        "## Selected file states", "", table(["Shard", "State", "Expected rows", "Error"],
            [(x["path"], x["state"], x["metadata_rows"], x.get("error")) for x in selected]), "",
        "## Review and next delivery", "",
        f"The domain-stratified random sample has {sum(allocation.values())} records, with probabilities and weights.",
        "Targeted flag examples are separate and must not be used to estimate error rates.",
        f"Review source locators in review_samples.jsonl and fill {review_queue} (prior annotations preserved on resume).",
        "Run the full audit in delivery 2 before confirming processing_plan.md or enabling proposed_rules.yaml.", "",
        "## Artifacts", "",
        "- [Input manifest](input_manifest.json), [scan status](scan_status.json), [record metrics](record_audit.parquet).",
        "- [Domain quality](domain_quality.csv), [metadata profile](metadata_profile.csv), [structure profile](structure_profile.csv).",
        "- [Duplicate versions](duplicate_versions.parquet), [duplicate content](duplicate_content.parquet), [template candidates](repeated_block_candidates.csv).",
        "- [Review samples](review_samples.jsonl), [review decisions](review_decisions.csv), [draft plan](processing_plan.md), [draft rules](proposed_rules.yaml).", "",
        "## Limitations", "", *["- " + item for item in summary["limitations"]], ""]
    (out / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    return summary
