"""Human-authored local questions, pooled judgments and source-backed gold spans."""
import argparse
import csv
import json
import math
from pathlib import Path
import sqlite3

from .chunk_batches import read_json, read_rows
from .io import canonical_json, file_hash, text_hash, write_json
from .review_common import completed, frozen_sample, priority, require, separate, workflow, write_csv
from .retrieval_evaluation import dev_signature, identifier, prediction_map

JUDGMENT_FIELDS = ["query_id", "doc_id", "judged", "relevant", "annotator", "reviewer", "reason"]
SPAN_FIELDS = ["query_id", "doc_id", "derived_version_id", "region_id", "start", "end", "annotator", "reviewer"]


def init_benchmark(qa_dir, output_dir, *, query_count=30):
    require(query_count > 0, "Positive question count required")
    sample = frozen_sample(Path(qa_dir) / "sample.json")
    rows = sorted(sample["selection"], key=lambda r: priority(sample["seed"], "questions", r["doc_id"]))
    require(len(rows) >= query_count, "Not enough sampled documents for this question template")
    output = Path(output_dir)
    require(not output.exists() or not any(output.iterdir()), "Use a new empty benchmark authoring directory; existing labels are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    questions = [{"id": index + 1, "query": "", "split": "train" if index % 3 == 0 else "dev" if index % 3 == 1 else "holdout",
                  "case_group": f"source_{row['doc_id']}", "origin_doc_ids": [row["doc_id"]],
                  "author": "", "vietnamese_confirmed": False, "source_verified": False,
                  "goldless_confirmed_by": "", "gold_languages": [], "intent": [], "icd": [], "gold_domains": [], "tags": []}
                 for index, row in enumerate(rows[:query_count])]
    write_json(output / "queries.json", questions)
    write_csv(output / "judgments.csv", JUDGMENT_FIELDS, (
        {**dict.fromkeys(JUDGMENT_FIELDS, ""), "query_id": q["id"], "doc_id": q["origin_doc_ids"][0], "judged": "no"}
        for q in questions))
    write_csv(output / "spans.csv", SPAN_FIELDS, [])
    write_json(output / "AUTHORING.json", {"state": "human authoring required", "qa_sample_signature": sample["signature"],
               "queries": query_count, "source": "Human-authored Vietnamese questions; no generated gold labels",
               "split_note": "Template split is provisional; keep related cases/translations/source groups together"})
    return {"output": str(output), "state": "templates only; no questions or labels generated"}


def pool_candidates(queries_path, prediction_paths, output_file):
    queries = read_json(queries_path)
    ids = {identifier(q["id"]) for q in queries}
    pairs = {(q["id"], identifier(doc)) for q in queries for doc in q.get("origin_doc_ids", [])}
    for path in prediction_paths:
        predictions = prediction_map(read_json(path))
        require(set(predictions) <= ids, "Pool contains unknown queries")
        pairs.update((q, doc) for q, row in predictions.items() for doc in row["relevant_docs"])
    output_file = Path(output_file)
    require(not output_file.exists(), "Pool output exists; preserve manual judgments and choose a new file")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output_file, JUDGMENT_FIELDS, ({**dict.fromkeys(JUDGMENT_FIELDS, ""), "query_id": q, "doc_id": doc, "judged": "no"}
                                            for q, doc in sorted(pairs)))
    write_json(output_file.with_suffix(".doc_ids.json"), sorted({doc for _, doc in pairs}))
    return {"pairs": len(pairs), "all_judgments": "unfilled; no automatic negatives or relevance labels"}


def selected_packets(config, doc_ids):
    ids = set(doc_ids)
    require(ids and all(type(i) is int for i in ids), "Nonempty integer document IDs required")
    parent, _ = completed(config["input_dir"])
    catalog_dir = Path(config["qa_dir"])
    catalog = read_json(catalog_dir / "catalog_manifest.json")
    require(catalog["state"] == "complete" and catalog["identity"]["source"]["signature"] == parent["signature"], "Build the matching QA catalog first")
    database = (catalog_dir / "catalog.sqlite3").resolve()
    with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        locations = {}
        for doc_id in ids:
            row = db.execute("SELECT batch,version FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
            require(row is not None, f"Document not in the catalog: {doc_id}")
            locations[doc_id] = row
    found = set()
    for index in sorted({v[0] for v in locations.values()}):
        path = Path(config["input_dir"]) / f"batch-{index:06d}"
        info = read_json(path / "batch.json")
        require(info["index"] == index and info["signature"] == parent["signature"] and
                file_hash(path / "documents.jsonl.gz") == info["sha256"], "Selected input batch changed")
        for packet in read_rows(path):
            if packet["doc_id"] in ids:
                require(packet["derived_version_id"] == locations[packet["doc_id"]][1], "Catalog version changed")
                found.add(packet["doc_id"])
                yield packet
    require(found == ids, "Selected document packet missing")


def export_evidence(config, doc_ids, output_dir):
    root = Path(output_dir)
    separate(root, [config["input_dir"], config["stage3_dir"], config["stage4_dir"]])
    root.mkdir(parents=True, exist_ok=True)
    parent, snapshot = completed(config["input_dir"])
    identity = {"input": snapshot, "doc_ids": sorted(set(doc_ids))}
    if (root / "manifest.json").exists():
        require(read_json(root / "manifest.json")["identity"] == identity, "Evidence export selection changed; choose a new output directory")
    hashes = {}
    for packet in selected_packets(config, doc_ids):
        target = root / f"{packet['doc_id']}.json"
        write_json(target, packet)
        hashes[str(packet["doc_id"])] = file_hash(target)
    write_json(root / "manifest.json", {"state": "complete", "identity": identity, "files": hashes})
    return {"documents": len(hashes), "input_signature": parent["signature"]}


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            require(None not in row and all(isinstance(v, str) for v in row.values()), f"Malformed CSV cells: {path}")
            yield {key: value.strip() for key, value in row.items()}


def compile_benchmark(queries_path, judgments_path, spans_path, evidence_dir, output_dir, *, allow_incomplete_review=False):
    queries = read_json(queries_path)
    require(isinstance(queries, list) and queries, "Questions must be a nonempty JSON array")
    ids, groups, source_splits, question_texts = set(), {}, {}, set()
    for q in queries:
        identifier(q["id"])
        require(q["id"] not in ids, "Duplicate question ID")
        ids.add(q["id"])
        require(isinstance(q["query"], str) and q["query"].strip() and q.get("author", "").strip(), "Human question text and author are required")
        from .retrieval_evaluation import normalize
        require(normalize(q["query"]) not in question_texts, "Duplicate normalized question text")
        question_texts.add(normalize(q["query"]))
        require(q.get("vietnamese_confirmed") is True and q.get("source_verified") is True, "Verify Vietnamese question and source manually")
        require(q["split"] in {"train", "dev", "holdout"} and isinstance(q["case_group"], str) and q["case_group"], "Declare split/case_group")
        require(groups.setdefault(q["case_group"], q["split"]) == q["split"], "Related case/translation group crosses splits")
        for doc_id in q.get("origin_doc_ids", []):
            identifier(doc_id)
            require(source_splits.setdefault(doc_id, q["split"]) == q["split"], "Origin source crosses splits; group related questions together")
    judgments, label_count, second_reviewed = {}, 0, 0
    for row in csv_rows(judgments_path):
        key = (int(row["query_id"]), int(row["doc_id"]))
        require(key[0] in ids and key not in judgments, "Unknown/duplicate document judgment")
        identifier(key[1])
        require(row["judged"] in {"yes", "no"}, "Fill judged=yes/no explicitly")
        if row["judged"] == "yes":
            require(row["relevant"] in {"yes", "no"} and row["annotator"].strip(), "Judged labels need relevance and human annotator")
            label_count += 1
            if row["reviewer"].strip():
                require(row["reviewer"] != row["annotator"], "Independent label reviewer required")
                second_reviewed += 1
        else:
            require(not row["relevant"].strip(), "Unjudged documents cannot be labeled negative or positive")
        judgments[key] = row
    source_root = Path(evidence_dir)
    source_manifest = read_json(source_root / "manifest.json")
    require(source_manifest["state"] == "complete", "Complete evidence export required")
    packets, gold_spans, seen = {}, {q: [] for q in ids}, set()
    for row in csv_rows(spans_path):
        qid, doc_id = int(row["query_id"]), int(row["doc_id"])
        judgment = judgments.get((qid, doc_id))
        require(judgment and judgment["judged"] == judgment["relevant"] == "yes", "Gold span requires a judged-positive document")
        require(row["annotator"].strip(), "Gold span needs a human annotator")
        path = source_root / f"{doc_id}.json"
        require(str(doc_id) in source_manifest["files"] and file_hash(path) == source_manifest["files"][str(doc_id)], "Gold evidence changed or not exported")
        if doc_id not in packets:
            packets[doc_id] = read_json(path)
        packet = packets[doc_id]
        require(packet["doc_id"] == doc_id and packet["derived_version_id"] == row["derived_version_id"], "Gold version differs from evidence")
        regions = {r["region_id"]: r for r in packet["regions"]}
        require(row["region_id"] in regions, "Unknown source region")
        start, end = int(row["start"]), int(row["end"])
        region = regions[row["region_id"]]
        require(0 <= start < end <= len(region["text"]) and region["text"][start:end].strip(), "Invalid/empty original gold span")
        key = (qid, doc_id, row["derived_version_id"], row["region_id"], start, end)
        require(key not in seen, "Duplicate gold span annotation")
        seen.add(key)
        text = region["text"][start:end]
        gold_spans[qid].append({"doc_id": doc_id, "derived_version_id": row["derived_version_id"], "region_id": row["region_id"],
            "region_start": start, "region_end": end, "source_start": region["source_start"] + start,
            "source_end": region["source_start"] + end, "chunk_text": text, "text_sha256": text_hash(text),
            "annotator": row["annotator"], "reviewer": row["reviewer"]})
        label_count += 1
        if row["reviewer"].strip():
            require(row["reviewer"] != row["annotator"], "Independent span reviewer required")
            second_reviewed += 1
    output_queries = []
    for query in queries:
        qid = query["id"]
        relevant = sorted(doc for (q, doc), row in judgments.items() if q == qid and row["judged"] == row["relevant"] == "yes")
        require(set(relevant) == {c["doc_id"] for c in gold_spans[qid]}, "Every positive document needs an independent original evidence span")
        require(relevant or query.get("goldless_confirmed_by", "").strip(), "No positives: confirm goldless explicitly or finish labeling")
        output_queries.append({**query, "relevant_docs": relevant, "relevant_chunks": gold_spans[qid],
             "judged_docs": sorted(doc for (q, doc), row in judgments.items() if q == qid and row["judged"] == "yes")})
    reviewed = label_count > 0 and second_reviewed >= math.ceil(0.2 * label_count)
    require(reviewed or allow_incomplete_review, "Re-review at least 20% of document/span labels, or explicitly export an unreviewed draft")
    result = {"format": "vibiomir-local-benchmark-v1", "human_reviewed": reviewed, "queries": output_queries,
              "corpus_signature": source_manifest["identity"]["input"]["signature"],
              "sources": {"queries": file_hash(queries_path), "judgments": file_hash(judgments_path), "spans": file_hash(spans_path),
                          "evidence_manifest": file_hash(source_root / "manifest.json")},
              "labels": label_count, "second_reviewed_labels": second_reviewed,
              "gold_scope": "Human-reviewed local candidate pool, not official competition gold"}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "benchmark.json").exists():
        require(read_json(output_dir / "benchmark.json") == result, "Compiled benchmark changed; version it in another directory")
    write_json(output_dir / "benchmark.json", result)
    return {"questions": len(queries), "human_reviewed": reviewed, "splits": {s: sum(q["split"] == s for q in queries) for s in ("train", "dev", "holdout")}}


def freeze_dev(report_dir, output_file, run_names):
    report = read_json(Path(report_dir) / "REPORT.json")
    require(report["split"] == "dev" and run_names and set(run_names) <= set(report["runs"]), "Freeze only declared dev-evaluated runs")
    output_file = Path(output_file)
    require(not output_file.exists(), "Selection is already frozen; use a new version to change it")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_file, {"benchmark_sha256": report["benchmark_sha256"], "dev_report_sha256": file_hash(Path(report_dir) / "REPORT.json"),
               "dev_labels_signature": report["dev_labels_signature"],
               "frozen_runs": {n: report["runs"][n]["configuration_signature"] for n in run_names},
               "selection_method": "Human dev selection; holdout cannot tune these configurations"})


def locate_span(evidence_dir, doc_id, text_file, output_file, *, strip_final_newline=False):
    root = Path(evidence_dir)
    manifest = read_json(root / "manifest.json")
    path = root / f"{doc_id}.json"
    require(manifest["state"] == "complete" and manifest["files"].get(str(doc_id)) == file_hash(path), "Original evidence export required")
    text = Path(text_file).read_text(encoding="utf-8")
    if strip_final_newline:
        text = text.removesuffix("\n")
    require(text.strip(), "Select nonempty original evidence")
    packet, candidates = read_json(path), []
    for region in packet["regions"]:
        start = region["text"].find(text)
        while start >= 0:
            candidates.append({"doc_id": doc_id, "derived_version_id": packet["derived_version_id"],
                "region_id": region["region_id"], "start": start, "end": start + len(text), "text_sha256": text_hash(text)})
            start = region["text"].find(text, start + 1)
    require(candidates, "Selection is not an exact source substring; check combining characters/editor newline; normalization is not applied")
    require(not Path(output_file).exists(), "Span candidate file exists; choose a new output path")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    write_json(output_file, candidates)
    return {"matches": len(candidates), "choice": "Human chooses the correct source occurrence; no automatic gold label"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--qa-dir", type=Path, required=True)
    initialize.add_argument("--output-dir", type=Path, required=True)
    initialize.add_argument("--questions", type=int, default=30)
    pool = commands.add_parser("pool")
    pool.add_argument("--queries", type=Path, required=True)
    pool.add_argument("--predictions", type=Path, action="append", default=[])
    pool.add_argument("--output-file", type=Path, required=True)
    evidence = commands.add_parser("export-evidence")
    evidence.add_argument("--config", default="configs/stage5_workflow.json")
    evidence.add_argument("--ids-file", type=Path, required=True)
    evidence.add_argument("--output-dir", type=Path, required=True)
    compile_ = commands.add_parser("compile")
    for name in ("queries", "judgments", "spans", "evidence-dir", "output-dir"):
        compile_.add_argument("--" + name, type=Path, required=True)
    compile_.add_argument("--allow-incomplete-review", action="store_true")
    freeze = commands.add_parser("freeze-dev")
    freeze.add_argument("--report-dir", type=Path, required=True)
    freeze.add_argument("--output-file", type=Path, required=True)
    freeze.add_argument("--run", action="append", required=True)
    locate = commands.add_parser("locate-span")
    locate.add_argument("--evidence-dir", required=True, type=Path)
    locate.add_argument("--doc-id", required=True, type=int)
    locate.add_argument("--text-file", required=True, type=Path)
    locate.add_argument("--output-file", required=True, type=Path)
    locate.add_argument("--strip-final-newline", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_benchmark(args.qa_dir, args.output_dir, query_count=args.questions)
        elif args.command == "pool":
            result = pool_candidates(args.queries, args.predictions, args.output_file)
        elif args.command == "export-evidence":
            result = export_evidence(workflow(args.config), read_json(args.ids_file), args.output_dir)
        elif args.command == "compile":
            result = compile_benchmark(args.queries, args.judgments, args.spans, args.evidence_dir, args.output_dir,
                                       allow_incomplete_review=args.allow_incomplete_review)
        elif args.command == "freeze-dev":
            result = freeze_dev(args.report_dir, args.output_file, args.run)
        else:
            result = locate_span(args.evidence_dir, args.doc_id, args.text_file, args.output_file,
                                 strip_final_newline=args.strip_final_newline)
        print(json.dumps(result or {"state": "done"}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
