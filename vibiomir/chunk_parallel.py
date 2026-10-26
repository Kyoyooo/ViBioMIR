"""Manual full-corpus runner: exact tokenizer caching, ordered CPU workers, CUDA semantic.

The original five chunk identity modules stay unchanged, so old fixed/structure
checkpoints remain compatible. This module changes scheduling only. Each CPU
worker loads a local tokenizer once; only the parent writes committed batches.
"""
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import time

from .chunk_batches import batches, commit_batch, finish, output_store, read_json, read_rows
from .chunk_colab import restore_checkpoint, save_checkpoint
from .chunk_models import load_tokenizer
from .chunk_pipeline import build_chunks, code_signature
from .chunking import chunk_document, settings
from .io import file_hash, write_json


class CachedTokenizer:
    """Cache exact encode/offset requests, bounded by entries and source characters.

    Only the two tokenizer operations used by the chunker are wrapped. Token IDs,
    special-token policy and original offset mappings are never approximated.
    Clear at every document boundary to avoid retaining long corpus strings.
    """
    def __init__(self, tokenizer, max_entries=2048, max_characters=2_000_000):
        self.tokenizer = tokenizer
        self.max_entries, self.max_characters = max_entries, max_characters
        self.cache, self.characters = OrderedDict(), 0

    def clear(self):
        self.cache.clear()
        self.characters = 0

    def _get(self, key, calculate):
        if key in self.cache:
            value = self.cache.pop(key)
            self.cache[key] = value
            return value
        value = calculate()
        size = len(key[1])
        if size <= self.max_characters and self.max_entries > 0:
            while self.cache and (len(self.cache) >= self.max_entries or self.characters + size > self.max_characters):
                old, _ = self.cache.popitem(last=False)
                self.characters -= len(old[1])
            self.cache[key] = value
            self.characters += size
        return value

    def encode(self, text, add_special_tokens=True, truncation=False):
        return self._get(("encode", text, add_special_tokens, truncation),
            lambda: self.tokenizer.encode(text, add_special_tokens=add_special_tokens, truncation=truncation))

    def __call__(self, text, **kwargs):
        key = ("offsets", text, tuple(sorted(kwargs.items())))
        return self._get(key, lambda: self.tokenizer(text, **kwargs))


_WORKER_TOKENIZER = None
_WORKER_SETTINGS = None


def _initialize_worker(tokenizer_dir, config, strategy, signature):
    global _WORKER_TOKENIZER, _WORKER_SETTINGS
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["USE_TF"] = "0"
    os.environ["USE_FLAX"] = "0"
    tokenizer, _ = load_tokenizer(tokenizer_dir)
    _WORKER_TOKENIZER = CachedTokenizer(tokenizer)
    _WORKER_SETTINGS = config, strategy, signature


def _process_packet(packet):
    config, strategy, signature = _WORKER_SETTINGS
    _WORKER_TOKENIZER.clear()
    try:
        return chunk_document(packet, _WORKER_TOKENIZER, config, strategy, signature)
    except ValueError as exc:
        raise ValueError(f"doc_id={packet['doc_id']}: {exc}") from exc


def build_parallel(input_dir, output_dir, tokenizer_dir, *, strategy="structure", config=None,
                   workers=2, resume=False, stop_after_batches=None, backup_dir=None):
    """Identical logical output to build_chunks; CPU execution parameters are not IDs."""
    if strategy not in {"fixed", "structure"}:
        raise ValueError("CPU parallel runner supports fixed/structure; use the semantic runner for CUDA")
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("workers must be an integer between 1 and 32")
    if stop_after_batches is not None and stop_after_batches < 1:
        raise ValueError("stop_after_batches must be positive")
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    parent = read_json(input_dir / "manifest.json")
    if parent.get("state") != "complete" or parent["identity"]["operation"] != "prepare-chunk-inputs":
        raise ValueError("Reuse the completed prepare-chunk-inputs output; do not prepare it again")
    config = settings(config)
    tokenizer, tokenizer_signature = load_tokenizer(tokenizer_dir)
    identity = {"operation": "build-chunks", "parent_signature": parent["signature"],
                "code": code_signature(), "strategy": strategy, "config": config,
                "tokenizer": tokenizer_signature, "encoder": None}
    with output_store(output_dir, identity, resume) as (directory, manifest, committed):
        if manifest["state"] == "complete":
            if backup_dir:
                save_checkpoint(directory, backup_dir)
            return manifest
        print(json.dumps({"phase": "verifying_input_batches", "workers": workers,
                          "gpu_used": False, "reused_output_batches": len(committed)}), flush=True)
        inputs = list(batches(input_dir, parent["signature"]))
        if len(committed) > len(inputs):
            raise ValueError("Output has more batches than the prepared input")
        for (_, old), (_, source) in zip(committed, inputs):
            if any(old[key] != source[key] for key in ("index", "documents", "first_doc_id", "last_doc_id")):
                raise ValueError("Committed batch does not match the prepared input")
        engine = {"runner": "ordered CPU processes + exact tokenizer cache", "workers": workers,
                  "code_sha256": file_hash(Path(__file__)), "gpu_used": False}
        manifest.setdefault("execution_engines", []).append(engine)
        write_json(directory / "manifest.json", manifest)
        if backup_dir:
            save_checkpoint(directory, backup_dir)
        pool = None
        cached = CachedTokenizer(tokenizer)
        invocation_start = time.monotonic()
        new_documents = added = 0
        try:
            if workers > 1:
                pool = ProcessPoolExecutor(max_workers=workers,
                    mp_context=multiprocessing.get_context("spawn"), initializer=_initialize_worker,
                    initargs=(str(Path(tokenizer_dir).resolve()), config, strategy, manifest["signature"]))
            for index in range(len(committed), len(inputs)):
                path, info = inputs[index]
                start = time.monotonic()
                print(json.dumps({"phase": "chunking", "batch": index, "of_batches": len(inputs),
                                  "documents": info["documents"], "workers": workers}), flush=True)
                packets = list(read_rows(path))
                if pool:
                    rows = list(pool.map(_process_packet, packets, chunksize=4))
                else:
                    rows = []
                    for packet in packets:
                        cached.clear()
                        rows.append(chunk_document(packet, cached, config, strategy, manifest["signature"]))
                committed.append(commit_batch(directory, manifest["signature"], index, rows))
                built = time.monotonic()
                if backup_dir:
                    save_checkpoint(directory, backup_dir, committed[-1][0])
                new_documents += len(rows)
                added += 1
                elapsed = time.monotonic() - invocation_start
                rate = new_documents / max(elapsed, 0.001)
                remaining = sum(part["documents"] for _, part in inputs[index + 1:])
                print(json.dumps({"phase": "committed", "batch": index,
                    "build_seconds": round(built - start, 2),
                    "backup_seconds": round(time.monotonic() - built, 2),
                    "documents_per_second_this_invocation": round(rate, 2),
                    "estimated_remaining_seconds": round(remaining / max(rate, 0.001)),
                    "gpu_used": False}), flush=True)
                if stop_after_batches and added >= stop_after_batches:
                    result = finish(directory, manifest, committed, False)
                    if backup_dir:
                        save_checkpoint(directory, backup_dir)
                    return result
            manifest["elapsed_seconds_last_invocation"] = round(time.monotonic() - invocation_start, 3)
            result = finish(directory, manifest, committed, True)
            if backup_dir:
                save_checkpoint(directory, backup_dir)
            return result
        finally:
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)


def restore_best_checkpoint(output_dir, backup_dir=None, seed_dir=None):
    """Prefer the longest compatible committed prefix; never combine strategies."""
    output_dir = Path(output_dir)
    if (output_dir / "manifest.json").is_file():
        return
    candidates = []
    for directory in (backup_dir, seed_dir):
        if directory and (Path(directory) / "manifest.json").is_file():
            path = Path(directory)
            candidates.append((len(list(path.glob("batch-*"))), path,
                               read_json(path / "manifest.json")["signature"]))
    if len({signature for _, _, signature in candidates}) > 1:
        raise ValueError("Seed checkpoint and Drive backup have different identities; use a separate output/backup")
    if candidates:
        _, selected, _ = max(candidates, key=lambda value: value[0])
        print(json.dumps({"phase": "restoring_checkpoint", "source": str(selected)}), flush=True)
        restore_checkpoint(selected, output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--strategy", choices=["fixed", "structure", "semantic"], default="structure")
    parser.add_argument("--config", type=Path, default=Path("configs/chunking_stage5.json"))
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--encoder-batch-size", type=int, default=8)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--seed-checkpoint-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-after-batches", type=int)
    args = parser.parse_args(argv)
    try:
        config = settings(read_json(args.config))
        if not (args.input_dir / "manifest.json").is_file():
            raise ValueError("Missing prepared input manifest")
        if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
            raise ValueError("Output exists: repeat with --resume or choose a new directory")
        for candidate in (args.output_dir, args.backup_dir, args.seed_checkpoint_dir):
            if candidate and (candidate / "manifest.json").is_file():
                existing = read_json(candidate / "manifest.json")
                if existing["identity"].get("strategy") != args.strategy:
                    raise ValueError("Checkpoint strategy differs. Structural batches cannot be reused as semantic/fixed batches.")
        if args.resume:
            restore_best_checkpoint(args.output_dir, args.backup_dir, args.seed_checkpoint_dir)
        resuming = (args.output_dir / "manifest.json").is_file()
        if args.strategy == "semantic":
            from .chunk_models import SentenceEncoder
            tokenizer, signature = load_tokenizer(args.tokenizer_dir)
            encoder = SentenceEncoder(tokenizer, args.encoder_batch_size)
            if args.backup_dir and resuming:
                save_checkpoint(args.output_dir, args.backup_dir)
            result = build_chunks(args.input_dir, args.output_dir, tokenizer, signature,
                strategy="semantic", config=config, resume=resuming, encoder=encoder,
                encoder_registry=encoder.registry, stop_after_batches=args.stop_after_batches,
                on_batch=(lambda batch: save_checkpoint(args.output_dir, args.backup_dir, batch)) if args.backup_dir else None)
            if args.backup_dir:
                save_checkpoint(args.output_dir, args.backup_dir)
        else:
            result = build_parallel(args.input_dir, args.output_dir, args.tokenizer_dir,
                strategy=args.strategy, config=config, workers=args.workers, resume=resuming,
                backup_dir=args.backup_dir, stop_after_batches=args.stop_after_batches)
        print(json.dumps(result, indent=2))
    except BrokenProcessPool:
        parser.error("A CPU worker stopped unexpectedly, often because of RAM pressure. Retry with --workers 1 or 2 and --resume; committed batches remain intact.")
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; reuse the same settings with --resume. Last committed batches are preserved.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
