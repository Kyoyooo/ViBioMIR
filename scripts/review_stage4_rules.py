"""Collect deterministic source-context evidence for reviewing stage-4 rules."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import random
import re
import sqlite3

from vibiomir.cleaning_rules import clinical_signals, load_cleaning_config
from vibiomir.io import file_hash, write_json
from vibiomir.validate_cleaning import iter_export


def collect(dry_store, parent_store, config_path, output, sample_size=12, seed=20261025):
    dry_store, parent_store, output = map(lambda p: Path(p).resolve(), (dry_store, parent_store, output))
    if output.exists():
        raise FileExistsError("Choose a new review evidence directory")
    manifest = json.loads((dry_store / "run_manifest.json").read_text())
    validation = json.loads((dry_store / "VALIDATION.json").read_text())
    if manifest["state"] != "complete_dry_run" or validation["validation"] != "passed":
        raise ValueError("Use a completed, validated dry-run")
    config = load_cleaning_config(config_path)
    rules = {r["id"]: r for r in config["rules"]}
    counts, samples, unique = defaultdict(Counter), defaultdict(list), defaultdict(Counter)
    rng = random.Random(seed)
    checks = Counter()
    for row in iter_export(dry_store, manifest, "transformations"):
        rid, decision = row["rule_id"], row["decision"]
        rule = rules[rid]
        counts[rid][decision] += 1
        if decision == "proposed_removal":
            checks["proposals_checked"] += 1
            match_text = row["text"].splitlines()[0].strip() if rule["action"] == "trim_tail" else row["text"].strip()
            if not re.fullmatch(rule["pattern"], match_text, re.I):
                raise ValueError("Proposed text no longer matches rule " + rid)
            if any(clinical_signals(row["text"]).values()):
                raise ValueError("Proposed text contains protected signals: " + rid)
            checks["rule_and_protected_signal_checks_passed"] += 1
            unique[rid][row["text"]] += 1
        key = (rid, decision)
        group = samples[key]
        if len(group) < sample_size:
            group.append(row)
        else:
            index = rng.randrange(counts[rid][decision])
            if index < sample_size:
                group[index] = row
    cases = []
    database = parent_store / "document_store.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        for (rid, decision), selected in sorted(samples.items()):
            for row in selected:
                saved = db.execute("SELECT payload FROM versions WHERE version_id=?", (row["parent_version_id"],)).fetchone()
                if saved is None or row["source_kind"] != "stage3_text":
                    raise ValueError("This review collector expects retained stage-3 text; review other origins separately")
                parent = json.loads(saved[0])
                text = parent["evidence_text"]
                start, end = row["source_start"], row["source_end"]
                if text[start:end] != row["text"]:
                    raise ValueError("Sample source slice mismatch")
                cases.append({"rule_id": rid, "decision": decision, "doc_id": row["doc_id"],
                    "parent_version_id": row["parent_version_id"], "source_url": parent["source_url"],
                    "source_start": start, "source_end": end, "source_chars": len(text),
                    "before": text[max(0, start - 350):start], "removed_or_vetoed": row["text"],
                    "after": text[end:end + 350], "reasons": json.loads(row["reason_json"])})
    output.mkdir(parents=True)
    summary = {"source_dry_manifest_sha256": file_hash(dry_store / "run_manifest.json"),
        "source_validation_sha256": file_hash(dry_store / "VALIDATION.json"),
        "parent_manifest_sha256": file_hash(parent_store / "run_manifest.json"),
        "config_sha256": file_hash(config_path), "seed": seed, "samples_per_rule_decision": sample_size,
        "sample_scope": "deterministic_per_rule_reservoir_not_a_clinical_accuracy_estimate",
        "checks": dict(checks), "sample_count": len(cases),
        "rules": {rid: {"counts": dict(counts[rid]), "unique_proposed_texts": len(unique[rid]),
            "most_common_proposals": [{"text": text[:1500], "full_chars": len(text), "count": n}
                                      for text, n in unique[rid].most_common(8)]} for rid in rules}}
    write_json(output / "SUMMARY.json", summary)
    with (output / "SOURCE_CASES.jsonl").open("w", encoding="utf-8") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(json.dumps({"checks": summary["checks"], "sample_count": len(cases),
        "rules": {rid: {"counts": v["counts"], "unique_proposed_texts": v["unique_proposed_texts"]}
                  for rid, v in summary["rules"].items()}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-store", type=Path, required=True)
    parser.add_argument("--parent-store", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.dry_store, args.parent_store, args.config, args.output)
