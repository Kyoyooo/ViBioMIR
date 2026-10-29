# Run metadata for team review

These are small copies observed through 2026-10-30, not runnable checkpoints.
[index.json](index.json) records original source directories and observation
time; newer entries have an explicit `observed_on` date. Original artifacts and all their data parts belong on Drive. A registry
copy must never replace an original manifest or be used to resume a run.

| Snapshot | Observed outcome | Review metadata |
| --- | --- | --- |
| `stage3_full_v2` | Completed provisional export; validation passed | [Manifest](stage3_full_v2/run_manifest.json), [validation](stage3_full_v2/VALIDATION.json) |
| `stage4_full_apply` | Completed provisional apply; validation passed | [Report](stage4_full_apply/REPORT.md), [validation](stage4_full_apply/VALIDATION.json) |
| `stage4_full_dry` | Completed dry-run; validation passed | [Report](stage4_full_dry/REPORT.md), [validation](stage4_full_dry/VALIDATION.json) |
| `stage4_reviewed_pilot_dry` | Completed reviewed pilot; validation passed | [Validation](stage4_reviewed_pilot_dry/VALIDATION.json) |
| `stage4_pilot_dry` | Completed 15-document dry-run; no standalone validation file present | [Manifest](stage4_pilot_dry/run_manifest.json) |
| `stage5_inputs_full` | Prepared all 855,745 documents, 1,712 batches | [Manifest](stage5_inputs_full/manifest.json) |
| `stage5_inputs_smoke` | Prepared 200 documents, 4 batches | [Manifest](stage5_inputs_smoke/manifest.json) |
| `stage5_smoke_fixed` | 200 documents / 765 chunks; validation passed | [Validation](stage5_smoke_fixed/VALIDATION.json) |
| `stage5_smoke_structure` | 200 documents / 687 chunks; validation passed | [Validation](stage5_smoke_structure/VALIDATION.json) |
| `stage5_full_structure_observed` | **Building when observed**; preserved as active work | [Observed manifest](stage5_full_structure_observed/manifest.json) |
| `stage5_full_structure_20261026` | Full local validation passed: 855,745 documents / 5,512,624 chunks | [Manifest](stage5_full_structure_20261026/manifest.json), [validation](stage5_full_structure_20261026/VALIDATION.json) |
| `stage5_full_semantic` | Full local and Colab validation passed: 855,745 documents / 9,036,279 chunks; reports agree | [Manifest](stage5_full_semantic/manifest.json), [validation](stage5_full_semantic/VALIDATION.json) |
| `stage5_semantic_shard_0` | Complete: 863 batches; provenance backup, not a full-corpus output | [Manifest](stage5_semantic_shard_0/manifest.json) |
| `stage5_semantic_shard_1` | Complete: 849 batches; provenance backup, not a full-corpus output | [Manifest](stage5_semantic_shard_1/manifest.json) |
| `stage5_tokenizer` | Local tokenizer provenance and file checksums | [Registry](stage5_tokenizer/REGISTRY.json) |
| `stage6_rules_20261029` | 855,745 rules annotations; existing full validation and snapshot seals verified | [Manifest](stage6_rules_20261029/manifest.json), [validation](stage6_rules_20261029/VALIDATION.json) |
| `stage7_structure_20261029` | 5,512,624 chunks / 737,967 terms; index verified, full queries deferred | [Manifest](stage7_structure_20261029/manifest.json), [validation](stage7_structure_20261029/VALIDATION.json) |
| `stage8_merged_20261029` | 5,512,624 BGE-M3 vectors; complete mapping and payload validation | [Manifest](stage8_merged_20261029/manifest.json), [validation](stage8_merged_20261029/VALIDATION.json) |
| `stage8_queries_20261029` | All 1,200 query vectors validated | [Manifest](stage8_queries_20261029/manifest.json), [validation](stage8_queries_20261029/VALIDATION.json) |
| `stage8_index_20261029` | Full IVF-PQ index; all unique vector IDs and seals verified | [Manifest](stage8_index_20261029/manifest.json), [validation](stage8_index_20261029/VALIDATION.json) |
| `stage8_candidates_20261029` | 1,200 queries × 500 dense candidates; every mapping checked | [Manifest](stage8_candidates_20261029/manifest.json), [validation](stage8_candidates_20261029/VALIDATION.json) |
| `stage7_projection_20261029` | Query-bound compact postings; 2,819 terms / 234,349,921 postings; validation passed | [Manifest](stage7_projection_20261029/manifest.json), [validation](stage7_projection_20261029/VALIDATION.json) |
| `stage7_bm25_candidates_20261029` | Full BM25 retrieval: 1,200 queries / 600,000 mapped candidates; validation passed | [Manifest](stage7_bm25_candidates_20261029/manifest.json), [validation](stage7_bm25_candidates_20261029/VALIDATION.json) |
| `stage7_tfidf_candidates_20261029` | Full TF-IDF retrieval: 1,200 queries / 600,000 mapped candidates; validation passed | [Manifest](stage7_tfidf_candidates_20261029/manifest.json), [validation](stage7_tfidf_candidates_20261029/VALIDATION.json) |
| `stage9_prepared_20261029` | All 1,200 queries / 240,000 candidate pairs resolved; two GPU workers prepared | [Manifest](stage9_prepared_20261029/manifest.json) |
| `stage9_hybrid_baseline_20261029` | All 1,200 baseline results / 42,051 chunks; original-evidence validation passed | [Manifest](stage9_hybrid_baseline_20261029/manifest.json), [validation](stage9_hybrid_baseline_20261029/VALIDATION.json) |
| `stage9_hybrid_reranked_20261030` | All 1,200 reranked results / 41,086 chunks; original-evidence validation passed; later Dashboard scores recorded separately | [Manifest](stage9_hybrid_reranked_20261030/manifest.json), [validation](stage9_hybrid_reranked_20261030/VALIDATION.json) |

`chunks: 0` in a `prepare-chunk-inputs` manifest means that preparation has
finished before chunk generation starts; it does not mean the source is empty.

Human/clinical QA remains pending. Exported IDs were subsequently checked against
the saved official allowlist; [the later score record](../baseline_submission_20261030/README.md)
preserves the owner-reported Dashboard metrics without rewriting historical flags. Technical
validation does not imply a final corpus freeze or measured retrieval quality.
Historical building snapshots remain unchanged. Later dated snapshots record
completed validation separately; they do not retroactively change earlier
observations. Current S06–S08 technical evidence does not imply clinical or
ANN quality acceptance.
