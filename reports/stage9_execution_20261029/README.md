# S09 CPU execution and GPU handoff — 2026-10-29

**Historical report:** this records the CPU handoff before the owner's GPU
completion. Both workers and final source validation subsequently completed;
the [2026-10-30 baseline record](../baseline_submission_20261030/README.md)
contains the reranked result snapshot and owner-reported Dashboard scores.
The original measurements and pending-at-the-time statements below are retained.

The owner authorized S09 execution and chose to run the GPU notebook themselves.
The assistant completed the local CPU processing, tests, baseline validation and
portable bundle. **Neural GPU reranking has not executed in this closeout.**
The [runbook](../../docs/RETRIEVAL_STAGE9.md) is the canonical operating guide;
[RESULTS.json](RESULTS.json) contains observed counts, hashes and measurements.

## Completed processing

- Both validated input branches retained: BM25 and BGE-M3 dense, each with
  1,200 queries / 600,000 candidates and the same structural source snapshot.
- Equal-family RRF, k=60; all 1,200 original queries preserved; top 200/query.
- 240,000 query–chunk pairs, 179,244 unique evidence chunks, resolved from
  1,695 stage-5 source batches. Exact mapping, text hashes and spans checked.
- A real one-batch checkpoint was resumed automatically to completion.
- Hybrid baseline contains 24,000 document predictions and 42,051 chunk
  predictions over all queries, with no empty queries. These are prediction
  counts, not counts of medically relevant evidence.
- Full baseline validation passed: 37,626 distinct selected original chunks
  reread from stage-5 source and all prediction selections reproduced.
- GPU assignment is 600 queries / 120,000 candidate pairs per worker, IDs 0/1.
  The same prepared ZIP serves both workers.

## Verification

[The dedicated suite](tests_stage9.log) ran **22 tests, all passed**. It covers
fusion, source identity, selection/deduplication, original query preservation,
complete multilingual pair windows, cache reuse, simulated CUDA OOM, automatic
resume, portable bundles, worker ZIP import and the single-command finish path.
Local real fast-tokenizer checks passed without downloading model weights.
GPU logits in these fixtures are synthetic; they are not production results.

[Bundle verification](bundle_validation.json) checked every one of 1,209 payload
hashes and imported the bundled package in an isolated temporary directory,
then read its frozen plan and a real prepared query packet. The verified ZIP is
238,769,633 bytes (about 227.7 MiB). [BUNDLE.json](BUNDLE.json) retains payload
checksums. [Notebook checks](notebook_validation.json) verify five Python cells
parse and are unexecuted with empty outputs.

[Frozen-source checks](source_preservation.json) confirm all modules bound by
the S08 plan remain unchanged. S07 retrieval results and existing embeddings
were reused. No index, embedding or corpus rebuild was launched.

## Measured local work

| Operation | Wall time | Peak RSS, KiB |
| --- | --- | ---: |
| Offline tests | 0:01.74 | 370,400 |
| Real-source checkpoint pilot | 0:14.14 | 400,404 |
| Full preparation continuation | 3:09.61 | 484,264 |
| Hybrid baseline export | 0:18.11 | 460,840 |
| GPU bundle creation | 0:18.13 | 32,980 |
| Full selected-source validation | 1:50.12 | 485,992 |

Each process exited 0 and reported zero process swaps. Full preparation excludes
the separate one-batch pilot. Export and bundle creation ran concurrently;
their times must not be added to infer elapsed wall time. These are local CPU
observations, without GPU throughput or relevance claims. Raw time statistics
are in the corresponding `.log` files.

## Existing artifacts

| Local path | Logical file sizes |
| --- | ---: |
| `artifacts/retrieval_stage9_hybrid_rrf` | 1,214.7 MiB |
| `artifacts/results_stage9_hybrid_baseline` | 283.3 MiB |
| `artifacts/stage9_rerank_colab.zip` | 227.7 MiB |

The baseline `predictions.json` is immediately readable. Its `review/` files
include original chunks, source spans, branch scores and selection decisions.
Large stores/results/ZIPs stay outside Git; review snapshots are under
[the run registry](../run_registry/README.md).

## Owner-run GPU handoff

1. Upload the existing `artifacts/stage9_rerank_colab.zip` to
   `MyDrive/ViBioMIR/stage9_rerank_colab.zip`.
2. Open [colab/stage9_rerank.ipynb](../../colab/stage9_rerank.ipynb) in two CUDA
   sessions, using `WORKER_ID=0` and `1`. Use one query as the notebook pilot,
   then set `STOP_AFTER_QUERIES=None` and rerun the worker cell.
3. The final cell validates and writes one ZIP per worker under the Drive
   `ViBioMIR/stage9_rerank_workers/` directory. Download both ZIPs unchanged
   to local `artifacts/stage9_rerank_workers/`.
4. Run `python -m vibiomir.stage9_pipeline finish` using `.venv-stage5`.
   It restores workers, exports reranked predictions and validates the selected
   original evidence. This final real-GPU completion step remains pending.

The pinned public reranker, automatic VRAM presets, backups and resume details
are in the runbook. S10 retains clinical QA, gold relevance metrics, hybrid and
reranker comparisons, calibration and tuning. S11 retains official document-ID
eligibility and final submission packaging. The existing CPU baseline is a
technically verified experimental result, without clinical/final approval.
