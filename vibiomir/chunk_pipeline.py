"""Manual S05 preparation, chunk building and validation, bounded by one batch."""
from contextlib import contextmanager, ExitStack
import fcntl
import json
from pathlib import Path
import sqlite3
import time

from .chunk_batches import (batches, commit_batch, finish, output_store, read_json, read_rows)
from .chunking import chunk_document, count, settings, structure_units
from .document_contracts import mapped_blocks, parse_blocks
from .io import canonical_json, file_hash, text_hash, write_json


def code_signature():
    root = Path(__file__).parent
    return {name: file_hash(root / name) for name in
            ("chunk_batches.py", "chunking.py", "chunk_models.py", "chunk_pipeline.py", "document_contracts.py")}


@contextmanager
def readonly_store(directory, database):
    directory = Path(directory).resolve()
    db_path = directory / database
    if not db_path.is_file() or not (directory / ".store.lock").is_file():
        raise ValueError(f"Missing completed source store: {directory}")
    with (directory / ".store.lock").open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"Source is in use; stop its writer first: {directory}") from exc
        wal = Path(str(db_path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError(f"Uncheckpointed source WAL: {wal}. Finish and close the source build first.")
        db = sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            yield db
        finally:
            db.close()


def source_identity(cleaned_dir, store_dir):
    cleaned_dir, store_dir = Path(cleaned_dir), Path(store_dir)
    manifest = read_json(cleaned_dir / "run_manifest.json")
    validation = read_json(cleaned_dir / "VALIDATION.json")
    if manifest.get("mode") != "apply" or manifest.get("state") != "complete_provisional":
        raise ValueError("Stage 5 requires a completed stage-4 apply output; dry-run and partial outputs are not accepted")
    if validation.get("validation") != "passed" or not validation.get("source_slices_verified"):
        raise ValueError("Run validate-cleaned successfully before stage 5")
    parent = manifest["signature"]["parent"]
    for key, name in [("manifest_sha256", "run_manifest.json"), ("validation_sha256", "VALIDATION.json")]:
        if file_hash(store_dir / name) != parent[key]:
            raise ValueError(f"Stage-3 {name} does not match the stage-4 parent")
    return {"stage4_manifest": file_hash(cleaned_dir / "run_manifest.json"),
            "stage4_validation": file_hash(cleaned_dir / "VALIDATION.json"),
            "stage3_manifest": parent["manifest_sha256"],
            "verification": "validated stage4 snapshot + per-used-source slice; no full SQLite rehash"}


def prepare_packet(doc_id, selection, result, parent, html_loader=None):
    packet = {"doc_id": doc_id, "derived_version_id": selection["selected_derived_version_id"],
              "parent_version_id": selection.get("selected_parent_id"),
              "availability": selection["availability"], "title": None,
              "language_hint": None, "regions": [], "unmapped_blocks": 0}
    if result is None:
        if packet["derived_version_id"] is not None:
            raise ValueError(f"Selected version missing for doc_id={doc_id}")
        return packet
    version = result["version"]
    if (version["doc_id"] != doc_id or parent["doc_id"] != doc_id or
            version["derived_version_id"] != packet["derived_version_id"] or
            version["parent_version_id"] != parent["version_id"] or
            packet["parent_version_id"] != parent["version_id"] or
            version["availability"] != "content_candidate"):
        raise ValueError(f"Selected version relationship mismatch for doc_id={doc_id}")
    blocks, _ = parse_blocks(parent["blocks_raw_json"])
    body = parent.get("evidence_text") or ""
    mapped = list(mapped_blocks(body, blocks))
    packet["unmapped_blocks"] = sum(b["mapping_state"] == "unmapped_exact" for b in mapped)
    packet["title"] = json.loads(version["metadata_json"])["fields"].get("title", {}).get("value")
    packet["language_hint"] = version.get("language_hint")
    for region in result["regions"]:
        if any(region[key] != version[key] for key in ("doc_id", "derived_version_id", "parent_version_id")):
            raise ValueError(f"Region version mismatch for doc_id={doc_id}")
        locator = json.loads(region["source_locator_json"])
        if region["source_kind"] == "stage3_text":
            source = body
        elif region["source_kind"] == "legacy_block":
            source = blocks[locator["index"]][1]
        elif region["source_kind"] == "html_text_node":
            if html_loader is None:
                raise ValueError("HTML recovery found: add --data-dir Data to verify the original HTML source")
            loaded = html_loader({**parent, "representative_record_id": version["representative_record_id"]})
            if loaded is None:
                raise ValueError(f"Missing original HTML for doc_id={doc_id}")
            source = loaded["html"]
        else:
            raise ValueError(f"Unsupported source kind: {region['source_kind']}")
        start, end = region["source_start"], region["source_end"]
        if not (0 <= start < end <= len(source)) or source[start:end] != region["text"] or text_hash(region["text"]) != region["text_sha256"]:
            raise ValueError(f"Source slice/hash mismatch for region={region['region_id']}")
        item = {key: region[key] for key in ("region_id", "text", "source_kind", "source_locator_json", "source_start", "source_end", "kind")}
        item["units"] = structure_units(region, mapped if region["source_kind"] == "stage3_text" else ())
        packet["regions"].append(item)
    return packet


def prepare_inputs(cleaned_dir, store_dir, output_dir, *, batch_documents=500,
                   max_documents=None, stop_after_batches=None, resume=False, data_dir=None):
    if batch_documents < 1 or (max_documents is not None and max_documents < 1) or (stop_after_batches is not None and stop_after_batches < 1):
        raise ValueError("Batch/document limits must be positive")
    source = source_identity(cleaned_dir, store_dir)
    identity = {"operation": "prepare-chunk-inputs", "source": source, "code": code_signature(),
                "batch_documents": batch_documents, "max_documents": max_documents}
    with ExitStack() as stack:
        source4 = stack.enter_context(readonly_store(cleaned_dir, "cleaning.sqlite3"))
        source3 = stack.enter_context(readonly_store(store_dir, "document_store.sqlite3"))
        source_manifest = read_json(Path(cleaned_dir) / "run_manifest.json")
        if source4.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] != source_manifest["tables"]["document_selection"]["rows"]:
            raise ValueError("Stage-4 SQLite document count does not match its manifest")
        loader = None
        if data_dir:
            from .cleaning_source import RawHTMLLoader
            loader = RawHTMLLoader(source3, read_json(Path(store_dir) / "run_manifest.json"), data_dir)
        directory, manifest, committed = stack.enter_context(output_store(output_dir, identity, resume))
        if manifest["state"] == "complete":
            return manifest
        done = sum(info["documents"] for _, info in committed)
        last_id = committed[-1][1]["last_doc_id"] if committed else None
        cursor = source4.execute("SELECT doc_id, state, selection_json FROM jobs" +
                    (" WHERE doc_id > ?" if last_id is not None else "") + " ORDER BY doc_id",
                    (last_id,) if last_id is not None else ())
        rows, added = [], 0
        for doc_id, state, selection_json in cursor:
            if max_documents is not None and done >= max_documents:
                break
            if state != "complete":
                raise ValueError(f"Stage-4 document not complete: {doc_id}")
            selection = json.loads(selection_json)
            selected = selection["selected_derived_version_id"]
            result = parent = None
            if selected:
                record = source4.execute("SELECT payload FROM results WHERE derived_version_id=?", (selected,)).fetchone()
                if record is None:
                    raise ValueError(f"Missing stage-4 version {selected}")
                result = json.loads(record[0])
                record = source3.execute("SELECT payload FROM versions WHERE version_id=?", (result["version"]["parent_version_id"],)).fetchone()
                if record is None:
                    raise ValueError(f"Missing stage-3 parent for {selected}")
                parent = json.loads(record[0])
            rows.append(prepare_packet(doc_id, selection, result, parent, loader))
            done += 1
            if len(rows) == batch_documents:
                committed.append(commit_batch(directory, manifest["signature"], len(committed), rows))
                rows, added = [], added + 1
                if stop_after_batches and added >= stop_after_batches:
                    return finish(directory, manifest, committed, False)
        if rows:
            committed.append(commit_batch(directory, manifest["signature"], len(committed), rows))
        return finish(directory, manifest, committed, True)


def build_chunks(input_dir, output_dir, tokenizer, tokenizer_signature, *, strategy="structure",
                 config=None, resume=False, stop_after_batches=None, encoder=None, encoder_registry=None,
                 on_batch=None):
    started = time.monotonic()
    input_dir = Path(input_dir)
    parent = read_json(input_dir / "manifest.json")
    if parent.get("state") != "complete" or parent["identity"]["operation"] != "prepare-chunk-inputs":
        raise ValueError("Chunk inputs must be complete; resume prepare-chunk-inputs first")
    config = settings(config)
    if stop_after_batches is not None and stop_after_batches < 1:
        raise ValueError("stop_after_batches must be positive")
    if strategy == "semantic" and encoder is None:
        raise ValueError("Semantic strategy requires the GPU encoder")
    stable_encoder = {k: encoder_registry[k] for k in ("model_id", "revision", "pooling", "dtype", "parameters")} if encoder_registry else None
    identity = {"operation": "build-chunks", "parent_signature": parent["signature"],
                "code": code_signature(), "strategy": strategy, "config": config,
                "tokenizer": tokenizer_signature, "encoder": stable_encoder}
    with output_store(output_dir, identity, resume) as (directory, manifest, committed):
        if manifest["state"] == "complete":
            return manifest
        if encoder_registry and encoder_registry not in manifest.setdefault("encoder_executions", []):
            manifest["encoder_executions"].append(encoder_registry)
            write_json(directory / "manifest.json", manifest)
        added = 0
        for index, (path, _) in enumerate(batches(input_dir, parent["signature"])):
            if index < len(committed):
                continue
            rows = [chunk_document(packet, tokenizer, config, strategy, manifest["signature"], encoder)
                    for packet in read_rows(path)]
            committed.append(commit_batch(directory, manifest["signature"], index, rows))
            if on_batch:
                on_batch(committed[-1][0])
            added += 1
            if stop_after_batches and added >= stop_after_batches:
                return finish(directory, manifest, committed, False)
        manifest["elapsed_seconds_last_invocation"] = round(time.monotonic() - started, 3)
        return finish(directory, manifest, committed, True)


def verify_chunk(chunk, packet, region, tokenizer, config, identity, strategy):
    start, end = chunk["region_start"], chunk["region_end"]
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(region["text"])):
        raise ValueError("Invalid chunk offsets")
    evidence = region["text"][start:end]
    expected = {"doc_id": packet["doc_id"], "derived_version_id": packet["derived_version_id"],
                "parent_version_id": packet["parent_version_id"], "source_kind": region["source_kind"],
                "source_locator_json": region["source_locator_json"],
                "source_start": region["source_start"] + start, "source_end": region["source_start"] + end,
                "chunk_text": evidence, "text_sha256": text_hash(evidence), "strategy": strategy,
                "is_final": False, "official_match": "unverified"}
    if any(chunk.get(key) != value for key, value in expected.items()):
        raise ValueError("Chunk source text/version/offset mismatch")
    key = [identity, packet["doc_id"], packet["derived_version_id"], region["region_id"], start, end]
    if chunk["chunk_id"] != text_hash(canonical_json(key)):
        raise ValueError("Chunk ID mismatch")
    if chunk["retrieval_text"] != chunk["context_prefix"] + evidence:
        raise ValueError("Retrieval context/evidence mismatch")
    actual = count(tokenizer, chunk["retrieval_text"])
    if actual != chunk["token_count"] or actual > config["max_tokens"]:
        raise ValueError("Chunk token count/budget mismatch")


def validate_chunks(input_dir, output_dir, tokenizer, tokenizer_signature):
    from itertools import zip_longest
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    parent, manifest = read_json(input_dir / "manifest.json"), read_json(output_dir / "manifest.json")
    identity = manifest["identity"]
    if parent["state"] != "complete" or manifest["state"] != "complete":
        raise ValueError("Completed inputs and chunks required; resume the corresponding build first")
    if identity["parent_signature"] != parent["signature"] or identity["tokenizer"] != tokenizer_signature:
        raise ValueError("Wrong input snapshot or tokenizer")
    documents = chunks = empty = short = unmapped = overlap_chars = 0
    question_regions = split_question_regions = table_regions = 0
    languages = {}
    for pair in zip_longest(batches(input_dir, parent["signature"]), batches(output_dir, manifest["signature"])):
        left, right = pair
        if left is None or right is None:
            raise ValueError("Input/output batch count mismatch")
        for packet, row in zip_longest(read_rows(left[0]), read_rows(right[0])):
            if packet is None or row is None or any(packet[key] != row[key] for key in ("doc_id", "derived_version_id", "availability")):
                raise ValueError("Input/output document accounting mismatch")
            regions = {r["region_id"]: r for r in packet["regions"]}
            language = languages.setdefault(packet.get("language_hint") or "unknown",
                {"documents": 0, "chunks": 0, "retrieval_tokens": 0, "short_chunks": 0, "max_tokens": 0})
            language["documents"] += 1
            seen, coverage = set(), {key: [] for key in regions}
            for chunk in row["chunks"]:
                region = regions.get(chunk["region_id"])
                if region is None or chunk["chunk_id"] in seen:
                    raise ValueError("Orphan or duplicate chunk")
                verify_chunk(chunk, packet, region, tokenizer, identity["config"], manifest["signature"], identity["strategy"])
                seen.add(chunk["chunk_id"])
                coverage[region["region_id"]].append((chunk["region_start"], chunk["region_end"]))
                short += chunk["token_count"] < identity["config"]["min_tokens"]
                language["chunks"] += 1
                language["retrieval_tokens"] += chunk["token_count"]
                language["short_chunks"] += chunk["token_count"] < identity["config"]["min_tokens"]
                language["max_tokens"] = max(language["max_tokens"], chunk["token_count"])
                chunks += 1
            for key, region in regions.items():
                from .chunking import QUESTION
                explicit_question = bool(QUESTION.match(region["text"].lstrip()))
                question_regions += explicit_question
                split_question_regions += explicit_question and len(coverage[key]) > 1
                table_regions += any(unit["kind"] == "tr" for unit in region["units"])
                cursor = 0
                for start, end in sorted(coverage[key]):
                    if region["text"][cursor:start].strip():
                        raise ValueError(f"Uncovered source text in {key}")
                    overlap_chars += max(0, min(cursor, end) - start)
                    cursor = max(cursor, end)
                if region["text"][cursor:].strip():
                    raise ValueError(f"Missing tail in {key}")
            documents += 1
            unmapped += packet["unmapped_blocks"]
            empty += not row["chunks"]
    if documents != manifest["documents"] or documents != parent["documents"] or chunks != manifest["chunks"]:
        raise ValueError("Manifest totals do not match committed rows")
    report = {"validation": "passed", "documents": documents, "chunks": chunks,
              "documents_without_chunks": empty, "short_chunks": short,
              "by_language_hint": languages, "overlapping_source_characters": overlap_chars,
              "explicit_question_regions": question_regions,
              "question_regions_with_multiple_chunks": split_question_regions,
              "table_regions": table_regions,
              "header_semantics": "legacy tr has no reliable header labels; first-row context only",
              "unmapped_legacy_blocks": unmapped, "exact_prepared_source_slices_verified": True,
              "all_retrieval_token_budgets_verified": True, "stage4_source": parent["identity"]["source"],
              "human_qa": "pending", "selection_is_final": False, "ready_for_retrieval": False,
              "gpu_required_for_validation": False}
    write_json(output_dir / "VALIDATION.json", report)
    return report
