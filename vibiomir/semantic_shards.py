"""Two disjoint semantic shards, optional legacy prefix, resumable merge.

Shard batch numbers are local. Their manifests record global input intervals;
only the merge produces the ordinary full-corpus chunk store for validation.
"""
from contextlib import ExitStack
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import argparse
import fcntl
import gzip
import json
import multiprocessing
from pathlib import Path
import resource
import shutil
import tempfile
import time

from .chunk_batches import batches, finish, output_store, read_json, read_rows
from .chunk_colab import restore_checkpoint, save_checkpoint
from .chunk_models import load_tokenizer, MODEL_ID, MODEL_REVISION
from .chunk_parallel import CachedTokenizer
from .chunk_pipeline import code_signature
from .chunking import settings
from .io import canonical_json, file_hash, text_hash, write_json
from .semantic_compute import (PackedCUDAEncoder, initialize_preparer, ordered_preparation,
                               render_wave, waves)


def execution_code():
    return {name: file_hash(Path(__file__).parent / name)
            for name in ("semantic_compute.py", "semantic_shards.py", "chunk_parallel.py")}


def read_plan(path):
    plan = read_json(path)
    signature = plan.pop("signature")
    if text_hash(canonical_json(plan)) != signature:
        raise ValueError("Plan checksum mismatch; do not manually edit a shared plan")
    plan["signature"] = signature
    ranges = plan["ranges"]
    if (len(ranges) != 2 or ranges[0][0] != plan["seed_batches"] or
            ranges[0][1] != ranges[1][0] or ranges[1][1] != plan["input_batches"] or
            any(a > b for a, b in ranges)):
        raise ValueError("Plan intervals contain a gap or overlap")
    return plan


def expected_identity(parent, config, tokenizer_signature, registry):
    return {"operation": "build-chunks", "parent_signature": parent["signature"],
            "code": code_signature(), "strategy": "semantic", "config": config,
            "tokenizer": tokenizer_signature,
            "encoder": {k: registry[k] for k in ("model_id", "revision", "pooling", "dtype", "parameters")}}


def check_common(identity, plan):
    expected = {"operation": "build-chunks", "parent_signature": plan["parent_signature"],
                "code": plan["code"], "strategy": "semantic", "config": plan["config"],
                "tokenizer": plan["tokenizer"]}
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ValueError("Semantic checkpoint does not match shared input/config/tokenizer/code")
    encoder = identity["encoder"]
    if (encoder["model_id"], encoder["revision"], encoder["pooling"], encoder["dtype"]) != (MODEL_ID, MODEL_REVISION, "CLS + L2", "float16"):
        raise ValueError("Checkpoint uses a different semantic encoder")
    if plan.get("seed_identity") and identity != plan["seed_identity"]:
        raise ValueError("Encoder parameters differ from the legacy prefix")


def checked_manifest(directory):
    manifest = read_json(Path(directory) / "manifest.json")
    if text_hash(canonical_json(manifest["identity"])) != manifest["signature"]:
        raise ValueError("Checkpoint identity/signature mismatch")
    return manifest


def snapshot_legacy_prefix(source, destination):
    source = Path(source)
    with ExitStack() as stack:
        if (source / ".store.lock").is_file():
            lock = stack.enter_context((source / ".store.lock").open("rb"))
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Stop the old semantic writer before snapshotting its prefix") from exc
        manifest = checked_manifest(source)
        if manifest["identity"].get("strategy") != "semantic" or manifest.get("partition"):
            raise ValueError("Legacy prefix must be the original semantic output")
        save_checkpoint(source, destination)


def make_plan(input_dir, tokenizer_dir, output, *, config=None, seed_dir=None):
    output, input_dir = Path(output), Path(input_dir)
    if output.exists():
        raise ValueError("Shared plan already exists; reuse it or choose a new shared run directory")
    parent = read_json(input_dir / "manifest.json")
    if parent.get("state") != "complete" or parent["identity"]["operation"] != "prepare-chunk-inputs":
        raise ValueError("Completed prepared input is required")
    _, tokenizer_signature = load_tokenizer(tokenizer_dir)
    parts = list(batches(input_dir, parent["signature"]))
    plan = {"version": 1, "parent_signature": parent["signature"], "input_batches": len(parts),
            "documents": parent["documents"], "config": settings(config), "code": code_signature(),
            "execution_code": execution_code(),
            "tokenizer": tokenizer_signature, "seed_batches": 0, "seed_identity": None, "seed_parts": []}
    if seed_dir:
        seed_dir = Path(seed_dir)
        # The owner must stop the old writer before snapshotting/restoring its backup.
        seed = checked_manifest(seed_dir)
        if seed.get("partition"):
            raise ValueError("Legacy prefix must be the original single-run semantic output, not a shard")
        check_common(seed["identity"], plan)
        completed = list(batches(seed_dir, seed["signature"]))
        if len(completed) > len(parts):
            raise ValueError("Legacy prefix is longer than the input")
        for (_, old), (_, source) in zip(completed, parts):
            if any(old[k] != source[k] for k in ("documents", "first_doc_id", "last_doc_id")):
                raise ValueError("Legacy prefix document accounting mismatch")
        plan.update(seed_batches=len(completed), seed_identity=seed["identity"],
                    seed_parts=[info for _, info in completed])
    start = plan["seed_batches"]
    costs = [p.joinpath("documents.jsonl.gz").stat().st_size for p, _ in parts[start:]]
    # Compressed input bytes are a cheap workload proxy; no medical/model inference.
    target, cumulative, split = sum(costs) / 2, 0, start
    best = float("inf")
    for position in range(len(costs) + 1):
        distance = abs(cumulative - target)
        if distance < best:
            best, split = distance, start + position
        if position < len(costs):
            cumulative += costs[position]
    plan["ranges"] = [[start, split], [split, len(parts)]]
    plan["balance_proxy"] = "compressed prepared-input bytes; not measured GPU time"
    plan["signature"] = text_hash(canonical_json(plan))
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, plan)
    return plan


def commit_rows(directory, signature, index, global_index, rows, expected):
    """Stream rows to disk; commit an entire source batch atomically."""
    destination = directory / f"batch-{index:06d}"
    if destination.exists():
        raise ValueError("Refusing to overwrite a committed shard batch")
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=directory) as temporary:
        temporary = Path(temporary)
        count = chunks = 0
        first = last = None
        with gzip.open(temporary / "documents.jsonl.gz", "wt", encoding="utf-8", compresslevel=3) as stream:
            for row in rows:
                if last is not None and row["doc_id"] <= last:
                    raise ValueError("Shard document order mismatch")
                if first is None:
                    first = row["doc_id"]
                last = row["doc_id"]
                count += 1
                chunks += len(row["chunks"])
                stream.write(canonical_json(row) + "\n")
        if (count, first, last) != (expected["documents"], expected["first_doc_id"], expected["last_doc_id"]):
            raise ValueError("Shard/input batch accounting mismatch")
        info = {"index": index, "signature": signature, "global_input_batch": global_index,
                "documents": count, "first_doc_id": first, "last_doc_id": last, "chunks": chunks,
                "sha256": file_hash(temporary / "documents.jsonl.gz")}
        write_json(temporary / "batch.json", info)
        temporary.rename(destination)
    return destination, info


def run_shard(input_dir, tokenizer_dir, plan_path, shard_id, output_dir, *, backup_dir=None,
              cpu_workers=2, max_batch_size=64, max_padded_tokens=8192,
              wave_documents=16, wave_sentences=1024, stop_after_batches=None,
              encoder_factory=PackedCUDAEncoder):
    if shard_id not in (0, 1) or not 0 <= cpu_workers <= 8:
        raise ValueError("shard_id must be 0/1; cpu_workers must be between 0 and 8")
    if min(max_batch_size, wave_documents, wave_sentences) < 1 or max_padded_tokens < 512:
        raise ValueError("Invalid batch/wave budgets")
    if stop_after_batches is not None and stop_after_batches < 1:
        raise ValueError("stop_after_batches must be positive")
    plan, input_dir, output_dir = read_plan(plan_path), Path(input_dir), Path(output_dir)
    parent = read_json(input_dir / "manifest.json")
    tokenizer, token_signature = load_tokenizer(tokenizer_dir)
    if (parent["state"] != "complete" or parent["signature"] != plan["parent_signature"] or
            token_signature != plan["tokenizer"] or code_signature() != plan["code"] or
            execution_code() != plan["execution_code"]):
        raise ValueError("Plan input, tokenizer or original chunk code changed")
    start, end = plan["ranges"][shard_id]
    partition = {"plan_signature": plan["signature"], "shard_id": shard_id, "global_start": start, "global_end": end}
    for candidate in (output_dir, Path(backup_dir) if backup_dir else None):
        if candidate and (candidate / "manifest.json").is_file():
            old = read_json(candidate / "manifest.json")
            if old.get("partition") != partition:
                raise ValueError("Shard output/backup belongs to another plan or shard")
    if backup_dir:
        restore_checkpoint(backup_dir, output_dir)
    if (output_dir / "manifest.json").is_file():
        previous = checked_manifest(output_dir)
        check_common(previous["identity"], plan)
        existing = list(batches(output_dir, previous["signature"]))
        if previous.get("state") == "complete" and len(existing) != end - start:
            raise ValueError("Completed shard has missing batches")
        if len(existing) == end - start:
            with output_store(output_dir, previous["identity"], True) as (directory, manifest, committed):
                result = finish(directory, manifest, committed, True)
            if backup_dir:
                save_checkpoint(output_dir, backup_dir)
            return result
    print(json.dumps({"phase": "loading_encoder", "shard_id": shard_id,
                      "global_input_range": [start, end]}), flush=True)
    encoder = encoder_factory(tokenizer, max_batch_size=max_batch_size,
                              max_padded_tokens=max_padded_tokens, tokenization_window=wave_sentences)
    identity = expected_identity(parent, plan["config"], token_signature, encoder.registry)
    check_common(identity, plan)
    with output_store(output_dir, identity, (output_dir / "manifest.json").is_file()) as (directory, manifest, committed):
        if manifest.get("partition", partition) != partition:
            raise ValueError("Shard partition changed")
        manifest["partition"] = partition
        write_json(directory / "manifest.json", manifest)
        if len(committed) > end - start:
            raise ValueError("Too many completed shard batches")
        parts = list(batches(input_dir, parent["signature"]))
        if len(parts) != plan["input_batches"]:
            raise ValueError("Prepared input batch count differs from the plan")
        for local_index, (_, info) in enumerate(committed):
            source = parts[start + local_index][1]
            if any(info[k] != source[k] for k in ("documents", "first_doc_id", "last_doc_id")):
                raise ValueError("Resumed shard/input geometry mismatch")
        runtime = {"runner": "packed semantic shards", "shard_id": shard_id,
                   "code": {name: file_hash(Path(__file__).parent / name) for name in ("semantic_compute.py", "semantic_shards.py")},
                   "cpu_workers": cpu_workers, "max_batch_size": max_batch_size,
                   "max_padded_tokens": max_padded_tokens, "wave_documents": wave_documents,
                   "wave_sentences": wave_sentences, "encoder": encoder.registry}
        manifest.setdefault("shard_executions", []).append(runtime)
        write_json(directory / "manifest.json", manifest)
        if backup_dir:
            save_checkpoint(directory, backup_dir)
        with ExitStack() as stack:
            pool = stack.enter_context(ProcessPoolExecutor(max_workers=cpu_workers,
                mp_context=multiprocessing.get_context("spawn"), initializer=initialize_preparer,
                initargs=(str(Path(tokenizer_dir).resolve()), plan["config"]))) if cpu_workers else None
            cached = CachedTokenizer(tokenizer)
            added = 0
            for global_index in range(start + len(committed), end):
                path, info = parts[global_index]
                begun = time.monotonic()
                print(json.dumps({"phase": "semantic_batch", "shard_id": shard_id,
                                  "global_input_batch": global_index, "range_end": end}), flush=True)
                prepared = ordered_preparation(read_rows(path), pool, tokenizer=cached,
                    config=plan["config"], prefetch=max(1, cpu_workers * 2))
                def render():
                    for wave in waves(prepared, max_documents=wave_documents, max_sentences=wave_sentences):
                        yield from render_wave(wave, encoder, cached, plan["config"], manifest["signature"], directory)
                committed.append(commit_rows(directory, manifest["signature"], len(committed), global_index, render(), info))
                built = time.monotonic()
                if backup_dir:
                    save_checkpoint(directory, backup_dir, committed[-1][0])
                telemetry = {"phase": "semantic_committed", "shard_id": shard_id,
                    "global_input_batch": global_index, "documents": info["documents"],
                    "chunks": committed[-1][1]["chunks"], "compute_seconds": round(built - begun, 2),
                    "backup_seconds": round(time.monotonic() - built, 2),
                    "parent_peak_rss_gib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 2),
                    "encoder_stats": dict(getattr(encoder, "stats", {}))}
                if hasattr(encoder, "torch"):
                    telemetry["gpu_allocated_gib"] = round(encoder.torch.cuda.memory_allocated() / 1024**3, 2)
                    telemetry["gpu_reserved_gib"] = round(encoder.torch.cuda.memory_reserved() / 1024**3, 2)
                    telemetry["gpu_peak_allocated_gib"] = round(encoder.torch.cuda.max_memory_allocated() / 1024**3, 2)
                print(json.dumps(telemetry), flush=True)
                added += 1
                if stop_after_batches and added >= stop_after_batches:
                    result = finish(directory, manifest, committed, False)
                    if backup_dir:
                        save_checkpoint(directory, backup_dir)
                    return result
        result = finish(directory, manifest, committed, True)
        if backup_dir:
            save_checkpoint(directory, backup_dir)
        return result


def merge_shards(input_dir, plan_path, shard_dirs, output_dir, *, seed_dir=None):
    plan, input_dir = read_plan(plan_path), Path(input_dir)
    if len(shard_dirs) != 2:
        raise ValueError("Exactly two shard directories are required")
    sources = {}
    identity = None
    for shard_id, directory in enumerate(map(Path, shard_dirs)):
        manifest = checked_manifest(directory)
        start, end = plan["ranges"][shard_id]
        partition = {"plan_signature": plan["signature"], "shard_id": shard_id, "global_start": start, "global_end": end}
        if manifest.get("state") != "complete" or manifest.get("partition") != partition:
            raise ValueError(f"Shard {shard_id} is incomplete or belongs to another plan")
        check_common(manifest["identity"], plan)
        identity = manifest["identity"] if identity is None else identity
        if identity != manifest["identity"]:
            raise ValueError("Shard encoder identities differ")
        parts = list(batches(directory, manifest["signature"]))
        if len(parts) != end - start:
            raise ValueError("Shard batch coverage mismatch")
        for local, value in enumerate(parts):
            sources[start + local] = value
    if plan["seed_batches"]:
        if seed_dir is None:
            raise ValueError("This plan requires the original legacy semantic prefix")
        seed_dir = Path(seed_dir)
        seed = checked_manifest(seed_dir)
        if seed["identity"] != identity:
            raise ValueError("Legacy prefix identity differs")
        seed_parts = list(batches(seed_dir, seed["signature"]))
        if len(seed_parts) != plan["seed_batches"]:
            raise ValueError("Legacy prefix changed after partitioning")
        for index, value in enumerate(seed_parts):
            if value[1] != plan["seed_parts"][index]:
                raise ValueError("Legacy prefix batch descriptor changed")
            sources[index] = value
    parent = read_json(input_dir / "manifest.json")
    if (parent["signature"] != plan["parent_signature"] or code_signature() != plan["code"] or
            execution_code() != plan["execution_code"]):
        raise ValueError("Merge input differs from the plan")
    inputs = list(batches(input_dir, parent["signature"]))
    if len(inputs) != plan["input_batches"]:
        raise ValueError("Prepared input batch count differs from the plan")
    if set(sources) != set(range(len(inputs))):
        raise ValueError("Missing or duplicated input-batch coverage")
    output_dir = Path(output_dir)
    with output_store(output_dir, identity, (output_dir / "manifest.json").is_file()) as (directory, manifest, completed):
        if manifest.get("merge_plan", plan["signature"]) != plan["signature"]:
            raise ValueError("Merge output belongs to another plan")
        manifest["merge_plan"] = plan["signature"]
        write_json(directory / "manifest.json", manifest)
        for index, (_, info) in enumerate(completed):
            if index not in sources or info["sha256"] != sources[index][1]["sha256"]:
                raise ValueError("Existing merge batch differs from this plan")
        for index in range(len(completed), len(inputs)):
            source, info = sources[index]
            expected = inputs[index][1]
            if any(info[k] != expected[k] for k in ("documents", "first_doc_id", "last_doc_id")):
                raise ValueError("Source/input geometry differs at merge")
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=directory) as temporary:
                temporary = Path(temporary)
                shutil.copy2(source / "documents.jsonl.gz", temporary / "documents.jsonl.gz")
                if file_hash(temporary / "documents.jsonl.gz") != info["sha256"]:
                    raise ValueError("Merge copy checksum mismatch")
                info = {**info, "index": index, "global_input_batch": index}
                write_json(temporary / "batch.json", info)
                target = directory / f"batch-{index:06d}"
                temporary.rename(target)
            completed.append((target, info))
        manifest["shard_execution_history"] = [read_json(Path(p) / "manifest.json").get("shard_executions", []) for p in shard_dirs]
        if plan["seed_batches"]:
            manifest["legacy_encoder_executions"] = seed.get("encoder_executions", [])
        return finish(directory, manifest, completed, True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    prepare = commands.add_parser("plan")
    prepare.add_argument("--input-dir", type=Path, required=True)
    prepare.add_argument("--tokenizer-dir", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--config", type=Path, default=Path("configs/chunking_stage5.json"))
    prepare.add_argument("--seed-dir", type=Path)
    run = commands.add_parser("run")
    for name in ("input-dir", "tokenizer-dir", "plan", "output-dir"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--shard-id", type=int, choices=[0, 1], required=True)
    run.add_argument("--backup-dir", type=Path)
    run.add_argument("--cpu-workers", type=int, default=2)
    run.add_argument("--max-batch-size", type=int, default=64)
    run.add_argument("--max-padded-tokens", type=int, default=8192)
    run.add_argument("--wave-documents", type=int, default=16)
    run.add_argument("--wave-sentences", type=int, default=1024)
    run.add_argument("--stop-after-batches", type=int)
    merge = commands.add_parser("merge")
    merge.add_argument("--input-dir", type=Path, required=True)
    merge.add_argument("--plan", type=Path, required=True)
    merge.add_argument("--shard-0", type=Path, required=True)
    merge.add_argument("--shard-1", type=Path, required=True)
    merge.add_argument("--seed-dir", type=Path)
    merge.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "plan":
            result = make_plan(args.input_dir, args.tokenizer_dir, args.output,
                               config=read_json(args.config), seed_dir=args.seed_dir)
        elif args.operation == "merge":
            result = merge_shards(args.input_dir, args.plan, [args.shard_0, args.shard_1], args.output_dir, seed_dir=args.seed_dir)
        else:
            result = run_shard(args.input_dir, args.tokenizer_dir, args.plan, args.shard_id, args.output_dir,
                backup_dir=args.backup_dir, cpu_workers=args.cpu_workers, max_batch_size=args.max_batch_size,
                max_padded_tokens=args.max_padded_tokens, wave_documents=args.wave_documents,
                wave_sentences=args.wave_sentences, stop_after_batches=args.stop_after_batches)
        print(json.dumps(result, indent=2))
    except BrokenProcessPool:
        parser.error("CPU preparer exited; retry with --cpu-workers 0 or 1. Existing committed batches are preserved.")
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted. Rerun the same shard/plan; completed batches are preserved.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
