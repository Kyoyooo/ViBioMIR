"""Manually package code + verified prepared batches + tokenizer; no GPU job.

Run from the project root. Never includes Data/, databases or existing models.
"""
import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vibiomir.chunk_batches import batches, read_json
from vibiomir.chunk_models import load_tokenizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = read_json(args.input_dir / "manifest.json")
    if manifest.get("state") != "complete" or manifest["identity"]["operation"] != "prepare-chunk-inputs":
        parser.error("Prepare a complete input snapshot first")
    load_tokenizer(args.tokenizer_dir)
    committed = list(batches(args.input_dir, manifest["signature"]))
    if args.output.exists():
        parser.error("Bundle already exists; choose a new --output path")
    entries = []
    for folder in ("vibiomir", "exploration"):
        entries.extend((p, p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / folder).glob("*.py")))
    for name in ("requirements-stage3.txt", "requirements-stage4.txt", "requirements-stage5.txt", "requirements-stage5-colab.txt", "configs/chunking_stage5.json"):
        entries.append((ROOT / name, name))
    entries.append((args.input_dir / "manifest.json", "inputs/manifest.json"))
    for path, _ in committed:
        for name in ("batch.json", "documents.jsonl.gz"):
            entries.append((path / name, f"inputs/{path.name}/{name}"))
    for path in sorted(args.tokenizer_dir.iterdir()):
        if path.is_file():
            entries.append((path, f"tokenizer/{path.name}"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".zip.partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path, name in entries:
            archive.write(path, name)
    temporary.replace(args.output)
    print(f"Created {args.output}: {manifest['documents']} documents; {len(entries)} files; no model weights included")


if __name__ == "__main__":
    main()
