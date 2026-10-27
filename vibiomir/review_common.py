"""Small source/IO contracts shared by manual QA and experiment tools."""
from contextlib import contextmanager
import csv
import json
import math
from pathlib import Path

from .chunk_batches import read_json
from .io import canonical_json, file_hash, text_hash, write_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


def workflow(path):
    value = read_json(path)
    require(isinstance(value, dict), "Workflow must be a JSON object")
    required = {"input_dir", "stage3_dir", "stage4_dir", "tokenizer_dir", "chunk_config", "qa_dir", "stores",
                "fixed_validation_dir", "fixed_report_dir", "comparison_dir", "pilot_dir", "seed", "workers",
                "random_documents", "minimum_per_domain", "minimum_per_language_hint", "minimum_per_type",
                "risk_documents", "second_review_fraction", "proposed_main_content_target"}
    require(not required - set(value), f"Missing workflow fields: {sorted(required - set(value))}")
    require(isinstance(value["stores"], dict) and {"fixed", "structure", "semantic"} <= set(value["stores"]),
            "Configure fixed, structure and semantic store paths")
    for key in ("random_documents", "minimum_per_domain", "minimum_per_language_hint", "minimum_per_type", "risk_documents"):
        require(type(value[key]) is int and value[key] >= 0, f"{key} must be a nonnegative integer")
    require(value["random_documents"] > 0, "random_documents must be positive")
    for key in ("second_review_fraction", "proposed_main_content_target"):
        require(type(value[key]) in (int, float) and math.isfinite(value[key]) and 0 <= value[key] <= 1, f"Invalid {key}")
    require(type(value["seed"]) is int and type(value["workers"]) is int and 1 <= value["workers"] <= 8,
            "Require integer seed and 1..8 workers")
    return value


def separate(output, sources):
    output = Path(output).resolve()
    for source in sources:
        source = Path(source).resolve()
        require(output != source and output not in source.parents and source not in output.parents,
                f"Output overlaps source: {output} / {source}")


def completed(root, *, validated=False):
    root = Path(root)
    require((root / "manifest.json").is_file(), f"Missing {root}/manifest.json; build this store first")
    value = read_json(root / "manifest.json")
    require(value.get("state") == "complete", f"Incomplete store: {root}; resume its build first")
    require(text_hash(canonical_json(value["identity"])) == value["signature"], f"Invalid identity: {root}")
    snapshot = {"path": str(root.resolve()), "signature": value["signature"],
                "manifest_sha256": file_hash(root / "manifest.json")}
    if validated:
        require((root / "VALIDATION.json").is_file(), f"Validate {root} before using it")
        validation = read_json(root / "VALIDATION.json")
        require(validation.get("validation") == "passed" and all(validation[k] == value[k] for k in ("documents", "chunks")),
                f"Passed validation/totals required: {root}")
        snapshot["validation_sha256"] = file_hash(root / "VALIDATION.json")
    return value, snapshot


def jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"Invalid JSONL {path}:{number}") from exc


def write_jsonl(path, rows):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")
    temporary.replace(path)


def write_csv(path, fields, rows):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def priority(seed, *values):
    return text_hash(canonical_json([seed, *values]))


def frozen_sample(path):
    value = read_json(path)
    require(text_hash(canonical_json({k: v for k, v in value.items() if k != "signature"})) == value["signature"],
            "Frozen QA sample was edited; restore it rather than changing sample membership")
    return value


def wilson(successes, total, z=1.959963984540054):
    if not total:
        return None
    p, z2 = successes / total, z * z
    center = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total**2)) / (1 + z2 / total)
    return [max(0, center - half), min(1, center + half)]
