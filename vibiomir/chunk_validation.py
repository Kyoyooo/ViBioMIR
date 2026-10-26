"""Full CPU chunk validation using the original validator on every batch.

Batch-local views retain identity/signatures and gzip bytes. Only temporary
manifest totals and the batch-local index change. Global accounting, ordering
and checksums are checked before aggregation. Original chunk identity modules
remain untouched. Completed batch reports can be resumed after interruption.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
import copy
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time

from .chunk_batches import batches, read_json
from .chunk_models import load_tokenizer
from .chunk_pipeline import code_signature, validate_chunks
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock

TOTALS = ("documents", "chunks", "documents_without_chunks", "short_chunks",
          "overlapping_source_characters", "explicit_question_regions",
          "question_regions_with_multiple_chunks", "table_regions", "unmapped_legacy_blocks")
_TOKENIZER = _SIGNATURE = None


def initialize(tokenizer_dir):
    global _TOKENIZER, _SIGNATURE
    os.environ.update(USE_TORCH="0", USE_TF="0", USE_FLAX="0",
                      TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="1")
    _TOKENIZER, _SIGNATURE = load_tokenizer(tokenizer_dir)


def validate_single_batch(job, tokenizer, signature):
    index, input_path, output_path, parent, manifest, left, right = job
    with tempfile.TemporaryDirectory(prefix="vibiomir-validate-") as temporary:
        paths = [Path(temporary) / name for name in ("input", "output")]
        for target, source, original, descriptor in zip(paths, (input_path, output_path),
                                                       (parent, manifest), (left, right)):
            batch = target / "batch-000000"
            batch.mkdir(parents=True)
            # A read-only reference, never a copy or edit of the source payload.
            (batch / "documents.jsonl.gz").symlink_to(Path(source).resolve() / "documents.jsonl.gz")
            info = dict(descriptor, index=0)
            write_json(batch / "batch.json", info)
            local = copy.deepcopy(original)
            local.update(batches=1, documents=info["documents"], chunks=info["chunks"])
            write_json(target / "manifest.json", local)
        report = validate_chunks(paths[0], paths[1], tokenizer, signature)
    return {"index": index, "input_sha256": left["sha256"], "output_sha256": right["sha256"],
            "report": report}


def worker(job):
    return validate_single_batch(job, _TOKENIZER, _SIGNATURE)


def aggregate(reports):
    reports = list(reports)
    if not reports:
        raise ValueError("No batch reports")
    result = copy.deepcopy(reports[0])
    for key in TOTALS:
        result[key] = 0
    result["by_language_hint"] = {}
    constants = set(result) - set(TOTALS) - {"by_language_hint"}
    for report in reports:
        if any(report[k] != result[k] for k in constants):
            raise ValueError("Batch validation contracts differ")
        for key in TOTALS:
            result[key] += report[key]
        for language, values in report["by_language_hint"].items():
            totals = result["by_language_hint"].setdefault(language, {k: 0 for k in values})
            for key, value in values.items():
                totals[key] = max(totals[key], value) if key == "max_tokens" else totals[key] + value
    return result


@contextmanager
def read_lock(directory):
    with (Path(directory) / ".store.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_parallel(input_dir, output_dir, tokenizer_dir, checkpoint_dir, *, workers=4, resume=False):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    input_dir, output_dir, tokenizer_dir, checkpoint_dir = map(Path,
        (input_dir, output_dir, tokenizer_dir, checkpoint_dir))
    if not (input_dir / "manifest.json").is_file() or not (output_dir / "manifest.json").is_file():
        raise ValueError("Missing input/output manifest")
    if checkpoint_dir.exists() and any(checkpoint_dir.iterdir()) and not resume:
        raise ValueError("Checkpoint exists; use --resume")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with read_lock(input_dir), writer_lock(output_dir), writer_lock(checkpoint_dir):
        parent, manifest = read_json(input_dir / "manifest.json"), read_json(output_dir / "manifest.json")
        if parent["state"] != "complete" or manifest["state"] != "complete":
            raise ValueError("Complete input and output required")
        for item in (parent, manifest):
            if text_hash(canonical_json(item["identity"])) != item["signature"]:
                raise ValueError("Manifest identity signature differs")
        _, signature = load_tokenizer(tokenizer_dir)
        if manifest["identity"]["parent_signature"] != parent["signature"] or manifest["identity"]["tokenizer"] != signature:
            raise ValueError("Wrong input/tokenizer")
        identity = {"input_manifest_sha256": file_hash(input_dir / "manifest.json"),
                    "output_manifest_sha256": file_hash(output_dir / "manifest.json"),
                    "tokenizer": signature, "validator_code": code_signature(),
                    "runner_sha256": file_hash(Path(__file__))}
        marker = checkpoint_dir / "manifest.json"
        if marker.exists() and read_json(marker)["identity"] != identity:
            raise ValueError("Validation checkpoint belongs to different data/code/tokenizer")
        write_json(marker, {"identity": identity, "state": "running"})
        print(json.dumps({"phase": "verify_batch_checksums", "output": str(output_dir)}), flush=True)
        lefts = list(batches(input_dir, parent["signature"]))
        rights = list(batches(output_dir, manifest["signature"]))
        if len(lefts) != len(rights) or len(lefts) != parent["batches"] or len(rights) != manifest["batches"]:
            raise ValueError("Global batch accounting differs")
        jobs, results = [], {}
        for index, ((ip, left), (op, right)) in enumerate(zip(lefts, rights)):
            if any(left[k] != right[k] for k in ("documents", "first_doc_id", "last_doc_id")):
                raise ValueError(f"Batch geometry differs: {index}")
            cached = checkpoint_dir / f"batch-{index:06d}.json"
            if cached.exists():
                record = read_json(cached)
                if record["index"] != index or record["input_sha256"] != left["sha256"] or record["output_sha256"] != right["sha256"]:
                    raise ValueError(f"Changed checkpoint batch: {index}")
                results[index] = record["report"]
            else:
                jobs.append((index, str(ip), str(op), parent, manifest, left, right))
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                 initializer=initialize, initargs=(str(tokenizer_dir.resolve()),)) as pool:
            iterator, pending = iter(jobs), {}
            def submit():
                job = next(iterator, None)
                if job is not None:
                    pending[pool.submit(worker, job)] = job[0]
            for _ in range(workers * 2):
                submit()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    record = future.result()
                    write_json(checkpoint_dir / f"batch-{index:06d}.json", record)
                    results[index] = record["report"]
                    if len(results) % 25 == 0 or len(results) == 1 or not pending:
                        print(json.dumps({"phase": "validated", "completed": len(results), "total": len(lefts),
                                          "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
                    submit()
        if len(results) != len(lefts):
            raise ValueError("Missing validation results")
        report = aggregate(results[i] for i in range(len(lefts)))
        if report["documents"] != parent["documents"] or report["documents"] != manifest["documents"] or report["chunks"] != manifest["chunks"]:
            raise ValueError("Global manifest totals differ from validated rows")
        write_json(output_dir / "VALIDATION.json", report)
        write_json(marker, {"identity": identity, "state": "complete", "batches": len(results),
                   "workers": workers, "elapsed_seconds_last_invocation": round(time.monotonic() - started, 3),
                   "method": "Original validate_chunks over every batch; global accounting and checksums reconciled"})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-dir", "output-dir", "tokenizer-dir", "checkpoint-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(validate_parallel(args.input_dir, args.output_dir, args.tokenizer_dir,
              args.checkpoint_dir, workers=args.workers, resume=args.resume), indent=2))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
