# Full-audit processing decisions

Derived from the completed automatic audit. Source examples and assistant review are reported separately.

| Metric | Value |
| --- | --- |
| records | 1105160 |
| distinct_doc_ids | 855745 |
| doc_ids_with_text | 852995 |
| doc_ids_without_text | 2750 |
| doc_ids_with_multiple_records | 248909 |
| doc_ids_with_conflicting_normalized_text | 28480 |
| doc_ids_with_text_and_matching_blocks | 852995 |
| rows_with_nonempty_html | 78739 |
| total_blocks | 34658787 |

## Priority order

1. Build versioned documents and keep official IDs, aliases, source hashes and row locators.
2. Choose among retry/content versions using structure and reviewed quality; do not keep the longest blindly.
3. Extract only metadata supported by existing columns/blocks/text or retained HTML; leave unknown fields null.
4. Pilot domain-specific UI/template removal on original block spans with before/after regression.
5. Preserve medical lists, repeated dosing instructions, negation, tables and Q&A; lexical noise markers are insufficient for deletion.
6. Use validated headings/paragraphs for baseline structural chunks; retain fixed-window fallback and source offsets.
7. Freeze a QA-approved working subset, then build lexical/dense indexes. No recrawl prerequisite.

The automatic candidate count is not a final count of clinically valid or relevant documents.
Do not infer exact embedding-token budgets from character or whitespace counts.
