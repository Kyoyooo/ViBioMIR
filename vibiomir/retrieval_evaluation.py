"""Local, explicitly provisional document/chunk P/R/F2 scorer and paired reports.

Profiles are proxies until organizer tokenization/zero/duplicate rules are
confirmed. Gold spans remain independent of prediction chunk boundaries.
"""
import argparse
from collections import Counter
import html
import json
import math
from pathlib import Path
import random
import re
import unicodedata

from .io import canonical_json, file_hash, text_hash, write_json
from .review_common import require, write_csv

PROFILES = ("whitespace_proxy_v1", "cjk_character_sensitivity_v1")


def normalize(text):
    require(isinstance(text, str), "Text must be a string")
    return " ".join(html.unescape(unicodedata.normalize("NFKC", text)).lower().split())


def tokens(text, profile):
    value = normalize(text)
    require(profile in PROFILES, "Unknown local scorer profile")
    return value.split() if profile == "whitespace_proxy_v1" else re.findall(r"[\u3400-\u9fff]|[^\s\u3400-\u9fff]+", value)


def lcs_reference(left, right):
    previous = [0] * (len(right) + 1)
    for token in left:
        current = [0]
        for index, other in enumerate(right):
            current.append(previous[index] + 1 if token == other else max(current[-1], previous[index + 1]))
        previous = current
    return previous[-1]


def lcs(left, right):
    positions = {}
    for index, token in enumerate(right):
        positions[token] = positions.get(token, 0) | (1 << index)
    state = 0
    for token in left:
        matches = positions.get(token, 0) | state
        state = matches & ~(matches - ((state << 1) | 1))
    return state.bit_count()


def metrics(correct, predicted, found, gold):
    precision = correct / predicted if predicted else 0.0
    recall = found / gold if gold else 0.0
    f2 = 5 * precision * recall / (4 * precision + recall) if precision or recall else 0.0
    return {"precision": precision, "recall": recall, "f2": f2}


def identifier(value):
    require(type(value) is int and -(2**63) <= value < 2**63, "IDs must be int64 integers, not booleans/strings")
    return value


def prediction_map(rows):
    require(isinstance(rows, list), "Predictions must be a JSON array")
    output = {}
    for row in rows:
        require(isinstance(row, dict), "Prediction rows must be objects")
        query_id = identifier(row["id"])
        require(query_id not in output, "Duplicate prediction query ID")
        require(isinstance(row["relevant_docs"], list) and isinstance(row["relevant_chunks"], list), "Both prediction lists are required")
        docs = list(dict.fromkeys(identifier(i) for i in row["relevant_docs"]))
        chunks, seen = [], set()
        for chunk in row["relevant_chunks"]:
            doc_id = identifier(chunk["doc_id"])
            require(doc_id in docs, "Predicted chunk document must appear in relevant_docs")
            text = normalize(chunk["chunk_text"])
            require(text, "Empty predicted evidence is invalid; use an empty chunk array")
            if "chunk_order" in chunk:
                require(type(chunk["chunk_order"]) is int and chunk["chunk_order"] >= 0, "Invalid chunk_order")
            key = (doc_id, text)
            if key not in seen:
                seen.add(key)
                chunks.append(chunk)
        output[query_id] = {"id": query_id, "relevant_docs": docs, "relevant_chunks": chunks}
    return output


def score_query(gold, predicted, profile):
    gold_docs, predicted_docs = set(gold["relevant_docs"]), set(predicted["relevant_docs"])
    document = metrics(len(gold_docs & predicted_docs), len(predicted_docs), len(gold_docs & predicted_docs), len(gold_docs))
    references = []
    for chunk in gold["relevant_chunks"]:
        value = tokens(chunk["chunk_text"], profile)
        require(value and chunk["doc_id"] in gold_docs, "Gold evidence must be nonempty and belong to a relevant document")
        references.append((chunk["doc_id"], value, Counter(value)))
    found, correct = set(), 0
    for chunk in predicted["relevant_chunks"]:
        value = tokens(chunk["chunk_text"], profile)
        bag, matched = Counter(value), False
        for index, (doc_id, reference, reference_bag) in enumerate(references):
            if doc_id != chunk["doc_id"]:
                continue
            # Necessary length/bag bounds only; never approximate ordered LCS.
            if min(len(value), sum((bag & reference_bag).values())) * 5 < len(reference) * 2:
                continue
            if lcs(value, reference) * 5 >= len(reference) * 2:
                matched = True
                found.add(index)
        correct += matched
    passage = metrics(correct, len(predicted["relevant_chunks"]), len(found), len(references))
    judged = set(gold.get("judged_docs", gold["relevant_docs"]))
    return {"id": gold["id"], "document": document, "chunk": passage,
            "predicted_documents": len(predicted_docs), "predicted_chunks": len(predicted["relevant_chunks"]),
            "gold_documents": len(gold_docs), "gold_chunks": len(references),
            "judged_documents_retrieved": len(predicted_docs & judged),
            "unjudged_documents_retrieved": len(predicted_docs - judged),
            "judged_at_output": len(predicted_docs & judged) / len(predicted_docs) if predicted_docs else None}


def score(gold, predictions, *, split="dev", profile="whitespace_proxy_v1", allow_unjudged_holdout=False):
    require(profile in PROFILES and split in {"train", "dev", "holdout", "all"}, "Unknown split/profile")
    require(isinstance(gold, dict) and isinstance(gold.get("queries"), list), "Use a compiled benchmark with queries")
    queries = [q for q in gold["queries"] if split == "all" or q["split"] == split]
    require(queries, "No benchmark queries in this split")
    predicted = prediction_map(predictions)
    ids = [identifier(q["id"]) for q in queries]
    require(len(set(ids)) == len(ids) and set(predicted) == set(ids), "Predictions must cover exactly every selected query ID")
    per_query = [score_query(q, predicted[q["id"]], profile) for q in queries]
    if split in {"holdout", "all"} and not allow_unjudged_holdout:
        holdout = {q["id"] for q in queries if q["split"] == "holdout"}
        require(not any(q["unjudged_documents_retrieved"] for q in per_query if q["id"] in holdout),
                "Unjudged holdout top results need independent annotation before final comparison")
    average = {level: {key: sum(q[level][key] for q in per_query) / len(per_query)
                       for key in ("precision", "recall", "f2")} for level in ("document", "chunk")}
    slices = {}
    by_id = {row["id"]: row for row in per_query}
    for dimension in ("gold_languages", "intent", "icd", "gold_domains", "tags"):
        groups = {}
        for query in queries:
            labels = query.get(dimension) or ["unknown"]
            require(isinstance(labels, list) and all(isinstance(label, str) for label in labels), "Slice labels must be arrays of strings")
            for label in set(labels):
                groups.setdefault(label, []).append(by_id[query["id"]])
        slices[dimension] = {label: {"queries": len(rows), "macro": {
            level: {key: sum(row[level][key] for row in rows) / len(rows) for key in ("precision", "recall", "f2")}
            for level in ("document", "chunk")}} for label, rows in groups.items()}
    return {"split": split, "queries": len(queries), "macro": average, "per_query": per_query, "slices": slices,
            "error_review_query_ids": {level: [row["id"] for row in sorted(per_query, key=lambda r: (r[level]["f2"], r["id"]))[:30]]
                                       for level in ("document", "chunk")},
            "profile": {"name": profile, "official": False, "normalization": "NFKC -> HTML unescape -> lowercase -> whitespace collapse",
                        "lcs": "exact ordered token subsequence", "overlap_threshold": 0.4,
                        "duplicates": "deduplicate normalized exact evidence within query/doc; document IDs are sets",
                        "zero_cases": "P=0 for no predictions, R=0 for empty gold, F2=0 for zero denominator"},
            "goldless_document_queries": sum(not q["gold_documents"] for q in per_query),
            "goldless_chunk_queries": sum(not q["gold_chunks"] for q in per_query),
            "scope": "Local/pool-relative labels; unjudged documents are not established negatives",
            "unjudged_documents_retrieved": sum(q["unjudged_documents_retrieved"] for q in per_query),
            "candidate_gold_coverage": None}


def paired_bootstrap(left, right, *, repetitions=1000, seed=270126):
    require(type(repetitions) is int and repetitions > 0, "Positive bootstrap repetitions required")
    require(left["profile"] == right["profile"] and left["split"] == right["split"], "Scorer profiles/splits differ")
    a, b = ({q["id"]: q for q in report["per_query"]} for report in (left, right))
    require(set(a) == set(b) and a, "Paired query sets differ")
    ids, rng, output = sorted(a), random.Random(seed), {}
    for level in ("document", "chunk"):
        differences = [b[i][level]["f2"] - a[i][level]["f2"] for i in ids]
        samples = sorted(sum(differences[rng.randrange(len(ids))] for _ in ids) / len(ids) for _ in range(repetitions))
        output[level] = {"delta_f2": sum(differences) / len(ids),
                         "bootstrap_95": [samples[max(0, math.floor(0.025 * (repetitions - 1)))],
                                          samples[min(repetitions - 1, math.ceil(0.975 * (repetitions - 1)))]]}
    return {"unit": "query", "repetitions": repetitions, "seed": seed, "right_minus_left": output}


def compare_runs(benchmark_path, run_paths, output_dir, *, split="dev", baseline=None,
                 profile="whitespace_proxy_v1", repetitions=1000, frozen_selection=None, experiment_family="chunker"):
    require(len(run_paths) >= 2 and baseline in run_paths, "Choose at least two runs and a named baseline")
    require(split in {"train", "dev", "holdout"}, "Compare one declared split")
    gold = json.loads(Path(benchmark_path).read_text())
    require(gold.get("human_reviewed") is True, "Compile human-reviewed local labels before comparing retrieval quality")
    benchmark_hash = file_hash(benchmark_path)
    require(experiment_family in {"chunker", "chunk_parameters", "output_policy", "retriever"}, "Unknown comparison parameter family")
    if split == "holdout":
        require(frozen_selection is not None, "Freeze dev selection before holdout reporting")
        frozen = json.loads(Path(frozen_selection).read_text())
        require(frozen["dev_labels_signature"] == dev_signature(gold) and set(run_paths) <= set(frozen["frozen_runs"]),
                "Dev labels or frozen run names changed; holdout-only adjudication may version the benchmark without retuning")
    reports, runs = {}, {}
    for name, run_path in run_paths.items():
        run_path = Path(run_path)
        metadata = json.loads((run_path / "run.json").read_text())
        require(metadata["benchmark_sha256"] == benchmark_hash and metadata["split"] == split, "Run benchmark/split differs")
        require(metadata["configuration_signature"] == text_hash(canonical_json(metadata["configuration"])), "Run configuration signature differs")
        require(metadata["configuration"]["corpus_signature"] == metadata["corpus_signature"] and
                metadata["configuration"]["policy"] == metadata["evaluation_policy"], "Run metadata contradicts its configuration")
        predictions_path = run_path / "predictions.json"
        require(metadata["predictions_sha256"] == file_hash(predictions_path), "Run predictions changed")
        if split == "holdout":
            require(metadata["configuration_signature"] == frozen["frozen_runs"][name], "Holdout configuration changed after freeze")
        reports[name] = score(gold, json.loads(predictions_path.read_text()), split=split, profile=profile)
        runs[name] = metadata
    common_fields = ["corpus_signature"] + ([] if experiment_family == "output_policy" else ["evaluation_policy"])
    for field in common_fields:
        require(len({json.dumps(m[field], sort_keys=True) for m in runs.values()}) == 1, f"Uncontrolled run difference: {field}")
    if experiment_family != "retriever":
        controls = [{k: m["configuration"][k] for k in ("analyzer", "retriever", "k1", "b")} for m in runs.values()]
        require(len({canonical_json(c) for c in controls}) == 1, "Retriever/analyzer changed alongside another parameter family")
    if experiment_family == "output_policy":
        require(len({m["configuration"]["chunk_signature"] for m in runs.values()}) == 1, "Chunk store changed alongside output caps")
        keys = set().union(*(m["evaluation_policy"] for m in runs.values()))
        require(sum(len({canonical_json(m["evaluation_policy"].get(k)) for m in runs.values()}) > 1 for k in keys) <= 1,
                "Change one output-policy parameter at a time")
    elif experiment_family == "chunker":
        require(len({canonical_json(m["configuration"]["chunk_config"]) for m in runs.values()}) == 1,
                "Chunk size/overlap/threshold changed alongside the chunker")
    elif experiment_family == "chunk_parameters":
        require(len({m["configuration"]["chunker"] for m in runs.values()}) == 1, "Chunker changed alongside its parameters")
        keys = set().union(*(m["configuration"]["chunk_config"] for m in runs.values()))
        require(sum(len({canonical_json(m["configuration"]["chunk_config"].get(k)) for m in runs.values()}) > 1 for k in keys) <= 1,
                "Change one chunk-parameter family at a time")
    elif experiment_family == "retriever":
        require(len({m["configuration"]["chunk_signature"] for m in runs.values()}) == 1, "Chunk store changed alongside retriever")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = {"benchmark_sha256": benchmark_hash, "dev_labels_signature": dev_signature(gold), "split": split, "profile": profile, "baseline": baseline,
              "implementation_sha256": file_hash(Path(__file__)),
              "experiment_family": experiment_family, "runs": runs, "scores": reports, "paired": {n: paired_bootstrap(reports[baseline], value,
              repetitions=repetitions) for n, value in reports.items() if n != baseline},
              "selection": "No automatic winner; inspect both F2 metrics, uncertainty, cost and slices"}
    if (output / "REPORT.json").exists():
        require(json.loads((output / "REPORT.json").read_text()) == report, "Comparison report exists with different inputs/options; choose a new output directory")
    write_json(output / "REPORT.json", report)
    write_csv(output / "SUMMARY.csv", ["run", "document_precision", "document_recall", "document_f2", "chunk_precision", "chunk_recall", "chunk_f2", "unjudged_documents", "p50_seconds", "p95_seconds"], (
        {"run": name, **{f"{level}_{key}": value for level, metrics_ in result["macro"].items() for key, value in metrics_.items()},
         "unjudged_documents": result["unjudged_documents_retrieved"],
         "p50_seconds": runs[name].get("latency", {}).get("p50_seconds"),
         "p95_seconds": runs[name].get("latency", {}).get("p95_seconds")}
        for name, result in reports.items()))
    return report


def dev_signature(gold):
    keys = ("id", "query", "case_group", "relevant_docs", "relevant_chunks", "judged_docs")
    return text_hash(canonical_json([{k: q[k] for k in keys} for q in sorted(gold["queries"], key=lambda q: q["id"]) if q["split"] == "dev"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    single = commands.add_parser("score")
    single.add_argument("--predictions", required=True, type=Path)
    compare = commands.add_parser("compare-runs")
    compare.add_argument("--run", action="append", required=True, help="name=run_directory")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--frozen-selection", type=Path)
    compare.add_argument("--bootstrap", type=int, default=1000)
    compare.add_argument("--experiment-family", choices=["chunker", "chunk_parameters", "output_policy", "retriever"], default="chunker")
    for command in (single, compare):
        command.add_argument("--benchmark", required=True, type=Path)
        command.add_argument("--output-dir", required=True, type=Path)
        command.add_argument("--split", choices=["train", "dev", "holdout"], default="dev")
        command.add_argument("--profile", choices=PROFILES, default=PROFILES[0])
    args = parser.parse_args()
    try:
        if args.command == "score":
            result = score(json.loads(args.benchmark.read_text()), json.loads(args.predictions.read_text()), split=args.split, profile=args.profile)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            write_json(args.output_dir / "SCORES.json", result)
        else:
            runs = dict(value.split("=", 1) for value in args.run)
            require(len(runs) == len(args.run), "Duplicate run name")
            result = compare_runs(args.benchmark, runs, args.output_dir, split=args.split, baseline=args.baseline,
                                  profile=args.profile, repetitions=args.bootstrap, frozen_selection=args.frozen_selection,
                                  experiment_family=args.experiment_family)
        print(json.dumps({"state": "complete", "output_dir": str(args.output_dir)}))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
