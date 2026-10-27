"""Bounded, dependency-free CPU retrieval pilot for controlled chunk comparisons.

This reference does not build the production S07 index. It indexes every chunk
of one explicit document subset, or refuses the memory cap; never silently
truncates different strategies to unequal corpora.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import resource
import sqlite3
import time

from .chunk_batches import read_json, read_rows
from .io import canonical_json, file_hash, text_hash, write_json
from .review_common import completed, require, separate, workflow
from .retrieval_evaluation import normalize

ANALYZER = "unicode_words_han_unigrams_bigrams_reference_v1"


def analyze(text):
    result = []
    for word in re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", normalize(text), re.UNICODE):
        if "\u3400" <= word[0] <= "\u9fff":
            result.extend("zh1:" + char for char in word)
            result.extend("zh2:" + word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.append("w:" + word)
    return result


class BM25:
    def __init__(self, chunks, *, k1=1.2, b=0.75):
        require(math.isfinite(k1) and k1 > 0 and math.isfinite(b) and 0 <= b <= 1, "Invalid BM25 k1/b")
        self.chunks, self.k1, self.b = chunks, k1, b
        self.lengths, self.postings = [], defaultdict(list)
        for index, chunk in enumerate(chunks):
            counts = Counter(analyze(chunk["retrieval_text"]))
            self.lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                self.postings[term].append((index, frequency))
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0

    def search(self, query):
        scores = defaultdict(float)
        for term, query_frequency in Counter(analyze(query)).items():
            postings = self.postings.get(term, [])
            if not postings:
                continue
            idf = math.log(1 + (len(self.chunks) - len(postings) + 0.5) / (len(postings) + 0.5))
            for index, frequency in postings:
                denominator = frequency + self.k1 * (1 - self.b + self.b * self.lengths[index] / self.average)
                scores[index] += query_frequency * idf * frequency * (self.k1 + 1) / denominator
        return sorted(scores.items(), key=lambda item: (-item[1], self.chunks[item[0]]["doc_id"], self.chunks[item[0]]["chunk_id"]))


def select_prediction(query_id, ranked, chunks, *, doc_cap=5, chunk_cap=20):
    require(type(doc_cap) is int and doc_cap > 0 and type(chunk_cap) is int and chunk_cap > 0, "Positive integer output caps required")
    docs, output, seen = [], [], set()
    for index, _ in ranked:
        doc_id = chunks[index]["doc_id"]
        if doc_id not in docs and len(docs) < doc_cap:
            docs.append(doc_id)
    for index, score in ranked:
        chunk = chunks[index]
        key = (chunk["doc_id"], normalize(chunk["chunk_text"]))
        if chunk["doc_id"] not in docs or key in seen:
            continue
        seen.add(key)
        output.append({"doc_id": chunk["doc_id"], "chunk_text": chunk["chunk_text"], "chunk_order": len(output)})
        if len(output) == chunk_cap:
            break
    return {"id": query_id, "relevant_docs": docs, "relevant_chunks": output}


def load_subset(config, strategy, doc_ids, *, max_chunks):
    require(strategy in config["stores"] and type(max_chunks) is int and max_chunks > 0, "Unknown store or invalid memory cap")
    ids = set(doc_ids)
    require(ids and all(type(i) is int for i in ids), "Explicit integer document subset required")
    parent, _ = completed(config["input_dir"])
    path = Path(config["stores"][strategy])
    manifest, snapshot = completed(path, validated=True)
    require(manifest["identity"]["parent_signature"] == parent["signature"], "Store belongs to another corpus")
    catalog = read_json(Path(config["qa_dir"]) / "catalog_manifest.json")
    require(catalog["state"] == "complete" and catalog["identity"]["source"]["signature"] == parent["signature"], "Matching QA catalog required")
    with sqlite3.connect((Path(config["qa_dir"]) / "catalog.sqlite3").resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        locations = {}
        for doc_id in ids:
            row = db.execute("SELECT batch,version FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
            require(row is not None, f"Unknown catalog ID: {doc_id}")
            locations[doc_id] = row
    chunks, found = [], set()
    for index in sorted({row[0] for row in locations.values()}):
        batch = path / f"batch-{index:06d}"
        info = read_json(batch / "batch.json")
        require(info["index"] == index and info["signature"] == manifest["signature"] and
                file_hash(batch / "documents.jsonl.gz") == info["sha256"], "Selected chunk batch changed")
        for row in read_rows(batch):
            if row["doc_id"] in ids:
                require(row["derived_version_id"] == locations[row["doc_id"]][1], "Selected chunk version differs")
                found.add(row["doc_id"])
                require(len(chunks) + len(row["chunks"]) <= max_chunks,
                        "Reference index exceeds --max-chunks; choose a smaller shared document subset or a larger explicit cap. Nothing was truncated")
                chunks.extend(row["chunks"])
    require(found == ids, "Missing selected documents")
    require(len({c["chunk_id"] for c in chunks}) == len(chunks), "Duplicate chunk IDs in selected store")
    return chunks, snapshot, text_hash(canonical_json([parent["signature"], sorted(ids)])), manifest["identity"]


def run(config, strategy, questions_path, ids, output_dir, *, split="dev", compiled=True,
        max_chunks=25000, k1=1.2, b=0.75, doc_cap=5, chunk_cap=20):
    data = read_json(questions_path)
    if compiled:
        require(data.get("human_reviewed") is True, "Compile reviewed local labels first; --queries supports an earlier unlabeled candidate-pool run")
        queries = data["queries"]
    else:
        require(isinstance(data, list), "Candidate-pool queries must be a JSON array")
        queries = data
    queries = [q for q in queries if q["split"] == split]
    require(queries and len({q["id"] for q in queries}) == len(queries), "Nonempty unique query split required")
    require(all(type(q["id"]) is int and isinstance(q["query"], str) and q["query"].strip() for q in queries), "Fill human-authored questions first")
    if compiled:
        require(all(set(q["relevant_docs"]) <= set(ids) for q in queries), "Gold positives are outside the chosen corpus subset")
    output = Path(output_dir)
    separate(output, [config["input_dir"], *config["stores"].values()])
    chunks, source, corpus_signature, chunk_identity = load_subset(config, strategy, ids, max_chunks=max_chunks)
    corpus_parent, _ = completed(config["input_dir"])
    if compiled:
        require(data["corpus_signature"] == corpus_parent["signature"], "Benchmark evidence belongs to another corpus")
    policy = {"doc_cap": doc_cap, "chunk_cap": chunk_cap, "deduplicate": "normalized exact within doc"}
    configuration = {"corpus_signature": corpus_signature, "chunk_signature": source["signature"],
                     "chunker": chunk_identity["strategy"], "chunk_config": chunk_identity["config"],
                     "implementation": {"reference_bm25.py": file_hash(Path(__file__)),
                                        "retrieval_evaluation.py": file_hash(Path(__file__).with_name("retrieval_evaluation.py"))},
                     "analyzer": ANALYZER, "retriever": "BM25 reference", "k1": k1, "b": b, "policy": policy}
    signature = text_hash(canonical_json(configuration))
    output.mkdir(parents=True, exist_ok=True)
    if (output / "run.json").exists():
        old = read_json(output / "run.json")
        require(old["configuration_signature"] == signature and old["benchmark_sha256"] == file_hash(questions_path) and old["split"] == split,
                "Run output belongs to another configuration/query snapshot; use a new directory")
        require(old["predictions_sha256"] == file_hash(output / "predictions.json"), "Saved predictions changed")
        return old
    require(not any(output.iterdir()), "Run output contains incomplete files; inspect it and choose a new output directory")
    started = time.perf_counter()
    index = BM25(chunks, k1=k1, b=b)
    index_seconds = time.perf_counter() - started
    predictions, timings = [], []
    for query in queries:
        started = time.perf_counter()
        ranked = index.search(query["query"])
        predictions.append(select_prediction(query["id"], ranked, chunks, doc_cap=doc_cap, chunk_cap=chunk_cap))
        timings.append(time.perf_counter() - started)
    write_json(output / "predictions.json", predictions)
    ordered = sorted(timings)
    metadata = {"benchmark_sha256": file_hash(questions_path), "split": split, "purpose": "reviewed evaluation" if compiled else "unlabeled candidate pool",
                "corpus_signature": corpus_signature, "evaluation_policy": policy, "configuration_signature": signature,
                "configuration": configuration, "chunk_store": source, "chunks_indexed": len(chunks),
                "selected_document_ids": sorted(set(ids)), "predictions_sha256": file_hash(output / "predictions.json"),
                "index_seconds": index_seconds, "latency": {"mode": "warm in-memory CPU reference",
                    "p50_seconds": ordered[(len(ordered) - 1) // 2], "p95_seconds": ordered[math.ceil(0.95 * len(ordered)) - 1]},
                "analyzer_warning": "Reference Unicode/Han analyzer, not a production biomedical language analyzer",
                "parent_peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2,
                "index_disk_bytes": None, "gpu_hours": 0,
                "gpu_used": False, "full_corpus_index": False}
    write_json(output / "run.json", metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stage5_workflow.json")
    parser.add_argument("--strategy", required=True, choices=["fixed", "structure", "semantic"])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--benchmark", type=Path)
    source.add_argument("--queries", type=Path)
    parser.add_argument("--ids-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "dev", "holdout"], default="dev")
    parser.add_argument("--max-chunks", type=int, default=25000)
    parser.add_argument("--k1", type=float, default=1.2)
    parser.add_argument("--b", type=float, default=0.75)
    parser.add_argument("--doc-cap", type=int, default=5)
    parser.add_argument("--chunk-cap", type=int, default=20)
    args = parser.parse_args()
    try:
        result = run(workflow(args.config), args.strategy, args.benchmark or args.queries, read_json(args.ids_file), args.output_dir,
            split=args.split, compiled=args.benchmark is not None, max_chunks=args.max_chunks,
            k1=args.k1, b=args.b, doc_cap=args.doc_cap, chunk_cap=args.chunk_cap)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
