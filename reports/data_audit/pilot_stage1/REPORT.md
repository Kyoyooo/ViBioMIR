# ViBioMIR S00 audit report

Generated: 2026-10-24T16:51:54.039183+00:00

**PARTIAL CORPUS AUDIT**

**Human review: pending.** Dispositions, language hints, and processing proposals are provisional.

| Metric | Value |
| --- | --- |
| run_state | finished |
| scope | selected_shards_only |
| discovered_corpus_files | 1195 |
| selected_corpus_files | 7 |
| selected_scope_complete | True |
| expected_selected_rows | 6040 |
| records_read | 6040 |
| distinct_doc_ids | 6040 |
| conflicting_doc_ids | 0 |
| last_invocation_seconds | 1.244 |
| process_peak_rss_mib | 169.33 |

## Dispositions

| Disposition | Records |
| --- | --- |
| needs_cleaning | 2435 |
| needs_reextraction | 48 |
| needs_review | 74 |
| unusable_currently | 1712 |
| usable_as_is | 1771 |

## Quality flags

| Flag | Records |
| --- | --- |
| empty_text | 1760 |
| html_entities | 6 |
| html_in_text | 1 |
| missing_text_column | 40 |
| missing_title | 1790 |
| non_2xx_status | 1672 |
| possible_block_page | 32 |
| possible_title_body_mismatch | 10 |
| possible_ui_text | 1749 |
| repeated_characters | 23 |
| repeated_lines | 1727 |
| replacement_character | 19 |
| short_text | 105 |

## Full-text language hints

| Hint (not confirmed language) | Records |
| --- | --- |
| cjk_likely_zh | 1979 |
| mixed_script | 21 |
| unknown | 1760 |
| vi_diacritics | 2280 |

## Selected file states

| Shard | State | Expected rows | Error |
| --- | --- | --- | --- |
| ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714570674-056.parquet | complete | 1000 | None |
| ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714909666-041.parquet | complete | 1000 | None |
| ViBioMIR/shards/A/vov.vn/part-1792716842933-706.parquet | complete | 1000 | None |
| ViBioMIR/shards/B/120ask.com/part-1792685726879-367.parquet | complete | 1000 | None |
| ViBioMIR/shards/B/vinmec.com/part-1792716882112-410.parquet | complete | 1000 | None |
| ViBioMIR/shards/C/cnkang.com/part-1792716526532-236.parquet | complete | 1000 | None |
| ViBioMIR/shards/C/pubmed.ncbi.nlm.nih.gov/part-1792716468718-977.parquet | complete | 40 | None |

## Review and next delivery

The domain-stratified random sample has 60 records, with probabilities and weights.
Targeted flag examples are separate and must not be used to estimate error rates.
Review source locators in review_samples.jsonl and fill review_decisions.csv (prior annotations preserved on resume).
Run the full audit in delivery 2 before confirming processing_plan.md or enabling proposed_rules.yaml.

## Artifacts

- [Input manifest](input_manifest.json), [scan status](scan_status.json), [record metrics](record_audit.parquet).
- [Domain quality](domain_quality.csv), [metadata profile](metadata_profile.csv), [structure profile](structure_profile.csv).
- [Duplicate versions](duplicate_versions.parquet), [duplicate content](duplicate_content.parquet), [template candidates](repeated_block_candidates.csv).
- [Review samples](review_samples.jsonl), [review decisions](review_decisions.csv), [draft plan](processing_plan.md), [draft rules](proposed_rules.yaml).

## Limitations

- Automatic dispositions and language/type hints require human confirmation.
- Source SQLite databases are cataloged, never opened; archives are not expanded or double-counted.
- Hashes cover selected corpus files; other files have metadata-only inventory.
- Row count limits are not byte limits; a very large single row can exceed the normal RAM target.
- HTMLParser is tolerant; parsed markup is not proof of complete/valid HTML or correct main content.
- Template counts cover short block/line candidates, not all-pairs semantic near-duplicate detection.
- Random review sampling is stratified by domain; language/type/disposition coverage must be reviewed in stage 2.
- No sources were downloaded, cleaned, re-extracted, embedded, or indexed.
