"""One-time, owner-authorized recovery of the four 2026-10-26 Drive ZIPs.

Run from the project root with Python 3. No GPU, model or tokenizer is loaded.
Original ZIPs, incomplete extractions and loose files are retained as backups.
Refuses a second execution after publication; this is an execution record, not
a new stage-5 pipeline or a general ZIP importer.
"""
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from vibiomir.chunk_batches import batches, read_json
from vibiomir.io import canonical_json, file_hash, text_hash, write_json

TEMP = ROOT / "artifacts/temp"
DEST = ROOT / "artifacts/semantic_parallel_stage5_full"
STAGING = ROOT / "artifacts/.stage5_download_recovery_20261026"
BACKUP = ROOT / "local_assets/archives/stage5_colab_download_20261026"
REPORT = Path(__file__).resolve().parent
PREFIX = "semantic_parallel_run01"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_store(directory, manifest, expected_batches):
    require(manifest["state"] == "complete", f"Incomplete store: {directory}")
    require(text_hash(canonical_json(manifest["identity"])) == manifest["signature"],
            f"Invalid identity: {directory}")
    parts = list(batches(directory, manifest["signature"]))
    require(len(parts) == expected_batches == manifest["batches"], f"Batch count: {directory}")
    for key in ("documents", "chunks"):
        require(sum(info[key] for _, info in parts) == manifest[key], f"{key} total: {directory}")
    return parts


def main():
    require(DEST.is_dir() and not any(DEST.iterdir()), "Destination must exist and be empty")
    require(not STAGING.exists() and not BACKUP.exists(), "Staging or backup already exists")
    archives = sorted(TEMP.glob(f"{PREFIX}-20261026T084344Z-1-*.zip"))
    require(len(archives) == 4, "Exactly four Drive ZIP parts are required")
    expected_names = {p.name for p in archives} | {p.stem for p in archives} | {
        "plan.json", "manifest.json", "VALIDATION.json", "batch.json", "documents.jsonl.gz"}
    require({p.name for p in TEMP.iterdir()} == expected_names, "Unexpected temp contents; inspect first")
    require(not any(p.is_symlink() for p in TEMP.rglob("*")), "Unexpected source symlink")
    STAGING.mkdir()
    files, archive_reports, missing_extracted = {}, [], []
    for archive in archives:
        print(f"Recovering {archive.name}", flush=True)
        extracted = TEMP / archive.stem
        archive_reports.append({"path": str(archive.relative_to(ROOT)),
                                "bytes": archive.stat().st_size, "sha256": file_hash(archive)})
        with zipfile.ZipFile(archive) as handle:
            for entry in handle.infolist():
                path = PurePosixPath(entry.filename)
                require(not path.is_absolute() and ".." not in path.parts and path.parts[0] == PREFIX,
                        f"Unsafe ZIP path: {entry.filename}")
                require(not stat.S_ISLNK(entry.external_attr >> 16), "Unexpected ZIP symlink")
                if entry.is_dir():
                    continue
                relative = Path(*path.parts[1:])
                require(len(relative.parts) in (1, 2, 3), f"Unexpected layout: {relative}")
                require(str(relative) not in files, f"Duplicate archive member: {relative}")
                destination = STAGING / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with handle.open(entry) as source, destination.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                        digest.update(block)
                require(destination.stat().st_size == entry.file_size, f"Size mismatch: {relative}")
                item = {"relative_path": relative.as_posix(), "archive": archive.name,
                        "archive_member": entry.filename, "bytes": entry.file_size,
                        "sha256": digest.hexdigest()}
                files[str(relative)] = item
                if not (extracted / entry.filename).is_file():
                    missing_extracted.append(item)
    print("Checking prepared inputs and all restored batch checksums", flush=True)
    plan = read_json(STAGING / "plan.json")
    require(text_hash(canonical_json({k: v for k, v in plan.items() if k != "signature"})) == plan["signature"],
            "Invalid plan signature")
    parent_dir = ROOT / "artifacts/chunk_inputs_stage5_full"
    parent = read_json(parent_dir / "manifest.json")
    require(parent["state"] == "complete" and parent["signature"] == plan["parent_signature"], "Wrong input")
    require(text_hash(canonical_json(parent["identity"])) == parent["signature"], "Invalid input identity")
    inputs = list(batches(parent_dir, parent["signature"]))
    require(len(inputs) == plan["input_batches"] == parent["batches"], "Input batch count")
    require(sum(info["documents"] for _, info in inputs) == plan["documents"] == parent["documents"],
            "Input document count")
    for group in ("code", "execution_code"):
        for name, digest in plan[group].items():
            require(file_hash(ROOT / "vibiomir" / name) == digest, f"Code mismatch: {name}")
    config = read_json(ROOT / "configs/chunking_stage5.json")
    registry = read_json(ROOT / "artifacts/tokenizers/bge_m3_stage5/REGISTRY.json")
    require(config == plan["config"], "Configuration differs")
    require(text_hash(canonical_json(registry)) == plan["tokenizer"], "Tokenizer registry differs")
    for name, digest in registry["files"].items():
        require(Path(name).name == name, "Unsafe tokenizer registry path")
        require(file_hash(ROOT / "artifacts/tokenizers/bge_m3_stage5" / name) == digest,
                f"Tokenizer file differs: {name}")
    merged = read_json(STAGING / "merged/manifest.json")
    require(merged["identity"]["strategy"] == "semantic", "Wrong strategy")
    require(merged["identity"]["parent_signature"] == parent["signature"], "Wrong merged parent")
    require(merged["identity"]["config"] == config and merged["identity"]["tokenizer"] == plan["tokenizer"],
            "Wrong merged config/tokenizer")
    require(merged["identity"]["code"] == plan["code"], "Wrong merged code")
    require(merged["merge_plan"] == plan["signature"], "Wrong merge plan")
    require(plan["seed_batches"] == 0 and plan["ranges"][0][0] == 0 and
            plan["ranges"][0][1] == plan["ranges"][1][0] and
            plan["ranges"][1][1] == len(inputs), "Unexpected or incomplete shard coverage")
    merged_parts = check_store(STAGING / "merged", merged, len(inputs))
    store_reports = {"merged": {k: merged[k] for k in ("state", "batches", "documents", "chunks", "signature")}}
    for shard_id, (start, end) in enumerate(plan["ranges"]):
        name = f"shard_{shard_id}"
        manifest = read_json(STAGING / name / "manifest.json")
        require(manifest["identity"] == merged["identity"], f"Shard identity: {name}")
        require(manifest["partition"] == {"plan_signature": plan["signature"], "shard_id": shard_id,
                "global_start": start, "global_end": end}, f"Shard partition: {name}")
        parts = check_store(STAGING / name, manifest, end - start)
        for local_index, (_, descriptor) in enumerate(parts):
            index = start + local_index
            output_info, input_info = merged_parts[index][1], inputs[index][1]
            require(descriptor["global_input_batch"] == index, f"Global batch mapping: {name}/{local_index}")
            require(output_info["global_input_batch"] == index, f"Merged global mapping: {index}")
            for key in ("documents", "first_doc_id", "last_doc_id"):
                require(descriptor[key] == output_info[key] == input_info[key], f"Geometry: {index}/{key}")
            for key in ("sha256", "chunks"):
                require(descriptor[key] == output_info[key], f"Merged payload differs: {index}/{key}")
        store_reports[name] = {k: manifest[k] for k in ("state", "batches", "documents", "chunks", "signature")}
    validation = read_json(STAGING / "merged/VALIDATION.json")
    require(validation["validation"] == "passed", "Colab full validation did not pass")
    for key in ("documents", "chunks"):
        require(validation[key] == merged[key], f"Validation total: {key}")
    require(validation["stage4_source"] == parent["identity"]["source"], "Validation source differs")
    require(validation["exact_prepared_source_slices_verified"] is True and
            validation["all_retrieval_token_budgets_verified"] is True, "Incomplete Colab validation")
    require(merged["human_qa"] == validation["human_qa"] == "pending" and
            merged["selection_is_final"] is validation["selection_is_final"] is False and
            merged["ready_for_retrieval"] is validation["ready_for_retrieval"] is False, "Unexpected QA flags")
    loose_matches = []
    for loose in sorted(p for p in TEMP.iterdir() if p.is_file() and p.suffix != ".zip"):
        digest = file_hash(loose)
        matches = [item["relative_path"] for item in files.values()
                   if item["sha256"] == digest and item["bytes"] == loose.stat().st_size]
        require(matches, f"Unidentified loose file: {loose}")
        loose_matches.append({"original": str(loose.relative_to(ROOT)), "sha256": digest,
                              "restored_matches": [str(DEST.relative_to(ROOT) / p) for p in matches]})
    with (REPORT / "restored_files.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(next(iter(files.values())).keys()))
        writer.writeheader()
        writer.writerows(files.values())
    result = {"date": "2026-10-26", "scope": "Owner-authorized local recovery and integrity checks; no GPU or full rechunking",
              "destination": str(DEST.relative_to(ROOT)), "archives": archive_reports,
              "restored_files": len(files), "restored_bytes": sum(x["bytes"] for x in files.values()),
              "missing_from_original_extractions": missing_extracted, "loose_files": loose_matches,
              "stores": store_reports, "plan_signature": plan["signature"],
              "input_signature": parent["signature"], "integrity": "passed",
              "checks": ["ZIP CRC on every recovered member", "Every restored gzip batch SHA-256 re-read from disk",
                         "Input gzip batch checksums", "Identity, plan and local code/config/tokenizer hashes",
                         "Batch geometry and totals", "Merged gzip hashes equal the corresponding shard hashes",
                         "All loose files matched to exact archived content"],
              "colab_validation": "Imported passed full validation; not rerun over all chunks locally",
              "human_qa": "pending", "selection_is_final": False, "ready_for_retrieval": False}
    write_json(REPORT / "RECOVERY.json", result)
    # Publish only after all checks have passed. No original download is deleted.
    DEST.rmdir()  # pre-existing, verified empty placeholder
    STAGING.rename(DEST)
    BACKUP.mkdir(parents=True)
    relocations = []
    for source in sorted(TEMP.iterdir()):
        category = "extracted_snapshot" if source.is_dir() else "zip_parts" if source.suffix == ".zip" else "loose_files"
        destination = BACKUP / category / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        relocations.append({"from": str(source.relative_to(ROOT)), "to": str(destination.relative_to(ROOT))})
        source.rename(destination)
    result["backup"] = str(BACKUP.relative_to(ROOT))
    result["relocations"] = relocations
    write_json(REPORT / "RECOVERY.json", result)
    for name, path in (("stage5_full_semantic", DEST / "merged"),
                       ("stage5_semantic_shard_0", DEST / "shard_0"),
                       ("stage5_semantic_shard_1", DEST / "shard_1")):
        target = ROOT / "reports/run_registry" / name
        target.mkdir()
        for filename in ("manifest.json", "VALIDATION.json"):
            if (path / filename).exists():
                shutil.copy2(path / filename, target / filename)
    shutil.copy2(DEST / "plan.json", REPORT / "plan.json")
    structure = ROOT / "artifacts/chunks_stage5_full_structure"
    observed = ROOT / "reports/run_registry/stage5_full_structure_20261026"
    observed.mkdir()
    shutil.copy2(structure / "manifest.json", observed / "manifest.json")
    print(json.dumps({"integrity": "passed", "destination": result["destination"],
                      "restored_files": len(files), "stores": store_reports,
                      "missing_extracted_files_restored": len(missing_extracted),
                      "loose_files_identified": len(loose_matches)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
