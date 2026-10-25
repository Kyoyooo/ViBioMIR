"""Colab persistence: keep locks/atomic computation local, copy committed batches.

Google Drive is a backup destination, not a SQLite/locking filesystem. Exactly
one notebook may write a given backup directory. No GPU dependency here.
"""
from pathlib import Path
import shutil
import tempfile

from .chunk_batches import batches, read_json
from .io import file_hash, write_json


def save_checkpoint(local_dir, backup_dir, batch_path=None):
    local_dir, backup_dir = Path(local_dir), Path(backup_dir)
    manifest = read_json(local_dir / "manifest.json")
    backup_dir.mkdir(parents=True, exist_ok=True)
    if (backup_dir / "manifest.json").exists():
        old = read_json(backup_dir / "manifest.json")
        if old["signature"] != manifest["signature"]:
            raise ValueError("Backup belongs to another input/configuration; choose a new backup directory")
    # Passing a batch checkpoints only that batch: no repeated full-corpus scan.
    paths = [Path(batch_path)] if batch_path else [p for p in sorted(local_dir.glob("batch-*"))]
    for path in paths:
        destination = backup_dir / path.name
        digest = read_json(path / "batch.json")["sha256"]
        if destination.exists():
            if file_hash(destination / "documents.jsonl.gz") != digest:
                raise ValueError(f"Backup checksum mismatch: {destination}")
            continue
        with tempfile.TemporaryDirectory(prefix=".pending-copy-", dir=backup_dir) as temporary:
            copied = Path(temporary) / "batch"
            shutil.copytree(path, copied)
            if file_hash(copied / "documents.jsonl.gz") != digest:
                raise ValueError("Checkpoint copy checksum mismatch")
            copied.rename(destination)
    write_json(backup_dir / "manifest.json", manifest)
    if (local_dir / "VALIDATION.json").exists():
        shutil.copy2(local_dir / "VALIDATION.json", backup_dir / "VALIDATION.json")


def restore_checkpoint(backup_dir, local_dir):
    backup_dir, local_dir = Path(backup_dir), Path(local_dir)
    if (local_dir / "manifest.json").exists():
        return  # interrupted same-runtime work is already authoritative
    if not (backup_dir / "manifest.json").exists():
        return  # first run
    manifest = read_json(backup_dir / "manifest.json")
    committed = list(batches(backup_dir, manifest["signature"]))
    local_dir.mkdir(parents=True, exist_ok=True)
    for path, _ in committed:
        if (local_dir / path.name).exists():
            shutil.rmtree(local_dir / path.name)  # only an unfinished local restore
        shutil.copytree(path, local_dir / path.name)
    # Write the manifest last; failed restores cannot masquerade as resumable.
    write_json(local_dir / "manifest.json", manifest)
