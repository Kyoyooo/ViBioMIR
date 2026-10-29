# S09 — candidate fusion, reranking and original-evidence results

Execution update: **2026-10-30**. Both owner-run Colab rerank workers and local
finish/source validation are complete. The result contains 1,200 queries,
24,000 document predictions and 41,086 chunks at
`artifacts/results_stage9_hybrid_reranked/predictions.json`. The owner reported
Dashboard score **0.0916** for the provisional submission; see the
[frozen baseline record](../reports/baseline_submission_20261030/README.md)
for all metrics, hashes, limitations and proposed optimizations.

The [earlier CPU execution record](../reports/stage9_execution_20261029/README.md)
retains its historical counts, runtime and handoff evidence. Commands below
are reproduction/resume instructions. Existing completed preparation, lexical
retrieval, embeddings and reranking do not need to run again.

After running this stage you can read a ranked document list and original
passages for every supplied query. This is retrieval, without generated medical
answers. The default is the existing **1,200 official queries**, not an online
service accepting arbitrary new questions. New questions require their own
S08 query encoding/candidate run and an S09 configuration bound to that set.

## Current route and compute

The default is **hybrid BM25 + dense BGE-M3 via RRF**. It consumes complete,
validated `artifacts/candidates_stage7_bm25/` and
`artifacts/candidates_stage8_bge/`, each with up to 500 candidates per query
over the same structural source. Follow the [S07 runbook](LEXICAL_RETRIEVAL_STAGE7.md)
for lexical completion evidence. S09 itself never launches lexical search or
re-embeds the corpus. TF-IDF remains available as a separate baseline.
The existing S06 rules are retained as metadata; ICD filters/boosts stay off.

| Operation | Compute | Output |
| --- | --- | --- |
| Resolve candidate evidence and prepare packets | Local CPU/SSD | `artifacts/retrieval_stage9_hybrid_rrf/` |
| Export a fused hybrid baseline immediately | Local CPU | `artifacts/results_stage9_hybrid_baseline/` |
| Package the bounded rerank pool | Local CPU | `artifacts/stage9_rerank_colab.zip` |
| Rerank the same candidates | Colab CUDA, default two independent workers | Drive `stage9_rerank_workers/worker_0/`, `worker_1/` |
| Export and validate reranked results | Local CPU/SSD | `artifacts/results_stage9_hybrid_reranked/` |

The prepared full run contains **1,200 queries × 200 chunks = 240,000
query–chunk pairs**, using 179,244 distinct original chunks from 1,695 source
batches. Each of the two workers receives 600 queries / 120,000 pairs, before
long-input windowing/cache reuse. The GPU model is
the public multilingual [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3),
pinned to [revision 953dc6f](https://huggingface.co/BAAI/bge-reranker-v2-m3/commit/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e)
from 2024-06-24, preceding the competition cutoff. Actual parameter count,
revision, FP16, software versions and GPU are recorded at inference; the code
enforces the 15B limit. No closed model or text generation is used. The model
card documents the Transformers sequence-classification interface used here.

## 1. CPU environment and offline tests

Run from the repository root. The existing `.venv-stage5` environment already
contains the CPU packages used by the earlier stages. For another environment,
install [requirements-stage9.txt](../requirements-stage9.txt).

```bash
source .venv-stage5/bin/activate
python -m unittest discover -s tests -p 'test_stage9*.py' -v
```

The 22 executed test cases use synthetic source chunks, synthetic score functions and
local fast tokenizers. They test fusion math, constant/missing scores, family
weights, source identity, caps/overlap, negation/age/dose preservation, complete
pair windows, cache keys/reuse, simulated OOM, pause/restore, empty predictions,
portable bundles and corruption rejection. No GPU, weights, network or full
corpus scan is needed. A passing fixture suite will not establish relevance
quality or clinical correctness. All 22 passed locally. The GPU inference path was exercised with
synthetic logits and simulated OOM; real neural inference was subsequently completed by the owner on Colab. The
actual registered local BGE tokenizer also passed multilingual pair-window
coverage checks offline.

## 2. Prepare original evidence once on CPU

Review [configs/retrieval_stage9.json](../configs/retrieval_stage9.json), then:

```bash
python -m vibiomir.stage9_pipeline prepare
```

Default prerequisites are the complete validated structural store and complete
validated S07 BM25 and S08 dense candidate directories at their existing paths. Preparation verifies
the common source identity, official query-file hash and candidate-file
signatures, performs deterministic fusion, and keeps at most 200 candidates
per query. Only required source batches are read. Every requested chunk is
matched by its document/version, batch/row/order, retrieval-text hash and exact
evidence span. Source text is never reconstructed from tokenizer decoding.

Each needed batch is extracted into a small-purpose SQLite evidence cache with
a committed ledger. No BM25 postings or dense vectors are loaded. Memory holds
candidate descriptors and bounded source records, with a 64 MiB SQLite cache;
it does not hold all corpus text. Observed disk usage, runtime and peak process memory are recorded in the
execution report. Keep several GiB free and use local SSD for preparation.

For a short extraction/resume check, optionally use:

```bash
python -m vibiomir.stage9_pipeline prepare --stop-after-batches 1
python -m vibiomir.stage9_pipeline prepare
```

The first command intentionally reports `paused`; repeat the second command
to finish. Resume is automatic: do not add `--resume` and do not change config.
Committed evidence batches are reused after checksum checks. Packet generation
can repeat after an interruption; source extraction checkpoints are retained.
Source/code/settings changes require a new output directory, rather than
editing manifest hashes or approving a separate configuration file.

For a separate bounded pilot (not a full-query output):

```bash
python -m vibiomir.stage9_pipeline prepare \
  --output-dir artifacts/retrieval_stage9_pilot --limit-queries 10
```

Later commands must explicitly use `--prepared-dir artifacts/retrieval_stage9_pilot`
and separate pilot result/ZIP paths. Never submit a pilot as the full result.

## 3. Get readable baseline results immediately, without GPU

```bash
python -m vibiomir.stage9_pipeline export \
  --prepared-dir artifacts/retrieval_stage9_hybrid_rrf \
  --output-dir artifacts/results_stage9_hybrid_baseline --mode baseline

python -m vibiomir.stage9_pipeline validate \
  --prepared-dir artifacts/retrieval_stage9_hybrid_rrf \
  --output-dir artifacts/results_stage9_hybrid_baseline

python -m vibiomir.stage9_pipeline show \
  --output-dir artifacts/results_stage9_hybrid_baseline --query-id 1
```

`baseline` uses fused rank scores without a reranker. With the default two
branches, RRF combines the BM25 and dense rank lists using equal family weights
and stable chunk-ID ties. It selects from the bounded fused pool, up to 200
chunks per query. A separate single-dense-branch configuration is still supported. The file `predictions.json` contains all input IDs, integer document IDs,
and `relevant_docs` / `relevant_chunks`; empty lists are retained. Files under
`review/` include the question, original text, source offsets, branch/fusion
scores, document scores and every selection/rejection reason. `show` displays
one of these existing results; it does not issue a new search.

This is an inspectable baseline, without a claim that it outperforms reranking
or satisfies the official submission gates. It is useful while GPU work runs.

## 4. Package and run the GPU branch

The default uses **two** logical workers; they can run concurrently on two
Colabs or sequentially on one. If you prefer 1–6 workers, set `workers` in the
config **before preparation** and choose a new prepared directory if a plan
already exists. Query assignment is deterministic by input position modulo
worker count. A changed worker assignment cannot resume an old run.

```bash
python -m vibiomir.stage9_pipeline bundle \
  --prepared-dir artifacts/retrieval_stage9_hybrid_rrf \
  --output artifacts/stage9_rerank_colab.zip
```

Upload just these two items for this GPU workload:

- `artifacts/stage9_rerank_colab.zip` to Drive, for example
  `MyDrive/ViBioMIR/stage9_rerank_colab.zip`.
- [colab/stage9_rerank.ipynb](../colab/stage9_rerank.ipynb), opened in each Colab.

The ZIP carries source, standalone GPU requirements, the frozen prepared
manifest and compressed query packets. It excludes the raw Data folder,
stage-3/4 databases, full chunk store, evidence SQLite cache, embeddings,
FAISS index, model weights and S07 index. The same ZIP works for every worker;
do not regenerate the S08 bundles. Model weights download at the pinned public
revision when the GPU worker first starts.

1. Select a CUDA GPU runtime. Set `WORKER_ID=0` in the first notebook and `1`
   in the second; keep `BUNDLE` and `DRIVE_WORKERS` consistent.
2. Run setup and real fast-tokenizer pair preflight. The notebook checks archive
   hashes, local disk, assigned queries and CUDA before model inference.
3. Run the worker with `STOP_AFTER_QUERIES=1` as a pilot.
4. Set `STOP_AFTER_QUERIES=None` and rerun the worker cell to finish.
5. Run the final cell. It validates the worker and writes one
   `stage9_worker_ID.zip` directly under the Drive `stage9_rerank_workers/`
   folder. Each completed query and the manifest are also backed up separately.

| GPU | Initial `BATCH_SIZE` | Initial `PADDED_TOKENS` |
| --- | ---: | ---: |
| T4, about 15–16 GB | 8 | 8,192 |
| L4 | 16 | 16,384 |
| A100, about 40 GB | 32 | 32,768 |
| A100, about 80 GB | 64 | 65,536 |

Leave `BATCH_SIZE=None` and `PADDED_TOKENS=None` for these automatic VRAM
presets, or set explicit integer overrides before preflight. These are
conservative starting settings, not measured throughput or memory claims.
Increase only after observing the pilot. GPU microbatches are length-bucketed and
bounded by padded tokens. CUDA OOM splits/retries without dropping candidates;
if one complete window cannot fit, execution stops with an explicit message.
CPU token features are prepared in bounded waves of
`min(128, max(16, 2 * BATCH_SIZE))` passages. This permits larger GPU batches
without keeping all query pairs in host memory. Extra host RAM is not a reason to load the full corpus.
No TPU, cross-machine DDP or account/session automation is used.

Resume the same notebook/ZIP/worker ID and backup path after disconnect. Signed
completed query files are restored and reused; only an unfinished query needs
reprocessing if its local SQLite score cache was lost. The local cache saves
model/revision/policy/query/passage-specific scores while the runtime survives.
GPU batch budgets can change without changing the logical scoring policy.
Newly computed scores may differ slightly across FP16 GPU hardware. Errors stream to the
cell and a timestamped log, rather than only an outer `CalledProcessError`.
Never run two sessions writing the same worker folder.

## 5. Return GPU results and finish locally

Download the two ZIPs generated by the final notebook cell to:

```text
artifacts/stage9_rerank_workers/
  stage9_worker_0.zip
  stage9_worker_1.zip
```

Keep these exact filenames and run from the local repository:

```bash
source .venv-stage5/bin/activate
python -m vibiomir.stage9_pipeline finish
```

This checks the full ZIP file set and checksums, restores each worker atomically
into `worker_0/` or `worker_1/`, validates all assigned query scores, exports
`artifacts/results_stage9_hybrid_reranked/predictions.json` and performs full
selected-evidence validation. The command is repeatable: identical restored
workers are reused. A missing worker or conflicting existing folder stops with
an actionable message and preserves the existing files. No model is loaded.

Downloading complete worker directories instead remains supported. Preserve
`worker_ID/manifest.json` and all `query-*.json` under the workers root; cache
SQLite, model weights and logs are unnecessary for final export. For a custom
layout, use `finish --prepared-dir ... --workers-dir ... --output-dir ...`.
Separate `export --mode reranked` and `validate` subcommands are also available.

Read a completed result with:

```bash
python -m vibiomir.stage9_pipeline show \
  --output-dir artifacts/results_stage9_hybrid_reranked --query-id 1
```

All configured workers must be complete. Export rejects missing/extra scored
candidates, mismatched packets/models, changed worker inputs and nonfinite
scores. Validation recomputes selection, checks complete query coverage and
re-reads the selected chunks from their original stage-5 batches. It does not
load a GPU model or run relevance evaluation. Always validate before treating
a generated output as a verified snapshot.

## Fusion, query and selection contracts

The CPU implementation supports both RRF and min–max weighted fusion for later
hybrid integration. Configure actual completed/validated candidate directories
in `branches`; missing branches are not silently created or ignored. Dense
and lexical families receive weights `1-alpha` and `alpha` when both exist;
one active family receives total weight 1. Each family's configured branches
share its weight equally, including empty lists, so supplementary facets do not
increase total family weight. Missing candidates contribute zero. Constant
lists normalize to zero. Scores are never directly added across raw methods.
All ties use stable chunk IDs. No relevance improvement is claimed solely because hybrid fusion executes.

The original query is always retained and is the rerank input. Small intent
regexes emit source-mapped signals for review only; they do not assert diseases,
classify clinical intent reliably or rewrite negation, population, age or dose.
Automatic facet splitting/translation is disabled. Optional `--facets-file`
accepts project-authored annotations such as:

```json
[{"id": 1, "facets": [{"id": "care", "start": 0, "end": 10, "text": "Cần làm gì"}]}]
```

Offsets are Python Unicode character indices and `text` must be the exact
original substring. If adding a facet retrieval branch, set its `facet_id`,
`queries` path and completed candidate directory. Its query file must have
the same ordered IDs and match that facet's mapped span for every query;
candidate validation must bind that file's hash. At most three supplementary
facets are accepted. An original-query retrieval branch must remain present.
Facet candidate generation is a separate retriever run, not silently performed
here; it is unnecessary for the default route. Reranking still uses the full
original query, retaining conditions that might be outside a facet slice.

The initial pair limit is 1,024 tokens. Query windows contain up to 384 tokens;
the remaining pair budget holds overlapping passage windows. All query and
passage tokens are covered; no implicit tokenizer truncation is permitted.
Each query window takes its maximum passage-window logit; these maxima are
averaged across query windows. Character/token spans and window counts persist
with scores. This is an explicit engineering aggregation with possible
long-input bias, to evaluate at S10. Inputs above 200,000 characters or requiring
more than 4,096 pair windows fail explicitly. Logits are not probabilities.

Document score is the maximum selected-pool chunk score, not a length-favoring
sum. Defaults: at most 20 documents, 40 chunks, four chunks per document.
Exact duplicates and IoU ≥0.8 spans are removed only within the same document
version; overlap comparisons also require the same region. Cross-document
text duplicates keep their own verified IDs. Evidence is not rewritten or
merged. Every exported chunk's document is in `relevant_docs`; a document can
remain in that list when the independent global chunk cap leaves no passage
for it. All defaults are untuned and may over-return irrelevant evidence.

## Completion boundary and sharing

The stage is operationally complete for a chosen branch after all its query
results and `VALIDATION.json` exist and pass. Both the full CPU hybrid baseline
and subsequent full neural reranked results have passed these checks.
Existing S08 outputs remain unchanged.

S10 covers clinical/gold review, F2/recall, candidate and selection losses,
intent/facet accuracy, ANN and reranker comparisons, threshold/cap tuning,
and full hybrid experiments. S11 verifies applicable official IDs, all required
queries and source provenance and produces the official submission package.
The later provisional JSON/ZIP passed local schema, query/ID and integrity
checks, and the owner supplied Dashboard scores. These observations do not
close human/clinical QA or full S10 acceptance. Original human-QA,
final-selection and readiness flags stay provisional; you
can still inspect and use these experimental retrieval results. No dashboard
upload or external publication occurs.

Source/review inventory: [stage9_source_files.txt](../sharing/stage9_source_files.txt).
Large prepared stores, workers and results remain under `artifacts/`, outside
Git. Do not remove the frozen S08 or stage-5 artifacts to make room for S09.
