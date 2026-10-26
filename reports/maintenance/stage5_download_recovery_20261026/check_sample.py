"""Bounded local check and review packet; not a full validator or human QA.

Uses the existing local tokenizer only. Checks up to 12 chunks per selected
document/strategy across evenly spaced batches and all repaired payload batches.
"""
import csv
from itertools import islice
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from vibiomir.chunk_batches import read_json, read_rows
from vibiomir.chunk_models import load_tokenizer
from vibiomir.chunk_pipeline import verify_chunk
from vibiomir.io import write_json


def positions(size, limit):
    if size <= limit:
        return list(range(size))
    return sorted({round(i * (size - 1) / (limit - 1)) for i in range(limit)})


def main():
    report_dir = Path(__file__).resolve().parent
    recovery = read_json(report_dir / "RECOVERY.json")
    semantic = ROOT / recovery["destination"] / "merged"
    structure = ROOT / "artifacts/chunks_stage5_full_structure"
    manifests = {"semantic": read_json(semantic / "manifest.json"),
                 "structure": read_json(structure / "manifest.json")}
    count = manifests["semantic"]["batches"]
    selected = set(positions(count, 20)) | {862, 863}
    for item in recovery["missing_from_original_extractions"]:
        parts = Path(item["relative_path"]).parts
        if len(parts) == 3:
            index = int(parts[1].split("-")[1])
            if parts[0] == "shard_1":
                index += 863
            selected.add(index)
    tokenizer, signature = load_tokenizer(ROOT / "artifacts/tokenizers/bge_m3_stage5")
    for manifest in manifests.values():
        if manifest["identity"]["tokenizer"] != signature or manifest["state"] != "complete":
            raise ValueError("Complete stores with matching tokenizer required")
    stats = {name: {"documents": 0, "chunks_checked": 0, "by_language_hint": {}} for name in manifests}
    review = []
    reviewed_languages = {}
    for batch in sorted(selected):
        name = f"batch-{batch:06d}"
        # Only the first 250 rows are read; rows 0 and 249 are selected when present.
        packets = {i: p for i, p in enumerate(islice(read_rows(ROOT / "artifacts/chunk_inputs_stage5_full" / name), 250))
                   if i in (0, 249)}
        for strategy, directory in (("semantic", semantic), ("structure", structure)):
            for i, row in enumerate(islice(read_rows(directory / name), 250)):
                if i not in packets:
                    continue
                packet = packets[i]
                if any(row[k] != packet[k] for k in ("doc_id", "derived_version_id", "availability")):
                    raise ValueError("Sample document mapping differs")
                language = packet.get("language_hint") or "unknown"
                stats[strategy]["documents"] += 1
                regions = {r["region_id"]: r for r in packet["regions"]}
                chunks = row["chunks"]
                for index in positions(len(chunks), 12):
                    chunk = chunks[index]
                    manifest = manifests[strategy]
                    verify_chunk(chunk, packet, regions[chunk["region_id"]], tokenizer,
                                 manifest["identity"]["config"], manifest["signature"], strategy)
                    stats[strategy]["chunks_checked"] += 1
                    hints = stats[strategy]["by_language_hint"]
                    hints[language] = hints.get(language, 0) + 1
                key = (strategy, language)
                if chunks and reviewed_languages.get(key, 0) < 3:
                    for index in positions(len(chunks), 3):
                        c = chunks[index]
                        review.append({"strategy": strategy, "global_batch": batch,
                            "doc_id": row["doc_id"], "language_hint": language,
                            "chunk_id": c["chunk_id"], "token_count": c["token_count"],
                            "context_prefix": c["context_prefix"], "chunk_text": c["chunk_text"],
                            "region_id": c["region_id"], "region_start": c["region_start"],
                            "region_end": c["region_end"], "review_status": "pending"})
                    reviewed_languages[key] = reviewed_languages.get(key, 0) + 1
    summary = {"validation": "passed", "scope": "Bounded deterministic source/token checks, not full validation or human QA",
               "input_batches_sampled": sorted(selected), "stores": stats,
               "review_rows": len(review), "human_qa": "pending", "gpu_required": False}
    write_json(report_dir / "SAMPLE_CHECK.json", summary)
    with (report_dir / "REVIEW_SAMPLE.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(review[0]))
        writer.writeheader()
        writer.writerows(review)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
