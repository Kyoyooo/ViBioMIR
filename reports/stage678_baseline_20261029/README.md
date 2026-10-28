# S06–S08 baseline execution evidence — 2026-10-29

The confirmed closeout scope is rules ICD, a verified BM25/TF-IDF index and
BGE-M3 dense retrieval. Full lexical query generation is deferred by the
owner. Read the [baseline handoff](../../docs/STAGES6_8_BASELINE_HANDOFF.md)
for canonical data paths, the S09 entry point and remaining S10 gates.

[RESULTS.json](RESULTS.json) records counts, checksums, source revision hashes,
timings, cleanup and test scope. No relevance or clinical scores are claimed.

| Evidence | What was actually checked/executed |
| --- | --- |
| [S06 snapshot](icd_snapshot.json) | Full existing validation reused; code/configuration and all input/output batch seals verified |
| [S07 snapshot](lexical_snapshot.json) | Full existing validation reused; sealed database, analyzer, source and completed index/norm ledger verified |
| [Query validation](query_validation.log) | All 1,200 query vectors, reference text/IDs, windows and model registry |
| [Index build](dense_build.log) | Full CPU IVF-PQ build; time/RSS recorded by `/usr/bin/time -v` |
| [Index validation](dense_validation.log) | Complete unique FAISS ID coverage and source/index seals |
| [Candidate generation](dense_search.log) | 1,200 queries, 500 dense candidates each |
| [Candidate validation](candidate_validation.log) | All 600,000 candidates checked against the original mapping |
| [S06 tests](tests_stage6.log) | 14 passed |
| [S07/S08 tests](tests_stage78.log) | 25 passed, 1 skipped (optional mBERT CPU pooling requires PyTorch) |

Dated control metadata is copied in the [run registry](../run_registry/README.md).
Original `VALIDATION.json` files remain with the artifact stores. The registry
copies are review evidence and must not be used as resume checkpoints.
