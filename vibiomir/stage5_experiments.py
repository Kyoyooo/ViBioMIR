"""Manual full fixed baseline, streaming intrinsic comparison and bounded pilots."""
import argparse
from collections import Counter
from contextlib import ExitStack
from itertools import zip_longest
import json
import math
from pathlib import Path
import sqlite3
import time

from .chunk_batches import batches, commit_batch, finish, output_store, read_json, read_rows
from .chunk_models import load_tokenizer
from .chunk_parallel import build_parallel
from .chunk_pipeline import code_signature
from .chunk_validation import read_lock, validate_parallel
from .chunking import settings
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .review_common import completed, frozen_sample, priority, require, separate, workflow, write_csv


def full_fixed(config, *, stop_after_batches=None):
    output = Path(config["stores"]["fixed"])
    separate(output, [config["input_dir"], config["stage3_dir"], config["stage4_dir"],
                      config["stores"]["structure"], config["stores"]["semantic"]])
    completed(config["input_dir"])
    # Preflight all inexpensive arguments before creating output or loading workers.
    require(stop_after_batches is None or stop_after_batches > 0, "Positive stop-after-batches required")
    require(not output.exists() or not any(output.iterdir()) or (output / "manifest.json").is_file(),
            "Fixed output has files but no manifest; choose another output after inspecting it")
    chunk_config = settings(read_json(config["chunk_config"]))
    report_dir = Path(config["fixed_report_dir"])
    separate(report_dir, [config["input_dir"], *config["stores"].values()])
    separate(config["fixed_validation_dir"], [config["input_dir"], *config["stores"].values()])
    started = time.monotonic()
    manifest = build_parallel(config["input_dir"], output, config["tokenizer_dir"], strategy="fixed",
        config=chunk_config, workers=config["workers"], resume=(output / "manifest.json").exists(),
        stop_after_batches=stop_after_batches)
    if manifest["state"] != "complete":
        return {"state": manifest["state"], "next": "Repeat full-fixed without --stop-after-batches"}
    validation = validate_parallel(config["input_dir"], output, config["tokenizer_dir"],
        config["fixed_validation_dir"], workers=config["workers"], resume=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"state": "complete", "strategy": "fixed", "source": str(output),
              "manifest_sha256": file_hash(output / "manifest.json"),
              "validation_sha256": file_hash(output / "VALIDATION.json"),
              "documents": manifest["documents"], "chunks": manifest["chunks"],
              "validation": validation["validation"], "seconds_this_invocation": time.monotonic() - started,
              "gpu_used": False, "human_qa": "pending", "ready_for_retrieval": False}
    write_json(report_dir / "REPORT.json", report)
    return report


def row_statistics(row, config):
    counts, lengths = Counter(), Counter()
    counts["documents"] = 1
    counts["documents_without_chunks"] = not row["chunks"]
    counts["chunks"] = len(row["chunks"])
    spans = {}
    for chunk in row["chunks"]:
        token_count = chunk["token_count"]
        require(type(token_count) is int and 0 < token_count <= config["max_tokens"], "Invalid recorded token budget")
        lengths[token_count] += 1
        counts["retrieval_tokens"] += token_count
        counts["evidence_characters"] += len(chunk["chunk_text"])
        counts["context_characters"] += len(chunk["context_prefix"])
        counts["short_chunks"] += token_count < config["min_tokens"]
        spans.setdefault(chunk["region_id"], []).append((chunk["region_start"], chunk["region_end"]))
    for intervals in spans.values():
        end = 0
        for start, stop in sorted(intervals):
            counts["overlapping_source_characters"] += max(0, min(end, stop) - start)
            end = max(end, stop)
    return {"counts": dict(counts), "tokens": dict(lengths), "chunks_per_document": {len(row["chunks"]): 1}}


def combine_statistics(values):
    result = {key: Counter() for key in ("counts", "tokens", "chunks_per_document")}
    for value in values:
        for key in result:
            result[key].update({int(k) if key != "counts" else k: v for k, v in value[key].items()})
    return {key: dict(value) for key, value in result.items()}


def quantile(histogram, fraction):
    total = sum(histogram.values())
    if not total:
        return None
    threshold, cumulative = max(1, math.ceil(total * fraction)), 0
    for value, count in sorted((int(k), v) for k, v in histogram.items()):
        cumulative += count
        if cumulative >= threshold:
            return value


def compare_stores(input_dir, stores, output_dir, *, selected_ids=None, exact_duplicates=False):
    parent, source = completed(input_dir)
    require(len(stores) >= 2, "Compare at least two strategies")
    root = Path(output_dir)
    separate(root, [input_dir, *stores.values()])
    manifests, snapshots = {}, {}
    for name, path in stores.items():
        manifests[name], snapshots[name] = completed(path, validated=True)
        require(manifests[name]["identity"]["parent_signature"] == parent["signature"], "All stores must use the same corpus")
    require(len({m["identity"]["tokenizer"] for m in manifests.values()}) == 1, "Tokenizer differs between strategies")
    if selected_ids is not None:
        require(selected_ids and all(type(i) is int for i in selected_ids), "Nonempty integer selection required")
        selected_ids = set(selected_ids)
    identity = {"input": source, "stores": snapshots, "selection": sorted(selected_ids) if selected_ids is not None else None,
                "exact_duplicates": exact_duplicates, "code": file_hash(Path(__file__))}
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "manifest.json"
    with ExitStack() as stack:
        stack.enter_context(writer_lock(root))
        for path in (input_dir, *stores.values()):
            stack.enter_context(read_lock(path))
        if marker.exists():
            require(read_json(marker)["identity"] == identity, "Comparison inputs/options changed; use another output directory")
        write_json(marker, {"state": "building", "identity": identity})
        db = stack.enter_context(sqlite3.connect(root / "comparison.sqlite3"))
        db.executescript("""CREATE TABLE IF NOT EXISTS batches(batch INTEGER PRIMARY KEY,identity TEXT,stats TEXT);
          CREATE TABLE IF NOT EXISTS groups(strategy TEXT,hash TEXT,n INTEGER,PRIMARY KEY(strategy,hash));""")
        iterators = [batches(input_dir, parent["signature"])] + [batches(stores[n], manifests[n]["signature"]) for n in stores]
        batch_count, found = 0, set()
        for index, parts in enumerate(zip_longest(*iterators)):
            require(all(part is not None for part in parts), "Batch count differs")
            batch_identity = canonical_json([info for _, info in parts])
            old = db.execute("SELECT identity FROM batches WHERE batch=?", (index,)).fetchone()
            if old:
                require(old[0] == batch_identity, "Committed comparison batch changed")
                batch_count += 1
                continue
            stats, selected_count, selected_here = {n: [] for n in stores}, 0, []
            with db:
                for records in zip_longest(*(read_rows(path) for path, _ in parts)):
                    require(all(row is not None for row in records), "Document count differs")
                    packet, outputs = records[0], records[1:]
                    require(all(all(row[k] == packet[k] for k in ("doc_id", "derived_version_id", "availability")) for row in outputs), "Document/version mapping differs")
                    if selected_ids is not None and packet["doc_id"] not in selected_ids:
                        continue
                    selected_count += 1
                    selected_here.append(packet["doc_id"])
                    for name, row in zip(stores, outputs):
                        stats[name].append(row_statistics(row, manifests[name]["identity"]["config"]))
                        if exact_duplicates:
                            from .retrieval_evaluation import normalize
                            db.executemany("INSERT INTO groups VALUES (?,?,1) ON CONFLICT(strategy,hash) DO UPDATE SET n=n+1",
                                           ((name, text_hash(normalize(c["chunk_text"]))) for c in row["chunks"]))
                payload = {"selected": selected_here if selected_ids is not None else None,
                           "documents": selected_count, "strategies": {n: combine_statistics(s) for n, s in stats.items()}}
                db.execute("INSERT INTO batches VALUES (?,?,?)", (index, batch_identity, canonical_json(payload)))
            batch_count += 1
            if index % 25 == 0:
                print(json.dumps({"phase": "intrinsic_comparison", "batch": index}), flush=True)
        summaries = [json.loads(row[0]) for row in db.execute("SELECT stats FROM batches ORDER BY batch")]
        require(batch_count == parent["batches"] and len(summaries) == batch_count, "Input/checkpoint batch accounting differs")
        if selected_ids is not None:
            found = {i for value in summaries for i in value["selected"]}
            require(found == selected_ids, "Some selected document IDs were not found")
        else:
            require(sum(s["documents"] for s in summaries) == parent["documents"], "Input document accounting differs")
        report = {"state": "complete", "scope": "full corpus" if selected_ids is None else "declared document subset",
                  "identity": identity, "gpu_used": False, "retrieval_quality_measured": False,
                  "controlled_chunker_comparison": len({canonical_json(m["identity"]["config"]) for m in manifests.values()}) == 1,
                  "strategies": {}}
        for name in stores:
            value = combine_statistics(s["strategies"][name] for s in summaries)
            value["config"] = manifests[name]["identity"]["config"]
            value["strategy"] = manifests[name]["identity"]["strategy"]
            value["retrieval_tokens_quantiles"] = {str(p): quantile(value["tokens"], p) for p in (0.5, 0.9, 0.95, 0.99)}
            value["compressed_payload_bytes"] = sum((p / "documents.jsonl.gz").stat().st_size for p in Path(stores[name]).glob("batch-*"))
            value["duplicate_groups"] = db.execute("SELECT COUNT(*) FROM groups WHERE strategy=? AND n>1", (name,)).fetchone()[0] if exact_duplicates else None
            value["duplicate_excess_chunks"] = db.execute("SELECT COALESCE(SUM(n-1),0) FROM groups WHERE strategy=? AND n>1", (name,)).fetchone()[0] if exact_duplicates else None
            report["strategies"][name] = value
        write_json(root / "REPORT.json", report)
        write_csv(root / "SUMMARY.csv", ["run", "strategy", "documents", "chunks", "short_chunks", "retrieval_tokens", "p50_tokens", "p95_tokens", "overlap_characters", "payload_bytes"], (
            {"run": name, "strategy": s["strategy"], "documents": s["counts"]["documents"], "chunks": s["counts"]["chunks"],
             "short_chunks": s["counts"].get("short_chunks", 0), "retrieval_tokens": s["counts"].get("retrieval_tokens", 0),
             "p50_tokens": s["retrieval_tokens_quantiles"]["0.5"], "p95_tokens": s["retrieval_tokens_quantiles"]["0.95"],
             "overlap_characters": s["counts"].get("overlapping_source_characters", 0), "payload_bytes": s["compressed_payload_bytes"]}
            for name, s in report["strategies"].items()))
        write_json(marker, {"state": "complete", "identity": identity})
        return report


def prepare_pilots(config, *, documents=100, allow_unreviewed=False):
    require(documents > 0, "Positive pilot document count required")
    qa = Path(config["qa_dir"])
    sample = frozen_sample(qa / "sample.json")
    accepted = set()
    if (qa / "decisions.jsonl").exists():
        from .review_common import jsonl
        accepted = {r["doc_id"] for r in jsonl(qa / "decisions.jsonl") if r["decision"] == "accept"}
    pool = [r for r in sample["selection"] if allow_unreviewed or r["doc_id"] in accepted]
    require(len(pool) >= documents, f"Only {len(pool)} eligible QA documents; complete reviews, lower --documents, or explicitly --allow-unreviewed for debugging")
    selected = sorted(r["doc_id"] for r in sorted(pool, key=lambda r: priority(config["seed"], "pilot", r["doc_id"]))[:documents])
    parent, source = completed(config["input_dir"])
    root = Path(config["pilot_dir"])
    separate(root, [config["input_dir"], *config["stores"].values()])
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / "inputs"
    identity = {**parent["identity"], "selection": {"source": source, "doc_ids": selected,
               "sample_signature": sample["signature"], "allow_unreviewed": allow_unreviewed,
               "code": file_hash(Path(__file__))}, "batch_documents": 25, "max_documents": documents}
    with output_store(input_dir, identity, resume=(input_dir / "manifest.json").exists()) as (directory, manifest, committed):
        if manifest["state"] != "complete":
            done = sum(info["documents"] for _, info in committed)
            packets, seen = [], 0
            for path, _ in batches(config["input_dir"], parent["signature"]):
                for packet in read_rows(path):
                    if packet["doc_id"] not in selected:
                        continue
                    seen += 1
                    if seen <= done:
                        continue
                    packets.append(packet)
                    if len(packets) == 25:
                        committed.append(commit_batch(directory, manifest["signature"], len(committed), packets))
                        packets = []
            require(seen == len(selected), "Pilot document IDs missing from source")
            if packets:
                committed.append(commit_batch(directory, manifest["signature"], len(committed), packets))
            finish(directory, manifest, committed, True)
    base = settings(read_json(config["chunk_config"]))
    variants = [(f"{s}_base", s, {}) for s in ("fixed", "structure", "semantic")]
    sizes = {max(base["min_tokens"], base["overlap_tokens"] + 1, round(base["target_tokens"] * 2 / 3)),
             min(base["max_tokens"], round(base["target_tokens"] * 4 / 3))} - {base["target_tokens"]}
    overlaps = {0, min(base["target_tokens"] - 1, base["overlap_tokens"] * 2)} - {base["overlap_tokens"]}
    thresholds = {round(max(-1, base["semantic_threshold"] - 0.1), 4),
                  round(min(1, base["semantic_threshold"] + 0.1), 4)} - {base["semantic_threshold"]}
    variants += [(f"fixed_size_{n}", "fixed", {"target_tokens": n}) for n in sorted(sizes)]
    variants += [(f"structure_overlap_{n}", "structure", {"overlap_tokens": n}) for n in sorted(overlaps)]
    variants += [(f"semantic_threshold_{str(n).replace('.', '_')}", "semantic", {"semantic_threshold": n}) for n in sorted(thresholds)]
    plan = {"input_dir": str(input_dir), "sample_signature": sample["signature"], "documents": len(selected),
            "input_signature": completed(input_dir)[0]["signature"], "allow_unreviewed": allow_unreviewed, "variants": []}
    configs = root / "configs"
    for name, strategy, overrides in variants:
        path = configs / f"{name}.json"
        value = settings({**base, **overrides})
        plan["variants"].append({"name": name, "strategy": strategy, "config_path": str(path), "config": value,
                                 "output_dir": str(root / name), "gpu_required": strategy == "semantic"})
    plan["signature"] = text_hash(canonical_json(plan))
    if (root / "plan.json").exists():
        require(read_json(root / "plan.json") == plan, "Pilot plan differs; choose a new pilot_dir")
    configs.mkdir(exist_ok=True)
    for variant in plan["variants"]:
        write_json(Path(variant["config_path"]), variant["config"])
    pilot_workflow = {**config, "input_dir": str(input_dir), "qa_dir": str(root / "qa"),
        "random_documents": min(documents, config["random_documents"]),
        "stores": {s: str(root / f"{s}_base") for s in ("fixed", "structure", "semantic")},
        "comparison_dir": str(root / "comparison"), "pilot_dir": str(root / "nested_pilots_unused")}
    write_json(root / "workflow.json", pilot_workflow)
    for variant in plan["variants"]:
        write_json(root / f"workflow_{variant['name']}.json", {**pilot_workflow,
            "stores": {**pilot_workflow["stores"], variant["strategy"]: variant["output_dir"]}})
    write_json(root / "plan.json", plan)
    return plan


def run_cpu_pilots(config):
    plan = read_json(Path(config["pilot_dir"]) / "plan.json")
    require(text_hash(canonical_json({k: v for k, v in plan.items() if k != "signature"})) == plan["signature"], "Pilot plan was edited")
    require(completed(plan["input_dir"])[0]["signature"] == plan["input_signature"], "Pilot input changed")
    for variant in plan["variants"]:
        if variant["gpu_required"]:
            continue
        output = Path(variant["output_dir"])
        require(read_json(variant["config_path"]) == variant["config"], "Pilot config differs from its frozen plan")
        build_parallel(plan["input_dir"], output, config["tokenizer_dir"], strategy=variant["strategy"],
            config=variant["config"], workers=config["workers"], resume=(output / "manifest.json").exists())
        validate_parallel(plan["input_dir"], output, config["tokenizer_dir"],
            Path(config["pilot_dir"]) / "validation" / variant["name"], workers=config["workers"], resume=True)
    return {"state": "CPU pilots complete", "GPU_variants": [v["name"] for v in plan["variants"] if v["gpu_required"]]}


def bundle_semantic_pilot(config, name):
    from .chunk_full_bundle import make_full_bundle
    plan = read_json(Path(config["pilot_dir"]) / "plan.json")
    require(text_hash(canonical_json({k: v for k, v in plan.items() if k != "signature"})) == plan["signature"], "Pilot plan was edited")
    variant = next((row for row in plan["variants"] if row["name"] == name), None)
    require(variant is not None and variant["gpu_required"], "Choose a semantic variant from the pilot plan")
    require(read_json(variant["config_path"]) == variant["config"], "Pilot config differs from the frozen plan")
    require(completed(plan["input_dir"])[0]["signature"] == plan["input_signature"], "Pilot input changed")
    return make_full_bundle(plan["input_dir"], config["tokenizer_dir"],
        Path(config["pilot_dir"]) / "colab" / f"{name}.zip", config_path=variant["config_path"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stage5_workflow.json")
    commands = parser.add_subparsers(dest="command", required=True)
    fixed = commands.add_parser("full-fixed")
    fixed.add_argument("--stop-after-batches", type=int)
    compare = commands.add_parser("compare")
    compare.add_argument("--strategies", nargs="+", default=["fixed", "structure", "semantic"])
    compare.add_argument("--ids-file", type=Path)
    compare.add_argument("--exact-duplicates", action="store_true")
    pilots = commands.add_parser("prepare-pilots")
    pilots.add_argument("--documents", type=int, default=100)
    pilots.add_argument("--allow-unreviewed", action="store_true")
    commands.add_parser("run-cpu-pilots")
    bundle = commands.add_parser("bundle-semantic-pilot")
    bundle.add_argument("--variant", required=True)
    args = parser.parse_args()
    try:
        config = workflow(args.config)
        if args.command == "full-fixed":
            result = full_fixed(config, stop_after_batches=args.stop_after_batches)
        elif args.command == "compare":
            require(all(s in config["stores"] for s in args.strategies), "Unknown configured strategy")
            result = compare_stores(config["input_dir"], {n: config["stores"][n] for n in args.strategies}, config["comparison_dir"],
                selected_ids=read_json(args.ids_file) if args.ids_file else None, exact_duplicates=args.exact_duplicates)
        elif args.command == "prepare-pilots":
            result = prepare_pilots(config, documents=args.documents, allow_unreviewed=args.allow_unreviewed)
        elif args.command == "run-cpu-pilots":
            result = run_cpu_pilots(config)
        else:
            result = bundle_semantic_pilot(config, args.variant)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, sqlite3.Error, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
