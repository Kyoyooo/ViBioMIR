"""Stage-4 contracts: regions are evidence; normalized views are not submissions."""
import pyarrow as pa

VERSION_SCHEMA = pa.schema([
    ("derived_version_id", pa.string()), ("doc_id", pa.int64()), ("parent_version_id", pa.string()),
    ("representative_record_id", pa.string()), ("source_url", pa.string()), ("extraction_method", pa.string()),
    ("availability", pa.string()), ("content_candidate", pa.bool_()), ("source_chars", pa.int64()),
    ("kept_chars", pa.int64()), ("removed_chars", pa.int64()), ("quarantined_chars", pa.int64()),
    ("region_count", pa.int64()), ("transformation_count", pa.int64()), ("normalized_sha256", pa.string()),
    ("metadata_json", pa.large_string()), ("language_hint", pa.string()), ("doc_type_hint", pa.string()),
    ("diagnostics_json", pa.large_string()), ("quality_flags_json", pa.string()), ("source_fidelity", pa.string()),
    ("content_scope", pa.string()), ("rulebook_sha256", pa.string()), ("mode", pa.string()),
    ("is_final", pa.bool_()), ("needs_review", pa.bool_()), ("official_match", pa.string())])
REGION_SCHEMA = pa.schema([
    ("region_id", pa.string()), ("derived_version_id", pa.string()), ("parent_version_id", pa.string()),
    ("doc_id", pa.int64()), ("ordinal", pa.int64()), ("source_kind", pa.string()), ("kind", pa.string()),
    ("source_locator_json", pa.string()), ("source_start", pa.int64()), ("source_end", pa.int64()),
    ("text", pa.large_string()), ("normalized_text", pa.large_string()), ("text_sha256", pa.string())])
TRANSFORMATION_SCHEMA = pa.schema([
    ("transformation_id", pa.string()), ("derived_version_id", pa.string()), ("parent_version_id", pa.string()),
    ("doc_id", pa.int64()), ("ordinal", pa.int64()), ("source_kind", pa.string()),
    ("source_locator_json", pa.string()), ("source_start", pa.int64()), ("source_end", pa.int64()),
    ("text", pa.large_string()), ("rule_id", pa.string()), ("rule_revision", pa.int64()),
    ("decision", pa.string()), ("reason_json", pa.string())])
SELECTION_SCHEMA = pa.schema([
    ("doc_id", pa.int64()), ("selected_derived_version_id", pa.string()), ("parent_version_count", pa.int64()),
    ("stage3_selected_parent_id", pa.string()), ("selected_parent_id", pa.string()), ("selection_changed", pa.bool_()),
    ("availability", pa.string()), ("reason_json", pa.string()), ("is_final", pa.bool_()), ("needs_review", pa.bool_())])
TABLES = {"cleaning_versions": VERSION_SCHEMA, "evidence_regions": REGION_SCHEMA,
          "transformations": TRANSFORMATION_SCHEMA, "document_selection": SELECTION_SCHEMA}
