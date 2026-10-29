# Submitted baseline and optimization starting point

The owner reported these Dashboard results on **2026-10-30 (Asia/Ho_Chi_Minh)**.
The submission ID, upload time and public/private phase were not supplied.
These are owner-reported competition scores, not locally reproduced metrics.
[DASHBOARD_RESULT.json](DASHBOARD_RESULT.json) preserves the numbers and binds
the local submission, source results and configuration by SHA-256.

| Metric | Reported value |
| --- | ---: |
| FINAL_SCORE | 0.0916 |
| DOCS_F2MACRO | 0.1323 |
| CHUNKS_F2MACRO | 0.0509 |
| DOCS_PRECISION | 0.3155 |
| DOCS_RECALL | 0.1280 |
| CHUNKS_PRECISION | 0.0871 |
| CHUNKS_RECALL | 0.0543 |

Do not recompute macro F2 from macro precision and recall: F2 is calculated per
query before averaging. The mean of the two reported F2 values is 0.0916; this
arithmetic agreement alone does not establish the organizer's scoring formula.
The scored phase may contain fewer questions than the submitted 1,200.

## Frozen baseline

- Source: 855,745 prepared documents; 5,512,624 structural chunks. Fixed and
  semantic stores exist separately and are not used by this submission.
- S06: rules ICD metadata; no ICD filtering, score boost or labels injected
  into embedding text.
- S07/S08: BM25 and BGE-M3, 500 candidates per branch per query. TF-IDF exists
  as a separate baseline, outside the submitted hybrid.
- S09: equal-family RRF (`k=60`), top 200/query, then pinned
  `BAAI/bge-reranker-v2-m3` on two owner-run Colab workers. Query facets and
  translation are off. Complete query/passage windows use the frozen policy.
- Selection: at most 20 documents, 40 chunks, four chunks/document; same-source
  overlap IoU threshold 0.8. The full output contains 24,000 document predictions
  and 41,086 chunks: 20 documents and approximately 34.24 chunks/query on average.
- Results: `artifacts/results_stage9_hybrid_reranked/predictions.json`.
- Submitted package: `artifacts/submissions/stage9_hybrid_reranked_provisional/`
  containing `submission.json` and `submission.zip`. The ZIP has exactly one
  root-level JSON, byte-identical to the validated S09 predictions.
- [Configuration](../../configs/retrieval_stage9.json),
  [reranked metadata snapshot](../run_registry/stage9_hybrid_reranked_20261030/manifest.json)
  and [source validation](../run_registry/stage9_hybrid_reranked_20261030/VALIDATION.json).

Local checks reconfirm query coverage, integer ID eligibility against the saved
official allowlist, schema, ZIP contents and the previous source-validation
seals. No new full-corpus validation, retrieval or GPU job was needed. A passed
Dashboard submission does not establish human/clinical acceptance. Original
manifests retain their historical quality/readiness fields; this dated report
adds the later observation without rewriting those manifests.

## What the scores support

Recall is low at both levels, and chunk F2 is below document F2. Improving
evidence retrieval/selection is a priority. Aggregate scores cannot locate
the loss: usable evidence may be absent from the indexed corpus, outside a
retriever's candidates, removed by the 200-item fused pool, demoted by the
reranker, or omitted by output caps/chunk boundaries. A 0.0543 macro chunk recall
does not mean 94.57% of all reference chunks were missed globally.

The existing 30-question/60-document S10 silver pilot is a separate research
experiment. Its proxy scores are not comparable to these Dashboard metrics.
It did not tune this submission. Human-reviewed qrels, clinical QA, the
300-document ICD audit and production ANN quality acceptance remain pending.

## Next experiments, ordered by diagnostic value and cost

These are hypotheses to test, not implemented improvements or promised gains.
Use reviewed development labels for selection; retain an untouched holdout.
Limit Dashboard comparisons to planned experiments under the daily quota.

| Order | Question and bounded experiment | Reuse / compute | Evidence needed |
| --- | --- | --- | --- |
| 1 | Where does relevant evidence disappear? For reviewed queries, measure eligible/usable source coverage, per-branch recall@500, union recall, fused recall@200, post-rerank coverage and final selected coverage. Slice by source language, domain and multi-intent question. | Existing source catalog, candidate files, prepared packets and worker scores; CPU | Original source spans and judged positives; unavailable documents must be counted separately from retrieval failures. |
| 2 | Does the current selection lose recall? Compare document caps 20/40/60, chunk caps 40/80/120 and per-document caps 4/8 on the same scored pool. Compare the no-reranker RRF control using the same pool. | Existing 240,000 scored pairs; CPU in a new comparison/export directory | Separate macro document/chunk P/R/F2, output counts and paired uncertainty. Larger caps can lower precision and F2. |
| 3 | Is the dense approximation losing candidates? Compare current IVF-PQ `nprobe=32` with 64/128 and a bounded query set searched exactly against the same full vectors. Measure ANN recall@k separately from relevance recall. | Existing vectors/index; streamed exact search on CPU or GPU if needed; no embedding rebuild | Same vectors, query IDs and k; latency/RAM and exact-neighbor recall. The tiny S10 IVF-Flat probe cannot validate this production IVF-PQ index. |
| 4 | Are usable multilingual sources missing? Audit official IDs against documents with nonempty indexed chunks; review English/PubMed and Chinese sources explicitly. Check extraction loss and cross-language duplicates before authorizing recovery. | Catalog and mapping audit on CPU | Verified language/source samples and evidence availability. Language hints alone cannot establish English coverage. Recrawling remains a separately authorized task. |
| 5 | Does fusion/reranking discard useful documents? Compare family weights, document diversity and a larger rerank pool (200 vs 400/800) only after the candidate-loss audit. | RRF comparison can reuse candidates on CPU; unseen rerank pairs require a new prepared run and Colab GPU | Candidate recall before/after each cut, reranker control, unchanged corpus and query set. |
| 6 | Do chunks miss reference context? Inspect original evidence versus structural boundaries, headings/tables and exact adjacent source spans. Then test fixed/semantic or source-context export on a bounded reviewed set. | Existing three chunk stores; new retrieval embeddings/indexes need a distinct run if their input changes | Source fidelity, chunk match/coverage, length and precision; never synthesize or concatenate unrelated evidence. |
| 7 | Do multi-intent facets or ICD help after coverage is sound? Keep the full original query, verify mapped facet spans and compare optional soft boosts without hard filters. | Bounded new query encoding/reranking may use GPU; existing document embeddings remain reusable if their text/model stays identical | Condition/negation preservation, per-intent recall and an ablation against this frozen baseline. |

Changing output caps alone does not require corpus embedding. Changing the
encoder, chunk boundaries or `retrieval_text` requires versioned affected
embeddings/indexes. Existing workers are bound to their prepared configuration;
do not edit manifests or settings in place to reuse them under a new identity.
Offline score reuse needs a separately bound comparison/export implementation.

For every future run record its parent baseline, single intended change,
configuration/code/data hashes, label version, phase, metrics, candidate/output
counts, runtime/RAM/VRAM and changed artifact paths. Never treat unjudged items
as confirmed negatives or use silver results as human-reviewed acceptance.

## Closeout evidence

[TESTS.json](TESTS.json) records the fixture suites rerun for this baseline.
[LOCAL_VERIFICATION.json](LOCAL_VERIFICATION.json) records the artifact and
frozen-source checks. [CLEANUP.json](CLEANUP.json) lists the narrowly scoped
cleanup and retained checkpoint dependencies. Large data, models and submission
payloads remain outside Git. No push or Dashboard action is performed here.
