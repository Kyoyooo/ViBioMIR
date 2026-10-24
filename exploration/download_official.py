"""Explicit network step to fetch the public, revision-pinned official dataset."""
import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

REPO = "AIGuruTinix/ViBioMIR"
REVISION = "71be0e1239a19dfd7a717e2bc13e3bec21bd0334"
FILES = ("README.md", "query.parquet", "links_corpus_vi_en_zh.parquet")


def fetch(output_dir, revision=REVISION):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Use a full 40-character commit SHA for reproducibility")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in (*FILES, "provenance.json")):
        raise FileExistsError("Official files already exist; choose a new --output-dir")
    metadata_url = f"https://huggingface.co/api/datasets/{REPO}/revision/{revision}"
    with urllib.request.urlopen(metadata_url, timeout=90) as response:
        metadata = json.load(response)
    if metadata["sha"] != revision:
        raise ValueError("Dataset revision does not match the requested commit")
    records = []
    for name in FILES:
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/{name}"
        target, temporary = out / name, out / (name + ".part")
        print(f"Downloading {name}…", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open("xb") as stream:
                shutil.copyfileobj(response, stream)
            digest = hashlib.sha256()
            with temporary.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            record = {"filename": name, "url": url, "bytes": temporary.stat().st_size,
                      "sha256": digest.hexdigest()}
            if name.endswith(".parquet"):
                with pq.ParquetFile(temporary) as pf:
                    required = {"id", "query" if name == "query.parquet" else "url"}
                    schema = pf.schema_arrow
                    if not required.issubset(schema.names) or not pa.types.is_integer(schema.field("id").type):
                        raise ValueError(f"Unexpected official schema: {schema}")
                    record.update(rows=pf.metadata.num_rows, schema=str(schema.remove_metadata()))
            temporary.replace(target)
            records.append(record)
        finally:
            if temporary.exists():
                temporary.unlink()
    manifest = {"repository": REPO, "revision": revision,
                "retrieved_at_vietnam": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(timespec="seconds"),
                "metadata_url": metadata_url, "dataset_last_modified": metadata.get("lastModified"),
                "configs": metadata.get("cardData", {}).get("configs"), "files": records}
    (out / "provenance.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved official data and provenance: {out.resolve()}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("Data/official/ViBioMIR"))
    parser.add_argument("--revision", default=REVISION)
    args = parser.parse_args()
    fetch(args.output_dir, args.revision)


if __name__ == "__main__":
    main()
