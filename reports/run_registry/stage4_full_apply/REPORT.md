# Stage 4 offline cleaning

State: `complete_provisional`; mode: `apply`; scope: `all_parent_documents`.

CPU local processing. Parent stage-3 artifacts and raw sources are read-only. No recrawl, GPU, indexing or generated medical evidence.
All selections remain provisional; independent main-content/clinical QA is pending.

| Metric | Value |
| --- | ---: |
| parent_versions_processed | 913969 |
| documents_complete | 855745 |
| documents_failed | 0 |
| kept_source_chars | 2799731328 |
| removed_source_chars | 7535268 |
| quarantined_source_chars | 2284456 |

Inspect `processing_summary.json` and `review_samples.jsonl` after successful export.
Dry-run evidence regions are proposed outputs, not approved production evidence.
Each evidence region maps to one contiguous original source slice. Never concatenate across removed spans and claim a continuous original chunk.
Metadata is inherited unchanged; descriptions do not become abstracts and missing authors/dates remain unknown.
Review configuration rule status and run tests manually before an apply run in a separate output directory.
