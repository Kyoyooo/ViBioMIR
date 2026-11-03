# S09 expanded rerank pool on Colab Pro

Open `colab/stage9_expand_pool_colab.ipynb` in a fresh CUDA runtime. The notebook
contains its support module, so uploading the notebook alone is sufficient for
code. Data and completed baseline worker outputs are required separately.

## Experiment

The original RRF pool of 200 becomes 400 (or 300 if selected before a new run).
The corpus, fusion weights, pinned reranker and window policy stay unchanged.
Final selection is 120 documents, 120 chunks, six chunks/document and IoU 0.8.
This is a new run; keep the submitted 0.1626 ZIP as fallback. No relevance gain
is established until measured under comparable evaluation conditions.

## Required Drive layout

```text
AIGuru/
  stage9_rerank_colab.zip
  stage9_rerank_workers/
    stage9_worker_0.zip
    stage9_worker_1.zip
  Data.zip
  artifacts/
    chunks_stage5_full_structure/
    candidates_stage7_bm25/
    candidates_stage8_bge/
```

Complete extracted worker directories also work. `artifacts.zip` can supply
stores instead of the extracted `artifacts/` directory, provided it actually
contains the full current stores with manifests, validation files and payloads.
Old Stage-5-only archives cannot supply S07/S08 candidates. The notebook selects
the required archive subdirectories and copies them to local Colab disk.
Official query/links Parquet can be extracted selectively from `Data.zip`.

For an extracted chunk store in another directory, set `CHUNK_STORE_OVERRIDE`
to its `Path` before running preparation. The notebook compares its completed
snapshot (signature, manifest/validation hashes and totals) with the frozen S09
source before copying. A directory named `semantic_parallel_run01/merged` is
usable only if this check passes: 1,712 batches and a validation file alone do
not establish that it is the same structural corpus. The override does not
change the candidate-store paths or bypass source validation.

Preparation verifies candidate identities and scans structural source payloads
on CPU to obtain evidence for new candidates. The frozen old bundle contains
only the old pool and cannot substitute for these source stores.

## Run and resume

1. Set `ROOT` and input paths. Keep `RUN_MISSING_RERANK=False`: the baseline
   workers must already be complete for this comparison.
2. Keep `RERANK_TOP_K=400` and `LIMITS=120/120/6/0.8`. Choose a different
   `RUN_DRIVE` for any experiment with different settings or inputs.
3. Run setup, official checks and old-worker verification. Missing inputs stop
   with an error before any new GPU work.
4. Run preparation. `expanded_prepared.zip` is backed up on Drive. `REUSE.json`
   reports old score reuse and new candidate-pair counts. `POOL_DIVERSITY.json`
   compares unique documents in old and expanded pools. The old candidate list
   must be an exact prefix of the expanded list.
5. Run the five-query GPU pilot. Inspect elapsed time and remaining work; the
   first five queries may not represent the full workload. Set
   `STOP_AFTER_QUERIES=None` in the setup variables (or a separate cell), then
   rerun the GPU cell. Do not rerun the entire setup cell in the same runtime.
6. Run final validation/export only when the new worker is complete. Download
   `submission.zip` and submit manually. Keep `COMPARISON.json` and `REPORT.json`.

On a disconnected runtime, rerun from the top with the same settings. Completed
prepared packets are restored from the signed bundle, old scores are reseeded,
and new completed query files are restored from Drive. An interrupted query may
need recomputation; the local SQLite cache is not backed up mid-query. Never
run concurrent writers against the same `RUN_DRIVE`.

New run files are under `AIGuru/stage9_expand_400_120_120_v1/` by default.
The final ZIP contains only one root-level JSON. Full source verification,
official-ID eligibility and exact query coverage precede packaging.

## Score reuse and evaluation

`scripts/stage9_expand_support.py` checks old worker signatures, packet hashes,
model registry, corpus/query/code identity and exact window policy. Cache keys
bind query text, retrieval passage text and policy. Conflicting values fail;
chunk IDs alone never authorize reuse. The original production source files and
manifests are not rewritten.

`COMPARISON.json` measures selected-document additions/removals and chunk counts
against the old pool using the same output policy. These are behavior changes,
not precision/recall/F2 estimates. No reviewed labels or production artifact
stores are present in the local checkout for an independent relevance score.

Offline Linux integration checks use synthetic evidence and a fake scorer. They
exercise verified cache import, new-pair-only scoring, interruption/resume,
tamper rejection, original-source validation and single-JSON ZIP creation.
Actual Colab inference speed, VRAM and competition quality remain unmeasured.

```bash
python -m unittest discover -s tests -p test_stage9_expand.py -v
```
