# S07 lexical query completion — 2026-10-29

The owner resumed lexical queries and authorized execution/optimization after
the earlier S06–S08 closeout deferred that workload. Both methods now have
**1,200 complete, technically validated query results**. The original sealed
5,512,624-chunk index and frozen S07/S08 modules remain unchanged.

| Output | Queries | Candidates | Validation |
| --- | ---: | ---: | --- |
| BM25 | 1,200 | 600,000 | Every signed query and emitted source mapping checked |
| TF-IDF | 1,200 | 600,000 | Every signed query and emitted source mapping checked |

Canonical paths and reproduction commands are in the
[S07 runbook](../../docs/LEXICAL_RETRIEVAL_STAGE7.md).
[RESULTS.json](RESULTS.json) retains hashes, scopes, detailed measurements and
validation reports. This is technical completion, without relevance/F2 claims.

## Measured work

| Run | Wall time | Peak RSS, KiB | Scope |
| --- | --- | ---: | --- |
| Compact projection | 2:31.17 | 2298604 | Read 404,849,605 postings once; retain all 234,349,921 postings for 2,819 known query terms |
| Pause/resume pilot | 0:40.98 | 2116988 | First four queries, both methods |
| Full query continuation | 4:40.94 | 5351472 | Remaining 1,196 queries, both methods, four threads; includes input seal checks |
| BM25 output validation | 1:03.97 | 288260 | Full query set and all candidate mappings |
| TF-IDF output validation | 1:03.97 | 287800 | Full query set and all candidate mappings |

All five processes exited successfully and reported zero swaps. These are
local CPU observations, not guaranteed throughput. The full continuation time
excludes projection creation and the four-query pilot; their times are shown
separately. The projection uses about 2.2 GiB of disk. Weight-cache size bounds
only derived weights and is not a process-RSS cap.

## Verification evidence

- [New fixture suite](tests_compact_search.log): **7 passed**, including original
  SQL score/rank parity, native/Python array parity, ties/unmatched terms,
  threaded equivalence, automatic resume and S09 input binding.
- [Real-index SQL comparisons](sql_reference_check.json): 48 low-DF cases,
  identical ranked IDs and maximum absolute score discrepancy
  1.11e-16. These are checks of
  scorer implementation on actual corpus statistics, not gold relevance tests.
- [Worker benchmark](parallel_benchmark.json): the same twelve queries returned
  identical candidates with 1/2/4 threads. Four threads were fastest in this
  indicative run; cache states differ, so the timing ratios do not isolate
  parallel speedup.
- [Projection build](projection_build.log), [query pilot](query_pilot.log),
  [full continuation](full_query_run.log), [BM25 validation](validate_bm25.log)
  and [TF-IDF validation](validate_tfidf.log) retain raw logs/time statistics.
- [S09 input binding](stage9_input_binding.json): current default BM25 and dense
  branches pass query/source identity checks. Full S09 preparation, bundles,
  GPU reranking and final selection have not been executed here.

The compact reader preserves all matching postings and original global scoring
statistics. It avoids repeated secondary-index/primary-row joins and adds bounded
memory maps, shared weight reuse and bounded NumPy query threading. It does not
remove frequent terms or substitute approximate lexical search.

## Handoff

S09 now defaults to **BM25 + BGE-M3 dense via RRF**; TF-IDF remains a control.
Continue with `python -m vibiomir.stage9_pipeline prepare` and the
[S09 runbook](../../docs/RETRIEVAL_STAGE9.md). Completed S07 files are reused;
there is no need to rerun lexical queries or embeddings to start that stage.
Clinical review, comparative quality evaluation and tuning remain at S10;
official submission gates remain at S11. Earlier closeout reports retaining
S07 deferral describe the earlier decision and are historical evidence.
