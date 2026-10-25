# Stage 3 document preparation

State: `partial`; scope: `all_audited_sources`.

CPU only. Source text was imported verbatim; no cleaning, recrawling, model inference or indexing was performed.
Selections are provisional. Metadata candidates and main content still require review; this is not a final retrieval-ready corpus.

| Metric | Value |
| --- | ---: |
| input_records | 0 |
| document_versions | 0 |
| distinct_document_ids | 0 |
| invalid_id_records | 0 |

Use `python -m vibiomir validate-documents --store-dir <this-directory>` after successful export.

The SQLite file is the resume checkpoint. Keep source shards and the audit input files; retained HTML is referenced, not copied into every version.

`metadata_coverage.json` counts selected/missing/candidate/conflicting metadata per unique version.
`version_review/` retains each provisional decision, quality rank and reason. Rank order: nonempty valid URL, not blocked, fewer critical flags, exact block coverage, HTTP success, fewer weak flags, explicit metadata count.
