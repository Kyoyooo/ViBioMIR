"""Stage 3 contracts and exact, monotonic mappings into unchanged source text."""
import json
from urllib.parse import urlsplit

import pyarrow as pa

from .io import canonical_json, text_hash

IMPORT_REVISION = "stage3-verbatim-import-v1"


def valid_doc_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63


def valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def parse_blocks(raw):
    if raw is None or raw == "":
        return [], "missing_or_empty"
    try:
        blocks = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(blocks, list) or any(not isinstance(b, list) or len(b) != 2
                or not all(isinstance(x, str) for x in b) for b in blocks):
            return [], "invalid"
        return blocks, "valid"
    except (ValueError, TypeError, RecursionError):
        return [], "invalid"


def mapped_blocks(text, blocks, doc_id=None, version_id=None):
    """No normalization or out-of-order fallback may fabricate a source slice."""
    cursor = 0
    headings = []
    for ordinal, (kind, block_text) in enumerate(blocks):
        start = text.find(block_text, cursor) if isinstance(text, str) and block_text else -1
        end = start + len(block_text) if start >= 0 else None
        if start >= 0:
            cursor = end
        if kind in {"h", "h1", "h2", "h3", "h4", "h5", "h6"}:
            if kind == "h":
                headings = [(1, block_text)]
            else:
                level = int(kind[1])
                headings = [(n, value) for n, value in headings if n < level] + [(level, block_text)]
        identity = [version_id, ordinal, kind, block_text]
        yield {"doc_id": doc_id, "version_id": version_id, "block_id": text_hash(canonical_json(identity)),
               "ordinal": ordinal, "kind": kind, "block_text": block_text,
               "start": start if start >= 0 else None, "end": end,
               "mapping_state": "exact" if start >= 0 else "empty" if not block_text else "unmapped_exact",
               "heading_path_json": canonical_json([value for _, value in headings]),
               "source_locator_json": canonical_json({"column": "blocks", "index": ordinal})}


VERSION_SCHEMA = pa.schema([
    ("version_id", pa.string()), ("doc_id", pa.int64()), ("source_url", pa.string()),
    ("final_url", pa.string()), ("evidence_text", pa.large_string()), ("text_state", pa.string()),
    ("text_sha256", pa.string()), ("normalized_sha256", pa.string()),
    ("blocks_raw_json", pa.large_string()), ("blocks_state", pa.string()),
    ("html_available", pa.bool_()), ("html_sha256", pa.string()),
    ("metadata_json", pa.large_string()), ("source_metadata_json", pa.large_string()),
    ("source_fidelity", pa.string()), ("content_scope", pa.string()), ("import_revision", pa.string()),
    ("n_blocks", pa.int64()), ("n_exact_blocks", pa.int64()), ("quality_flags_json", pa.string()),
    ("language_hint", pa.string()), ("doc_type_hint", pa.string()), ("official_match", pa.string()),
    ("input_record_count", pa.int64()), ("representative_record_id", pa.string())])
INPUT_SCHEMA = pa.schema([
    ("record_id", pa.string()), ("doc_id", pa.int64()), ("raw_doc_id_json", pa.string()),
    ("version_id", pa.string()), ("source_shard", pa.string()), ("source_sha256", pa.string()),
    ("row_group", pa.int64()), ("row_offset", pa.int64()), ("source_url", pa.string()),
    ("status", pa.string()), ("crawl_metadata_json", pa.large_string()),
    ("audit_flags_json", pa.string()), ("audit_disposition", pa.string()), ("import_state", pa.string())])
BLOCK_SCHEMA = pa.schema([
    ("doc_id", pa.int64()), ("version_id", pa.string()), ("block_id", pa.string()),
    ("ordinal", pa.int64()), ("kind", pa.string()), ("block_text", pa.large_string()),
    ("start", pa.int64()), ("end", pa.int64()), ("mapping_state", pa.string()),
    ("heading_path_json", pa.string()), ("source_locator_json", pa.string())])
SELECTION_SCHEMA = pa.schema([
    ("doc_id", pa.int64()), ("selected_version_id", pa.string()), ("version_count", pa.int64()),
    ("normalized_text_versions", pa.int64()), ("availability", pa.string()),
    ("selection_status", pa.string()), ("is_final", pa.bool_()), ("needs_review", pa.bool_()),
    ("reason_json", pa.string()), ("official_match", pa.string())])
GROUP_SCHEMA = pa.schema([("normalized_sha256", pa.string()), ("version_count", pa.int64()), ("doc_count", pa.int64())])
ALIAS_SCHEMA = pa.schema([("normalized_sha256", pa.string()), ("doc_id", pa.int64()), ("version_id", pa.string()),
                          ("text_sha256", pa.string()), ("group_kind", pa.string())])
REVIEW_SCHEMA = pa.schema([("doc_id", pa.int64()), ("version_id", pa.string()),
    ("decision", pa.string()), ("rank_json", pa.string()), ("reason_json", pa.string()), ("is_final", pa.bool_())])
