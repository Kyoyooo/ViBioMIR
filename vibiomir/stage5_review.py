"""Manual post-cleaning/chunk QA: resumable catalog, sampling, export, review.

No source manifest is changed by reviewer decisions. Random-estimation samples
remain distinct from domain/type coverage supplements and risk investigations.
"""
import argparse
from collections import Counter
import csv
from html import escape
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .chunk_batches import batches, read_json, read_rows
from .chunk_pipeline import prepare_packet, readonly_store, source_identity
from .cleaning_source import RawHTMLLoader
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .review_common import completed, frozen_sample, priority, require, separate, wilson, workflow, write_csv, write_jsonl

FIELDS = ["doc_id", "role", "reviewer", "language_reviewed", "main_content", "meaning_preserved",
          "chunk_structure", "decision", "reason", "issue_codes"]
CHECKS = ("main_content", "meaning_preserved", "chunk_structure")
DECISIONS = {"accept", "needs_fix", "quarantine", "uncertain"}
LANGUAGES = {"vi", "en", "zh", "mixed", "unknown"}


def source_snapshot(config):
    parent, snapshot = completed(config["input_dir"])
    require(parent["identity"]["operation"] == "prepare-chunk-inputs", "Prepared inputs required")
    require(parent["identity"]["source"] == source_identity(Path(config["stage4_dir"]), Path(config["stage3_dir"])),
            "Prepared inputs do not belong to these stage-3/4 stores")
    return parent, snapshot


def diagnostics(packet):
    text = "\n".join(region["text"] for region in packet["regions"])
    kinds = {unit["kind"] for region in packet["regions"] for unit in region["units"]}
    flags = []
    for condition, flag in ((not text.strip(), "no_content"), (not packet.get("title"), "missing_title"),
                            (packet.get("unmapped_blocks", 0) > 0, "unmapped_blocks"),
                            (len(text) > 100_000, "long_document"),
                            (bool(re.search(r"captcha|access denied|đăng nhập|验证码|访问受限", text, re.I)), "possible_gate"),
                            (bool(re.search(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|ml|mmol|g/kg)\b", text, re.I)), "dose_or_unit"),
                            (bool(re.search(r"\b(?:không|chống chỉ định|not|contraindicat\w*)\b|禁止|无|不建议", text, re.I)), "negation_or_condition")):
        if condition:
            flags.append(flag)
    types = [kind for condition, kind in (("tr" in kinds, "table"),
             (bool(re.search(r"^(?:Question|Hỏi|Câu hỏi|问题)\s*[:：]", text, re.I | re.M)), "qa"),
             (any(k in kinds for k in ("h", "h1", "h2", "h3", "h4", "h5", "h6")), "headings"),
             ("li" in kinds, "list")) if condition] or ["plain_or_unknown"]
    return len(text), flags, types


def catalog(config, *, resume=False, stop_after_batches=None):
    parent, snapshot = source_snapshot(config)
    root = Path(config["qa_dir"])
    separate(root, [config[k] for k in ("input_dir", "stage3_dir", "stage4_dir")])
    root.mkdir(parents=True, exist_ok=True)
    identity = {"source": snapshot, "seed": config["seed"], "code": file_hash(Path(__file__)),
                "common_code": file_hash(Path(__file__).with_name("review_common.py"))}
    manifest_path = root / "catalog_manifest.json"
    require(resume or not manifest_path.exists(), "QA catalog exists; repeat with --resume")
    if manifest_path.exists():
        require(read_json(manifest_path)["identity"] == identity, "QA catalog source/code/seed changed; use a new qa_dir")
    else:
        require(not (root / "catalog.sqlite3").exists(), "Catalog database exists without its manifest; inspect it before starting a new run")
    require(stop_after_batches is None or stop_after_batches > 0, "Positive stop-after-batches required")
    with writer_lock(root), readonly_store(config["stage3_dir"], "document_store.sqlite3") as source, \
            sqlite3.connect(root / "catalog.sqlite3") as db:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS documents(doc_id INTEGER PRIMARY KEY,version TEXT,parent TEXT,
            batch INTEGER,row_index INTEGER,url TEXT,domain TEXT,language TEXT,availability TEXT,
            characters INTEGER,flags TEXT,types TEXT,priority TEXT);
          CREATE INDEX IF NOT EXISTS document_priority ON documents(priority,doc_id);
          CREATE INDEX IF NOT EXISTS document_domain ON documents(domain,priority);
          CREATE TABLE IF NOT EXISTS committed(batch INTEGER PRIMARY KEY,sha256 TEXT,rows INTEGER);
        """)
        write_json(manifest_path, {"state": "building", "identity": identity})
        processed = added = 0
        for index, (path, info) in enumerate(batches(config["input_dir"], parent["signature"])):
            old = db.execute("SELECT sha256,rows FROM committed WHERE batch=?", (index,)).fetchone()
            if old:
                require(old == (info["sha256"], info["documents"]), "Catalog batch changed")
            else:
                with db:
                    count = 0
                    for row_index, packet in enumerate(read_rows(path)):
                        url = None
                        if packet["parent_version_id"]:
                            record = source.execute("SELECT source_url FROM versions WHERE version_id=?", (packet["parent_version_id"],)).fetchone()
                            require(record is not None, f"Missing parent {packet['doc_id']}")
                            url = record[0]
                        try:
                            domain = urlsplit(url or "").hostname or "unknown"
                        except ValueError:
                            domain = "unknown"
                        characters, flags, types = diagnostics(packet)
                        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                            packet["doc_id"], packet["derived_version_id"], packet["parent_version_id"], index, row_index,
                            url, domain, packet.get("language_hint") or "unknown", packet["availability"], characters,
                            canonical_json(flags), canonical_json(types), priority(config["seed"], packet["doc_id"], packet["derived_version_id"])))
                        count += 1
                    require(count == info["documents"], "Prepared batch row count differs")
                    db.execute("INSERT INTO committed VALUES (?,?,?)", (index, info["sha256"], count))
                added += 1
            processed += info["documents"]
            if added and (added % 25 == 0 or added == 1):
                print(json.dumps({"phase": "qa_catalog", "batch": index, "documents": processed}), flush=True)
            if stop_after_batches and added >= stop_after_batches:
                write_json(manifest_path, {"state": "paused", "identity": identity, "documents": processed})
                return
        require(processed == parent["documents"], "Catalog scope differs")
        require(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == processed, "Catalog document count differs")
        write_json(manifest_path, {"state": "complete", "identity": identity, "documents": processed,
                   "domain_note": "Stage-3 indexed source_url; selected payloads rechecked during export",
                   "language_note": "Heuristic hints, not verified languages", "human_qa": "pending"})


def sample(config, *, exclude_ids=None):
    root = Path(config["qa_dir"])
    manifest = read_json(root / "catalog_manifest.json")
    require(manifest["state"] == "complete", "Complete the QA catalog first")
    _, snapshot = source_snapshot(config)
    require(snapshot == manifest["identity"]["source"], "Catalog has another source")
    require(config["seed"] == manifest["identity"]["seed"], "Catalog seed differs; use another qa_dir")
    excluded = set(exclude_ids or [])
    require(all(type(i) is int for i in excluded), "Excluded IDs must be integers")
    with writer_lock(root), sqlite3.connect(root / "catalog.sqlite3") as db:
        db.row_factory = sqlite3.Row
        selected = {}
        eligible_count, domains, languages, types = 0, Counter(), Counter(), Counter()
        domain_counts, language_counts, type_counts = Counter(), Counter(), Counter()
        random_count = 0
        # Ordered hash sampling is uniform without replacement over eligible IDs.
        for item in db.execute("SELECT * FROM documents ORDER BY priority,doc_id"):
            if item["doc_id"] in excluded:
                continue
            item = dict(item)
            is_eligible = bool(item["characters"] and item["version"] and item["availability"] == "content_candidate")
            if not is_eligible:
                continue
            eligible_count += 1
            domain_counts[item["domain"]] += 1
            language_counts[item["language"]] += 1
            labels = json.loads(item["types"])
            for label in labels:
                type_counts[label] += 1
            cohorts = []
            if random_count < config["random_documents"]:
                cohorts.append("random")
                random_count += 1
            if domains[item["domain"]] < config["minimum_per_domain"]:
                cohorts.append("domain_coverage")
            if languages[item["language"]] < config["minimum_per_language_hint"]:
                cohorts.append("language_coverage")
            if any(types[label] < config["minimum_per_type"] for label in labels):
                cohorts.append("type_coverage")
            if cohorts:
                item.update(cohorts=cohorts)
                selected[item["doc_id"]] = item
                domains[item["domain"]] += 1
                languages[item["language"]] += 1
                types.update(labels)
        # A separate severity-ordered investigation, rather than relabeling the
        # first random documents as the entire risk sample.
        risk_count = 0
        sql = """SELECT * FROM documents WHERE characters>0 AND version IS NOT NULL AND availability='content_candidate'
          AND (instr(flags,'possible_gate') OR instr(flags,'unmapped_blocks') OR instr(flags,'long_document')
               OR instr(flags,'dose_or_unit') OR instr(flags,'negation_or_condition'))
          ORDER BY (instr(flags,'possible_gate')>0)*100 + (instr(flags,'unmapped_blocks')>0)*50
                   + (instr(flags,'long_document')>0)*10 + (instr(flags,'dose_or_unit')>0)*2 DESC, priority,doc_id"""
        for row in db.execute(sql):
            if risk_count >= config["risk_documents"]:
                break
            if row["doc_id"] in excluded:
                continue
            item = selected.setdefault(row["doc_id"], {**dict(row), "cohorts": []})
            item["cohorts"].append("risk")
            risk_count += 1
        require(selected, "No eligible documents; inspect availability/exclusions")
        ordered = sorted(selected.values(), key=lambda item: item["doc_id"])
        double_count = math.ceil(config["second_review_fraction"] * len(ordered))
        double = {item["doc_id"] for item in sorted(ordered, key=lambda r: priority(config["seed"], "second", r["doc_id"]))[:double_count]}
        for item in ordered:
            item["second_review_required"] = item["doc_id"] in double
        payload = {"source_catalog_sha256": file_hash(root / "catalog_manifest.json"),
                   "seed": config["seed"], "eligible_population": eligible_count,
                   "total_catalog_documents": manifest["documents"],
                   "population_by_availability": dict(db.execute("SELECT availability,COUNT(*) FROM documents GROUP BY availability")),
                   "excluded_ids": sorted(excluded), "random_sample": random_count,
                   "selected_documents": len(ordered), "second_review_documents": double_count,
                   "population_by_domain": dict(domain_counts), "population_by_language_hint": dict(language_counts),
                   "population_by_type": dict(type_counts), "selection": ordered}
        signature = text_hash(canonical_json(payload))
        if (root / "sample.json").exists():
            require(read_json(root / "sample.json")["signature"] == signature,
                    "Sample already frozen with another policy; use a new qa_dir")
            return frozen_sample(root / "sample.json")
        payload["signature"] = signature
        write_json(root / "sample.json", payload)
        write_json(root / "selected_doc_ids.json", [item["doc_id"] for item in ordered])
        write_csv(root / "reviews_template.csv", FIELDS, (
            {**dict.fromkeys(FIELDS, ""), "doc_id": item["doc_id"], "role": role}
            for item in ordered for role in (["primary", "secondary"] if item["second_review_required"] else ["primary"])))
        return payload


def export_cases(config):
    root = Path(config["qa_dir"])
    selection = frozen_sample(root / "sample.json")
    require(file_hash(root / "catalog_manifest.json") == selection["source_catalog_sha256"], "Catalog changed")
    parent, _ = source_snapshot(config)
    stores = {}
    for name, path in config["stores"].items():
        if (Path(path) / "manifest.json").exists() and (Path(path) / "VALIDATION.json").exists():
            manifest, snapshot = completed(path, validated=True)
            require(manifest["identity"]["parent_signature"] == parent["signature"], f"Wrong QA chunk parent: {name}")
            require(manifest["identity"]["strategy"] == name, f"Wrong strategy: {name}")
            stores[name] = (path, manifest, snapshot)
    export_identity = {"sample": selection["signature"], "stores": {k: v[2] for k, v in stores.items()}}
    cases = root / "cases"
    with writer_lock(root), readonly_store(config["stage3_dir"], "document_store.sqlite3") as db3, \
            readonly_store(config["stage4_dir"], "cleaning.sqlite3") as db4:
        if (root / "export_manifest.json").exists():
            previous = read_json(root / "export_manifest.json")
            require(previous["identity"] == export_identity,
                    "QA export stores changed; keep the frozen review run and use a new qa_dir")
            if previous["state"] == "complete":
                for doc_id, digest in previous["case_hashes"].items():
                    require(file_hash(cases / f"{doc_id}.json") == digest and
                            file_hash(cases / f"{doc_id}.html") == previous["html_hashes"][doc_id], "Frozen review files changed")
                return {"state": "complete", "cases": previous["cases"], "reused": True}
        cases.mkdir(exist_ok=True)
        chosen = {row["doc_id"]: row for row in selection["selection"]}
        input_parts = {info["index"]: (path, info) for path, info in batches(config["input_dir"], parent["signature"])}
        chunk_parts = {name: {i["index"]: (p, i) for p, i in batches(path, manifest["signature"])}
                       for name, (path, manifest, _) in stores.items()}
        loader = RawHTMLLoader(db3, read_json(Path(config["stage3_dir"]) / "run_manifest.json"), config["data_dir"]) if config.get("data_dir") else None
        for index in sorted({row["batch"] for row in chosen.values()}):
            packets = {p["doc_id"]: p for p in read_rows(input_parts[index][0]) if p["doc_id"] in chosen}
            outputs = {name: {row["doc_id"]: row for row in read_rows(parts[index][0]) if row["doc_id"] in packets}
                       for name, parts in chunk_parts.items()}
            for doc_id, packet in packets.items():
                item = chosen[doc_id]
                row = db3.execute("SELECT payload,representative_record_id FROM versions WHERE version_id=?", (item["parent"],)).fetchone()
                require(row is not None, f"Missing source: {doc_id}")
                source = json.loads(row[0])
                source["representative_record_id"] = row[1]
                require(source.get("source_url") == item["url"], "Catalog source URL differs from parent payload")
                result_row = db4.execute("SELECT payload FROM results WHERE derived_version_id=?", (item["version"],)).fetchone()
                require(result_row is not None, f"Missing cleaned version: {doc_id}")
                result = json.loads(result_row[0])
                rebuilt = prepare_packet(doc_id, {"selected_derived_version_id": item["version"],
                    "selected_parent_id": item["parent"], "availability": packet["availability"]}, result, source, loader)
                require(rebuilt == packet, f"Exported source packet changed: {doc_id}")
                for name, rows in outputs.items():
                    require(doc_id in rows and rows[doc_id]["derived_version_id"] == packet["derived_version_id"],
                            f"QA chunk version differs: {name}/{doc_id}")
                case = {"selection": item, "packet": packet, "parent": source,
                        "transformations": result.get("transformations", []),
                        "strategies": {name: rows[doc_id] for name, rows in outputs.items()},
                        "raw_html_available_in_parent": source.get("html_available", False),
                        "source_fidelity": source.get("source_fidelity") or "unknown"}
                raw_html = loader(source) if loader and source.get("html_available") else None
                case["stored_html_trim"] = raw_html["html"] if raw_html else None
                write_json(cases / f"{doc_id}.json", case)
                sections = [("Source URL", item["url"] or "unknown"), ("Stored source text", source.get("evidence_text") or ""),
                            ("Stored source blocks", source.get("blocks_raw_json") or ""),
                            ("Cleaned exact regions", json.dumps(packet["regions"], ensure_ascii=False, indent=2)),
                            ("Cleaning transformations", json.dumps(case["transformations"], ensure_ascii=False, indent=2))]
                if case["stored_html_trim"] is not None:
                    sections.append(("Stored html_trim snapshot (escaped, not a live webpage)", case["stored_html_trim"]))
                for name, rows in outputs.items():
                    sections.append((name + " chunks: original evidence and retrieval context", json.dumps(rows[doc_id]["chunks"], ensure_ascii=False, indent=2)))
                html = '<!doctype html><meta charset="utf-8"><title>Source review</title><style>pre{white-space:pre-wrap}body{max-width:1100px;margin:2em auto}</style>'
                html += f"<h1>Document {doc_id}</h1><p>Source fidelity: {escape(case['source_fidelity'])}. Review decisions belong in the CSV.</p>"
                html += "".join(f"<details><summary>{escape(title)}</summary><pre>{escape(text)}</pre></details>" for title, text in sections)
                (cases / f"{doc_id}.html").write_text(html, encoding="utf-8")
        require(all((cases / f"{row['doc_id']}.json").exists() for row in chosen.values()), "Incomplete case export")
        if loader:
            loader.verify_files()
        links = "".join(f'<li><a href="cases/{i}.html">{i}</a> {escape(row["language"])} / {escape(row["domain"])} / {escape(", ".join(row["cohorts"]))}</li>' for i, row in chosen.items())
        (root / "index.html").write_text('<!doctype html><meta charset="utf-8"><h1>Manual QA cases</h1><ul>' + links + '</ul>', encoding="utf-8")
        write_json(root / "export_manifest.json", {"state": "complete", "identity": export_identity,
                   "cases": len(chosen), "case_hashes": {str(i): file_hash(cases / f"{i}.json") for i in chosen},
                   "html_hashes": {str(i): file_hash(cases / f"{i}.html") for i in chosen},
                   "human_qa": "pending", "unavailable_strategies": sorted(set(config["stores"]) - set(stores))})


def summarize_reviews(config, reviews_file, reviewers_file, technical_report=None):
    root = Path(config["qa_dir"])
    selection, exported = frozen_sample(root / "sample.json"), read_json(root / "export_manifest.json")
    require(exported["state"] == "complete" and exported["identity"]["sample"] == selection["signature"], "Export the frozen sample first")
    reviewers = read_json(reviewers_file)
    chosen = {row["doc_id"]: row for row in selection["selection"]}
    reviews = {}
    with Path(reviews_file).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames and set(FIELDS) <= set(reader.fieldnames), f"Review CSV needs columns {FIELDS}")
        for row in reader:
            require(None not in row and all(isinstance(v, str) for v in row.values()), "Malformed review CSV cells")
            row = {key: value.strip() for key, value in row.items()}
            for key in ("role", "language_reviewed", *CHECKS, "decision"):
                row[key] = row[key].lower()
            doc_id = int(row["doc_id"])
            require(doc_id in chosen and row["role"] in {"primary", "secondary", "adjudication"}, "Unknown review document/role")
            if not any(row.get(k, "").strip() for k in (*CHECKS, "decision")):
                continue
            if any(not row.get(k) for k in ("reviewer", "language_reviewed", *CHECKS, "decision")):
                continue
            person = row["reviewer"].strip()
            require(person in reviewers, f"Register reviewer: {person}")
            language = row["language_reviewed"].strip()
            required_languages = set(language.split("+"))
            require(language == "unknown" or required_languages <= {"vi", "en", "zh"}, "Declare vi/en/zh or combinations such as vi+en")
            if language == "unknown":
                require(row["decision"] == "uncertain" and all(row[k] == "uncertain" for k in CHECKS), "Unknown language cannot be approved")
            else:
                require(required_languages <= set(reviewers[person]["languages"]), f"Reviewer lacks declared language competence: {person}")
            require(row["decision"] in DECISIONS and all(row[k] in {"pass", "fail", "uncertain"} for k in CHECKS), "Invalid/incomplete review")
            require(row["decision"] != "accept" or all(row[k] == "pass" for k in CHECKS), "Accept requires passing checks")
            require(row["decision"] == "accept" or row["reason"].strip(), "Non-accept decisions need a reason")
            key = (doc_id, row["role"])
            require(key not in reviews, "Duplicate reviewer slot")
            row["clinical_qualified"] = reviewers[person].get("clinical_qualified") is True
            reviews[key] = row
    decisions, random_pass, random_reviewed, missing, unresolved, clinical = [], 0, 0, 0, 0, 0
    issues, domain_counts, language_counts = Counter(), Counter(), Counter()
    for doc_id, item in chosen.items():
        require(file_hash(root / "cases" / f"{doc_id}.json") == exported["case_hashes"][str(doc_id)], "Review evidence changed")
        require(file_hash(root / "cases" / f"{doc_id}.html") == exported["html_hashes"][str(doc_id)], "Review page changed")
        primary, secondary, adjudication = (reviews.get((doc_id, role)) for role in ("primary", "secondary", "adjudication"))
        if primary and secondary:
            require(primary["reviewer"] != secondary["reviewer"], "Independent second reviewer required")
        final = primary
        disagreement = bool(primary and secondary and any(primary[k] != secondary[k] for k in (*CHECKS, "decision", "language_reviewed")))
        if adjudication:
            require(primary is not None and secondary is not None, "Adjudication requires two initial reviews")
            require(adjudication["reviewer"] not in {primary["reviewer"], secondary["reviewer"]}, "Independent adjudicator required")
            final = adjudication
        elif disagreement:
            final = None
            unresolved += 1
        required_missing = primary is None or (item["second_review_required"] and secondary is None)
        if required_missing:
            missing += 1
            final = None
        if final and "random" in item["cohorts"]:
            random_reviewed += 1
            random_pass += final["main_content"] == "pass"
        confirming = [r for r in (primary, secondary, adjudication) if final and r and r["clinical_qualified"] and
                      all(r[k] == final[k] for k in (*CHECKS, "decision", "language_reviewed"))]
        clinical_confirmed = bool(final and confirming and final["meaning_preserved"] == "pass")
        if clinical_confirmed:
            clinical += 1
        codes = [code.strip() for code in final["issue_codes"].split(";") if code.strip()] if final else []
        issues.update(set(codes))
        if final:
            domain_counts[item["domain"]] += 1
            language_counts[final["language_reviewed"]] += 1
        decisions.append({"doc_id": doc_id, "cohorts": item["cohorts"], "decision": final["decision"] if final else "pending",
                          "reason": final["reason"] if final else "missing review or unresolved disagreement",
                          "issue_codes": codes,
                          "language_reviewed": final["language_reviewed"] if final else None,
                          "clinical_confirmed": clinical_confirmed})
    rate = random_pass / random_reviewed if random_reviewed else None
    fixture_gate = None
    if technical_report:
        checks = read_json(technical_report)
        require(checks["successful"] is True and checks["meaning_preservation_fixtures_passed"] is True, "CPU tests/meaning fixtures have not passed")
        for name in ("stage5_review.py", "review_common.py", "chunking.py", "chunk_pipeline.py"):
            require(checks["source_code"][name] == file_hash(Path(__file__).with_name(name)), "Test evidence refers to another source revision")
        fixture_gate = {"passed": True, "report_sha256": file_hash(technical_report)}
    report = {"sample_signature": selection["signature"], "review_file_sha256": file_hash(reviews_file),
              "reviewers_file_sha256": file_hash(reviewers_file), "selected": len(chosen), "missing_required_reviews": missing,
              "unresolved_disagreements": unresolved, "random_population": selection["eligible_population"],
              "random_sample": selection["random_sample"], "random_reviewed": random_reviewed, "random_main_content_pass": random_pass,
              "random_main_content_rate": rate, "random_main_content_wilson_95": wilson(random_pass, random_reviewed),
              "rate_scope": "Reviewed random cohort only; supplements/risk cases excluded; incomplete reviews can bias estimates",
              "clinical_confirmed": clinical, "clinical_qa": "reviewed_sample_only" if clinical == len(chosen) else "pending",
              "issue_counts": dict(issues), "reviewed_cases_by_domain": dict(domain_counts),
              "reviewed_cases_by_actual_language": dict(language_counts),
              "proposed_main_content_target": config["proposed_main_content_target"],
              "automated_meaning_fixtures": fixture_gate,
              "validated_strategies_in_review": sorted(exported["identity"]["stores"]),
              "missing_strategies": exported["unavailable_strategies"],
              "main_content_target_met": rate is not None and rate >= config["proposed_main_content_target"],
              "review_gate_passed": missing == unresolved == 0 and rate is not None and rate >= config["proposed_main_content_target"] and
                                    all(d["decision"] == "accept" and d["clinical_confirmed"] for d in decisions),
              "corpus_freeze": "not performed; decisions are proposals and source manifests remain unchanged"}
    with writer_lock(root):
        write_json(root / "REVIEW_REPORT.json", report)
        write_jsonl(root / "decisions.jsonl", decisions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/stage5_workflow.json")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("catalog")
    build.add_argument("--resume", action="store_true")
    build.add_argument("--stop-after-batches", type=int)
    choose = commands.add_parser("sample")
    choose.add_argument("--exclude-ids-file", type=Path)
    commands.add_parser("export")
    review = commands.add_parser("summarize")
    review.add_argument("--reviews", required=True, type=Path)
    review.add_argument("--reviewers", required=True, type=Path)
    review.add_argument("--technical-report", type=Path)
    args = parser.parse_args()
    try:
        config = workflow(args.config)
        if args.command == "catalog":
            result = catalog(config, resume=args.resume, stop_after_batches=args.stop_after_batches)
        elif args.command == "sample":
            result = sample(config, exclude_ids=read_json(args.exclude_ids_file) if args.exclude_ids_file else None)
            result = {k: v for k, v in result.items() if k != "selection"}
        elif args.command == "export":
            result = export_cases(config)
        else:
            result = summarize_reviews(config, args.reviews, args.reviewers, args.technical_report)
        print(json.dumps(result or {"state": "done"}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
