"""Small immutable gzip batches: one atomic directory rename per checkpoint.

Each line represents one document, including documents with no usable content.
Committed directories are authoritative; interrupted .pending-* directories are
ignored. There are no per-document SQLite copies, fsyncs or mutable data files.
"""
from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import tempfile

from .io import canonical_json, file_hash, text_hash, write_json, writer_lock


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(batch_dir):
    with gzip.open(Path(batch_dir) / "documents.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def batches(directory, signature=None):
    """Verify committed file checksums, sequence and signature before reuse."""
    directory = Path(directory)
    previous = None
    for index, path in enumerate(sorted(directory.glob("batch-*"))):
        if path.name != f"batch-{index:06d}" or not path.is_dir():
            raise ValueError(f"Invalid batch sequence: {path}")
        info = read_json(path / "batch.json")
        if info["index"] != index or (signature and info["signature"] != signature):
            raise ValueError(f"Batch signature mismatch: {path}")
        if previous is not None and info["first_doc_id"] <= previous:
            raise ValueError(f"Document order mismatch: {path}")
        if file_hash(path / "documents.jsonl.gz") != info["sha256"]:
            raise ValueError(f"Batch checksum mismatch: {path}")
        previous = info["last_doc_id"]
        yield path, info


@contextmanager
def output_store(directory, identity, resume=False):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()) and not resume:
        raise ValueError(f"Output exists: {directory}. Use --resume with the same settings, or choose a new directory.")
    if resume and not (directory / "manifest.json").is_file():
        raise ValueError(f"Cannot resume: no manifest.json in {directory}. Start without --resume in a new directory.")
    directory.mkdir(parents=True, exist_ok=True)
    signature = text_hash(canonical_json(identity))
    with writer_lock(directory):
        path = directory / "manifest.json"
        if path.exists():
            manifest = read_json(path)
            if manifest["signature"] != signature:
                raise ValueError("Input, code, tokenizer or configuration changed. Choose a new output directory.")
        else:
            manifest = {"format": "vibiomir-document-batches-v1", "identity": identity,
                        "signature": signature, "state": "building", "human_qa": "pending",
                        "selection_is_final": False, "ready_for_retrieval": False}
            write_json(path, manifest)
        committed = list(batches(directory, signature))
        yield directory, manifest, committed


def commit_batch(directory, signature, index, rows):
    if not rows:
        raise ValueError("Cannot commit an empty batch")
    ids = [row["doc_id"] for row in rows]
    if any(a >= b for a, b in zip(ids, ids[1:])):
        raise ValueError("Batch document IDs must be unique and increasing")
    destination = directory / f"batch-{index:06d}"
    if destination.exists():
        raise ValueError(f"Refusing to overwrite committed batch: {destination}")
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=directory) as temporary:
        temporary = Path(temporary)
        with gzip.open(temporary / "documents.jsonl.gz", "wt", encoding="utf-8", compresslevel=3) as stream:
            for row in rows:
                stream.write(canonical_json(row) + "\n")
        info = {"index": index, "signature": signature, "documents": len(rows),
                "first_doc_id": ids[0], "last_doc_id": ids[-1],
                "chunks": sum(len(row.get("chunks", [])) for row in rows),
                "sha256": file_hash(temporary / "documents.jsonl.gz")}
        write_json(temporary / "batch.json", info)
        temporary.rename(destination)
    print(json.dumps({"batch": index, "last_doc_id": ids[-1], "documents": len(rows),
                      "chunks": info["chunks"]}), flush=True)
    return destination, info


def finish(directory, manifest, committed, complete):
    manifest.update(state="complete" if complete else "paused", batches=len(committed),
                    documents=sum(info["documents"] for _, info in committed),
                    chunks=sum(info["chunks"] for _, info in committed))
    write_json(directory / "manifest.json", manifest)
    return manifest
