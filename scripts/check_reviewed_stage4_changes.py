"""Check that reviewed apply removals are exactly the approved subset of dry-run proposals."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from vibiomir.cleaning_rules import load_cleaning_config
from vibiomir.io import canonical_json, text_hash, write_json
from vibiomir.validate_cleaning import iter_export


def identity(row):
    return text_hash(canonical_json({key: row[key] for key in (
        "parent_version_id", "doc_id", "source_kind", "source_locator_json", "source_start", "source_end", "text", "rule_id")}))


def check(dry_store, apply_store, config_path, report):
    dry_store, apply_store = Path(dry_store).resolve(), Path(apply_store).resolve()
    dry = json.loads((dry_store / "run_manifest.json").read_text())
    applied = json.loads((apply_store / "run_manifest.json").read_text())
    if dry["state"] != "complete_dry_run" or applied["state"] != "complete_provisional":
        raise ValueError("Both exports must be complete")
    if dry["signature"]["parent"] != applied["signature"]["parent"] or dry["expected"] != applied["expected"]:
        raise ValueError("Dry-run/apply parent input or scope differs")
    rules = {r["id"]: r for r in load_cleaning_config(config_path)["rules"] if r["enabled"]}
    expected, actual = Counter(), Counter()
    by_rule = {}
    retained = []
    for row in iter_export(dry_store, dry, "transformations"):
        if row["decision"] != "proposed_removal":
            continue
        rule = rules.get(row["rule_id"])
        text = row["text"].splitlines()[0].strip() if rule and rule["action"] == "trim_tail" else row["text"].strip()
        if rule and re.fullmatch(rule["pattern"], text, re.I):
            expected[identity(row)] += 1
            bucket = by_rule.setdefault(row["rule_id"], {"expected_removals": 0, "expected_chars": 0, "actual_removals": 0, "actual_chars": 0})
            bucket["expected_removals"] += 1
            bucket["expected_chars"] += len(row["text"])
        else:
            retained.append({"rule_id": row["rule_id"], "doc_id": row["doc_id"], "source_start": row["source_start"],
                             "source_end": row["source_end"], "chars": len(row["text"])})
    for row in iter_export(apply_store, applied, "transformations"):
        if row["decision"] != "applied_removal":
            continue
        actual[identity(row)] += 1
        bucket = by_rule.setdefault(row["rule_id"], {"expected_removals": 0, "expected_chars": 0, "actual_removals": 0, "actual_chars": 0})
        bucket["actual_removals"] += 1
        bucket["actual_chars"] += len(row["text"])
    if actual != expected:
        raise ValueError(f"Reviewed removal set differs: missing={sum((expected-actual).values())}, unexpected={sum((actual-expected).values())}")
    result = {"comparison": "passed", "expected_removals": sum(expected.values()), "actual_removals": sum(actual.values()),
              "retained_original_proposals": len(retained), "by_rule": by_rule,
              "interpretation": "Apply introduced no removal outside the reviewed subset of the validated original dry-run."}
    write_json(report, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-store", type=Path, required=True)
    parser.add_argument("--apply-store", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    check(args.dry_store, args.apply_store, args.config, args.report)
