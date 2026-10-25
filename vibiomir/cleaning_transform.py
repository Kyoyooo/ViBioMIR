"""Pure, deterministic cleaning proposals with exact mappings and no joined evidence."""
import json

from exploration.common import domain_of, normalize
from .document_contracts import parse_blocks, valid_url
from .io import canonical_json, text_hash
from .cleaning_rules import (REVISION, check_mode, clinical_signals, failure_reason, plan_removals,
                             repeated_line_diagnostic, rulebook_hash, title_body_diagnostic)
from .cleaning_source import html_units


def source_units(version, config, html_loader=None):
    """Keep existing usable text; repair only missing or narrowly identified failures."""
    if not valid_url(version.get("source_url")):
        return [], "no_safe_content", ["invalid_source_url"]
    text = version.get("evidence_text")
    title = json.loads(version["metadata_json"])["fields"].get("title", {}).get("value")
    failure = failure_reason(text, version.get("final_url"), title)
    flags = []
    if failure is None:
        return [{"text": text, "source_kind": "stage3_text", "source_start": 0, "source_end": len(text),
                 "kind": "legacy_text", "locator": {"parent_version_id": version["version_id"], "column": "text"}}], "retained_stage3_text", flags
    flags.append(failure)
    blocks, state = parse_blocks(version.get("blocks_raw_json"))
    if state == "invalid":
        flags.append("invalid_legacy_blocks_review")
    block_units = [{"text": value, "source_kind": "legacy_block", "source_start": 0, "source_end": len(value),
                    "kind": kind, "locator": {"parent_version_id": version["version_id"], "column": "blocks", "index": index}}
                   for index, (kind, value) in enumerate(blocks) if value.strip()]
    if block_units and failure_reason("\n".join(u["text"] for u in block_units), version.get("final_url"), title) is None:
        return block_units, "recovered_legacy_blocks", flags + ["offline_recovery_review_required"]
    if version.get("html_available"):
        if html_loader is None:
            flags.append("raw_html_source_required")
        else:
            payload = html_loader(version)
            units, issue = html_units(payload, version["version_id"], config["max_html_chars"])
            if issue:
                flags.append(issue)
            if units and failure_reason("\n".join(u["text"] for u in units), title=title) is None:
                return units, "recovered_html_article", flags + ["offline_recovery_review_required"]
    return [], "no_safe_content", flags


def process_version(version, config, *, mode="dry-run", html_loader=None):
    check_mode(config, mode)
    parent = version["version_id"]
    original = version.get("evidence_text")
    original = original if isinstance(original, str) else ""
    expected = text_hash(original) if original.strip() else None
    if expected != version.get("text_sha256"):
        raise ValueError("Parent evidence hash is inconsistent: " + parent)
    units, method, flags = source_units(version, config, html_loader)
    flags.append("main_content_qa_pending")
    source_chars = sum(len(u["text"]) for u in units) if units else len(original)
    region_data, transformation_data = [], []
    virtual_text = "\n".join(u["text"] for u in units)
    proposals = plan_removals(virtual_text, domain_of(version.get("source_url") or ""), config) if units else []
    accepted = [p for p in proposals if p["decision"] == "remove"]
    cursor = 0
    for unit in units:
        begin, end = cursor, cursor + len(unit["text"])
        local_cuts = []
        for proposal in proposals:
            left, right = max(begin, proposal["start"]), min(end, proposal["end"])
            if left >= right:
                continue
            local_start, local_end = left - begin, right - begin
            transformation_data.append({"source_kind": unit["source_kind"], "source_locator_json": canonical_json(unit["locator"]),
                "source_start": unit["source_start"] + local_start, "source_end": unit["source_start"] + local_end,
                "text": unit["text"][local_start:local_end], "rule_id": proposal["rule_id"],
                "rule_revision": proposal["rule_revision"],
                "decision": "vetoed" if proposal["decision"] == "vetoed" else "proposed_removal" if mode == "dry-run" else "applied_removal",
                "reason_json": canonical_json(proposal["reasons"] or ["exact_domain_rule_match"])})
            if proposal["decision"] == "remove":
                local_cuts.append((local_start, local_end))
        offset = 0
        keep = []
        for start, stop in sorted(local_cuts):
            if offset < start:
                keep.append((offset, start))
            offset = stop
        if offset < len(unit["text"]):
            keep.append((offset, len(unit["text"])))
        for start, stop in keep:
            value = unit["text"][start:stop]
            # Preserve whitespace too: kept and removed source characters partition each unit.
            region_data.append({"source_kind": unit["source_kind"], "kind": unit["kind"],
                "source_locator_json": canonical_json(unit["locator"]), "source_start": unit["source_start"] + start,
                "source_end": unit["source_start"] + stop, "text": value,
                "normalized_text": normalize(value), "text_sha256": text_hash(value)})
        cursor = end + 1  # synthetic analysis separator, never an evidence source position
    view = "\n".join(r["text"] for r in region_data)
    metadata = json.loads(version["metadata_json"])
    title = metadata["fields"].get("title", {}).get("value")
    candidate = bool(view.strip())
    if candidate and failure_reason(view, version.get("final_url"), title):
        # Retain mapped regions for inspection, but exclude the failed body from selection.
        candidate = False
        flags.append("cleaned_view_still_failed_review")
    title_check = title_body_diagnostic(title, view)
    if title_check.get("low_overlap"):
        flags.append("title_body_low_overlap_review_only")
    inherited = set(json.loads(version.get("quality_flags_json") or "[]"))
    if "possible_title_body_mismatch" in inherited and title_check.get("overlap", 0) is not None and title_check.get("overlap", 0) >= .2:
        flags.append("legacy_title_mismatch_counterexample")
    if "\ufffd" in view:
        flags.append("replacement_characters_require_review_no_repair")
    vetoes = sum(p["decision"] == "vetoed" for p in proposals)
    if vetoes:
        flags.append("vetoed_removal_candidates_require_review")
    if not candidate:
        availability = "quarantine" if original.strip() or units else "unavailable"
    else:
        availability = "content_candidate"
    kept = sum(len(r["text"]) for r in region_data)
    removed = sum(len(t["text"]) for t in transformation_data if t["decision"] != "vetoed")
    normalized = normalize(view)
    before_signals = clinical_signals(virtual_text)
    after_signals = before_signals if virtual_text == view else clinical_signals(view)
    diagnostics = {"original_failure": failure_reason(original, version.get("final_url"), title),
        "title_body": title_check, "repetitions": repeated_line_diagnostic(virtual_text),
        "signals_before": before_signals, "signals_after": after_signals,
        "accepted_removal_candidates": len(accepted), "vetoed_removal_candidates": vetoes,
        "parent_quality_flags": sorted(inherited), "metadata_policy": "unchanged_from_stage3",
        "structure_policy": "parent_blocks_remain_authoritative_no_invented_headings_or_table_columns"}
    if units and any(diagnostics["signals_before"][k] != diagnostics["signals_after"][k] for k in ("digits", "negations", "units")):
        # Regions separated by a removed line can change token boundaries in the view.
        # No source characters are rewritten; flag the view for explicit review.
        flags.append("protected_signal_view_delta_review")
    digest = rulebook_hash(config)
    identity = {"parent": parent, "revision": REVISION, "rules": digest, "mode": mode, "method": method,
                "regions": region_data, "transformations": transformation_data, "diagnostics": diagnostics, "flags": sorted(set(flags))}
    derived_id = text_hash(canonical_json(identity))
    result = {"derived_version_id": derived_id, "doc_id": version["doc_id"], "parent_version_id": parent,
        "representative_record_id": version["representative_record_id"], "source_url": version.get("source_url"),
        "extraction_method": method, "availability": availability, "content_candidate": candidate,
        "source_chars": source_chars, "kept_chars": kept, "removed_chars": removed,
        "quarantined_chars": source_chars if not units else 0,
        "region_count": len(region_data), "transformation_count": len(transformation_data),
        "normalized_sha256": text_hash(normalized) if normalized else None,
        "metadata_json": version["metadata_json"], "language_hint": version.get("language_hint"),
        "doc_type_hint": version.get("doc_type_hint"), "diagnostics_json": canonical_json(diagnostics),
        "quality_flags_json": canonical_json(sorted(set(flags))), "source_fidelity": "mapped_source_regions",
        "content_scope": "partial", "rulebook_sha256": digest, "mode": mode,
        "is_final": False, "needs_review": True, "official_match": "unverified"}
    regions, transformations = [], []
    base = {"derived_version_id": derived_id, "parent_version_id": parent, "doc_id": version["doc_id"]}
    for ordinal, value in enumerate(region_data):
        regions.append({**base, **value, "ordinal": ordinal,
                        "region_id": text_hash(canonical_json([derived_id, "region", ordinal, value]))})
    for ordinal, value in enumerate(transformation_data):
        transformations.append({**base, **value, "ordinal": ordinal,
            "transformation_id": text_hash(canonical_json([derived_id, "transformation", ordinal, value]))})
    # Prefer intact source text over recovery and fewer unresolved issues; never longest/newest.
    critical = len(set(flags) & {"replacement_characters_require_review_no_repair", "protected_signal_view_delta_review"})
    rank = [int(candidate), -critical, -vetoes, int(method == "retained_stage3_text"), -int(bool(title_check.get("low_overlap")))]
    return {"version": result, "regions": regions, "transformations": transformations, "rank": rank}
