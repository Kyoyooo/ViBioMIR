"""Owner-authorized cleanup, gated on successful full local validation.

Deletes only verified redundant/incomplete extractions. Original ZIP downloads,
successful stores and review metadata are retained. Retired upload ZIPs move
out of artifacts into the archive. Run once from the project root.
"""
import csv
import json
from pathlib import Path
import shutil
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from vibiomir.chunk_batches import read_json
from vibiomir.io import file_hash, write_json

REPORT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    record = REPORT / "CLEANUP.json"
    require(not record.exists(), "Cleanup record already exists; do not rerun")
    for name, store in (("structure", "artifacts/chunks_stage5_full_structure"),
                        ("semantic", "artifacts/semantic_parallel_stage5_full/merged")):
        state = read_json(ROOT / "artifacts/validation_stage5" / name / "manifest.json")
        require(state["state"] == "complete", f"Full local validation required: {name}")
        validation = read_json(ROOT / store / "VALIDATION.json")
        require(validation["validation"] == "passed", f"Validation did not pass: {name}")
        require(state["identity"]["output_manifest_sha256"] == file_hash(ROOT / store / "manifest.json"),
                f"Store changed after full validation: {name}")
    recovery_dir = ROOT / "reports/maintenance/stage5_download_recovery_20261026"
    with (recovery_dir / "restored_files.csv").open() as stream:
        recovered = {row["relative_path"]: row for row in csv.DictReader(stream)}
    extracted = ROOT / "local_assets/archives/stage5_colab_download_20261026/extracted_snapshot"
    old_bundle = ROOT / "artifacts/stage5_full_colab"
    old_zip = ROOT / "artifacts/stage5_full_colab.zip"
    retired = ROOT / "local_assets/archives/stage5_handoff_20261026"
    require(extracted.is_dir() and old_bundle.is_dir() and old_zip.is_file() and not retired.exists(),
            "Unexpected cleanup layout")
    deletions = []
    print("Checking incomplete Drive extractions against recovered file hashes", flush=True)
    for path in sorted(extracted.rglob("*")):
        require(not path.is_symlink(), f"Unexpected symlink: {path}")
        if path.is_file():
            relative = Path(*path.relative_to(extracted).parts[2:]).as_posix()
            expected = recovered[relative]
            require(path.stat().st_size == int(expected["bytes"]) and file_hash(path) == expected["sha256"],
                    f"Extraction differs from recovered original: {path}")
            deletions.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                              "reason": "Incomplete extraction; complete ZIPs and verified recovered result retained"})
    print("Checking retired full bundle extraction against its original ZIP", flush=True)
    with zipfile.ZipFile(old_zip) as archive:
        require(archive.testzip() is None, "Retained bundle ZIP has a CRC error")
        members = {i.filename: i for i in archive.infolist() if not i.is_dir()}
        actual = {p.relative_to(old_bundle).as_posix(): p for p in old_bundle.rglob("*") if p.is_file()}
        require(set(members) == set(actual), "Old bundle extraction and ZIP file sets differ")
        for name, path in sorted(actual.items()):
            require(not path.is_symlink(), f"Unexpected symlink: {path}")
            crc = 0
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    crc = zlib.crc32(block, crc)
            require(path.stat().st_size == members[name].file_size and crc == members[name].CRC,
                    f"Old extraction differs from retained ZIP: {path}")
            deletions.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                              "reason": "Retired extraction preserved in original ZIP; full outputs validated"})
    canonical = ROOT / "artifacts/chunks_stage5_full_structure"
    checkpoint = old_bundle / "checkpoint"
    require(read_json(checkpoint / "manifest.json")["signature"] == read_json(canonical / "manifest.json")["signature"],
            "Retired checkpoint has another identity")
    parts = sorted(checkpoint.glob("batch-*"))
    for path in parts:
        info = read_json(path / "batch.json")
        current = read_json(canonical / path.name / "batch.json")
        require(info == current and file_hash(path / "documents.jsonl.gz") == current["sha256"],
                f"Retired batch not preserved in validated full output: {path}")
    archive_moves = []
    for source in (old_zip, ROOT / "artifacts/stage5_semantic_parallel.zip"):
        require(source.is_file(), f"Missing bundle: {source}")
        archive_moves.append({"from": str(source.relative_to(ROOT)),
                              "to": str((retired / source.name).relative_to(ROOT)),
                              "bytes": source.stat().st_size, "sha256": file_hash(source)})
    payload = {"date": "2026-10-26", "state": "planned_verified", "deleted_files": len(deletions),
               "deleted_bytes": sum(item["bytes"] for item in deletions),
               "deleted_directories": [str(p.relative_to(ROOT)) for p in (extracted, old_bundle)],
               "retired_checkpoint_batches_verified": len(parts), "archive_moves": archive_moves,
               "preserved": ["All four original Drive ZIPs and five loose originals",
                             "Full structural/semantic stores and both semantic shards",
                             "Successful stage-3/4 stores and stage-5 smoke fixtures",
                             "Upload_to_Drive (owner-managed historical snapshot; not used for current handoff)"]}
    with (REPORT / "cleanup_files.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "bytes", "reason"])
        writer.writeheader()
        writer.writerows(deletions)
    write_json(REPORT / "RETIRED_CHECKPOINT_MANIFEST.json", read_json(checkpoint / "manifest.json"))
    write_json(record, payload)
    retired.mkdir(parents=True)
    for item in archive_moves:
        (ROOT / item["from"]).rename(ROOT / item["to"])
    for directory in (extracted, old_bundle):
        shutil.rmtree(directory)
    temp = ROOT / "artifacts/temp"
    if temp.is_dir() and not any(temp.iterdir()):
        temp.rmdir()
    payload["state"] = "complete"
    write_json(record, payload)
    print(json.dumps(payload, indent=2), flush=True)


if __name__ == "__main__":
    main()
