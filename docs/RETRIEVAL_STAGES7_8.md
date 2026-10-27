# S07 local CPU and S08 on six Colab GPUs

Delivery: 2026-10-27. Code, fixtures and an unexecuted notebook are supplied.
**The assistant has not run new tests, indexing, packaging, downloads, embedding
or retrieval jobs.** The owner manually runs every command. Comparisons,
clinical review, ICD calibration and ANN quality measurement remain deferred.

## Shared inputs and parallel execution

Both branches default to `artifacts/chunks_stage5_full_structure/`: 5,512,624
validated chunks across 855,745 document ledger rows and 1,712 source batches.
The two pipelines share a source fingerprint and deterministic global row IDs,
so S09 can fuse the same chunk IDs. The semantic chunk store remains available
for a later separate experiment. Do not mix structural lexical results with a
different dense corpus and call it the controlled baseline.

S07 and S08 read this immutable input, use separate output directories and can
run concurrently. They do not require semantic ICD or any stage-5 QA catalog,
full fixed build, new cleaning or recrawl. ICD is not embedded into
`retrieval_text`, and no ICD filter/boost is enabled. This is an experimental
snapshot, with final QA/readiness flags still pending.

| Work | Location | Blocking dependency |
| --- | --- | --- |
| S07 inverted index, global statistics and TF-IDF norms | Local CPU/SSD | Existing validated chunks |
| S08 plan and six bundles | Local CPU/SSD | Same chunks and pinned tokenizer |
| S08 embedding | Six independent Colab CUDA sessions | Matching bundle per worker |
| S08 merge and vector index | One local machine or one Colab CPU | All worker outputs complete |
| S09 fusion/reranking | Later delivery | Compatible S07/S08 candidates |

## 1. Local environment and fixtures

```bash
source .venv-stage5/bin/activate
python -m pip install -r requirements-stage7.txt -r requirements-stage8.txt
python -m unittest discover -s tests -p 'test_stage78*.py' -v
```

Fixtures use tiny synthetic corpora and a fake encoder; no model/GPU/download
is needed by the tests. Real analyzer and CPU tensor pooling fixtures run when
their optional packages are installed. Passing fixtures is not full-data quality
acceptance. Run from the project root. S07 global `--config` goes before its
subcommand; S08 `--config` follows `plan`/`bundle`.

## 2. Start S07 on local CPU

Inspect `configs/lexical_stage7.json`, then:

```bash
python -m vibiomir.lexical_stage7 build --stop-after-batches 1
python -m vibiomir.lexical_stage7 build
python -m vibiomir.lexical_stage7 validate
```

The first call is an optional transaction/resume smoke check. Repeat the second
after interruption. The builder first commits postings per source batch, then
freezes **global** document frequency, chunk count and average length before
computing cosine TF-IDF norms. Norms checkpoint per source batch. A paused
postings build is not a searchable complete index. Input checksums are rechecked
on resume; already committed postings/norms are reused.

Output: `artifacts/lexical_stage7_structure/`, containing `index.sqlite3`,
manifest and technical validation. BM25 and TF-IDF are two distinct scorers
over one physical disk-backed inverted store; duplicating the same postings is
unnecessary. The implementation follows the spec's BM25 (`k1=1.2`, `b=.75`,
unique query terms) and log-TF/smoothed-IDF cosine formulas. All source batches
use the same global statistics; there are no independently scored language shards.

The default analyzer uses [PyVi 0.1.1](https://pypi.org/project/pyvi/0.1.1/) for
Vietnamese segmentation, Unicode word tokens for English/other non-Han tokens,
and [jieba](https://github.com/fxsjy/jieba) search segmentation with HMM disabled
for Han blocks. It preserves diacritics, negation and numeric tokens in the
search view. Analyzer package/resource hashes are bound to the index; missing
packages or a changed analyzer do not silently trigger a fallback. The
`unicode_reference_v1` analyzer exists for fixtures/explicit alternate runs.
No query translation or cross-language synonym expansion is claimed by BM25.

SQLite uses a 256 MiB page cache and disk-backed temporary aggregation. Keep
the database on a local SSD, not mounted Drive/network storage. The postings,
term dictionary, mapping and temporary files can be much larger than the 2.58
GiB compressed chunk store: plan for tens of GiB and check actual growth after
the pilot. There is no measured full-build duration or final index size yet.
One batch is retained in memory; no corpus-sized Python postings dictionary is
created. CPU work can remain lengthy even without a model.

Default tokenization uses two CPU processes with bounded prefetch; the parent
alone writes SQLite transactions. Set config `workers` to 1–8 according to CPU/RAM.
Workers, SQLite cache and default output top-k can change without rebuilding
the unchanged postings. Input/analyzer/scoring changes still require a new run.

Candidate generation, when needed for S09 (no relevance scoring is run):

```bash
python -m vibiomir.lexical_stage7 search \
  --queries Data/official/ViBioMIR/query.parquet --method bm25 \
  --output-dir artifacts/candidates_stage7_bm25

python -m vibiomir.lexical_stage7 search \
  --queries Data/official/ViBioMIR/query.parquet --method tfidf \
  --output-dir artifacts/candidates_stage7_tfidf
```

Default top-k is 500, controlled by config or `--top-k`. Each query writes an
atomic, signed candidate JSON; reruns reuse matching completed query files.
The index must be built and technically validated first.

## 3. Prepare one immutable S08 plan and six bundles

Inspect `configs/dense_stage8.json`. Defaults: BGE-M3, six workers and the same
structural source. The existing registered stage-5 tokenizer is reused.

```bash
python -m vibiomir.dense_stage8 plan
python -m vibiomir.dense_stage8 bundle \
  --queries Data/official/ViBioMIR/query.parquet
```

The plan freezes source/validation hashes, tokenizer/model revision, encoder
policy, batch-to-worker assignment, expected global row intervals and code
hashes. Largest source batches are assigned first using chunk count as a cost
proxy. No new corpus tokenization or GPU inference is required to plan/package.
Each source batch belongs to exactly one worker; all chunks are covered.

For mixed GPUs, optional `worker_weights` can assign more work to faster known
GPUs before creating the plan, e.g. `[3,1,1,1,1,1]`. These are relative workload
weights, not speed guarantees. Default equal weights require no throughput
measurement. A changed assignment/model/input requires a new `run_dir` and plan.
Do not edit an already distributed `plan.json`. Same-plan bundle reruns reuse
matching ZIPs; changed source code/notebook/query payloads are rejected rather
than silently reusing an outdated bundle.

Upload this layout to Drive:

```text
MyDrive/ViBioMIR/dense_stage8_bge_structure/
  bundles/
    worker_0.zip
    worker_1.zip
    worker_2.zip
    worker_3.zip
    worker_4.zip
    worker_5.zip
```

Generated ZIPs are under `artifacts/dense_stage8_bge_structure/bundles/`.
Upload only each worker's matching ZIP to its session through Drive. Each ZIP
contains its source batches, shared plan, tokenizer, code/tests and optional
queries. Its `chunks/` directory is a **partial portable input**, not a standalone
complete stage-5 store; use it only through the frozen worker plan. Model
weights, raw Data, stage-3/4 SQLite stores, ICD labels and S07 index are omitted.
All six ZIPs together carry the chunk payload once, rather than six full copies.

## 4. Run six GPU sessions concurrently

Open [stage8_dense_worker.ipynb](../colab/stage8_dense_worker.ipynb) in six
sessions and set a distinct `WORKER_ID` from 0 to 5. Use the same `DRIVE_RUN`
and keep the notebook's worker-specific local/backup paths. Six independently
allocated CUDA sessions are the revised user infrastructure target; no cross-VM
DDP, shared VRAM or account/session automation is implemented. One GPU can also
process the six logical workers sequentially if fewer sessions are available.

| Starting setting | T4 | L4 | A100 |
| --- | ---: | ---: | ---: |
| `BATCH_SIZE` | 16 | 32 | 64 |
| `PADDED_TOKENS` | 8,192 | 16,384 | 32,768 |
| `WAVE_CHUNKS` | 128 | 128 | 256 |

Run setup, fixtures/preflight and the one-batch encode pilot. Then set
`STOP_AFTER_BATCHES=None` and rerun the encode cell. Model inference uses one
FP16 CUDA model per worker, length buckets and a padded-token budget. CUDA OOM
splits/retries the same windows; a single-window OOM stops explicitly without
truncating or dropping evidence. Adjust microbatch/wave settings per device;
each invocation records them. Settings are starting points, not benchmarked
utilization or throughput claims. TPU is not implemented in this delivery.

Every source batch commits `vectors.npy`, `mapping.jsonl.gz` and signed
`part.json` by atomic local rename, then backs it up to that worker's Drive
folder. Source/mapping/vector hashes are verified on reuse. On disconnect,
remount the same Drive, restore the same bundle and rerun the same worker ID.
Only unfinished batches re-encode; completed local work ahead of Drive is copied
before backup is marked complete. The model registry is also retained inside
nonempty signed batches so an interrupted registry copy can be recovered.
Never run two writers for the same worker/backup. Different GPU hardware can
produce small floating-point differences for newly encoded rows; this is not a
bitwise-equivalence claim.

Run each worker's validation cell when complete. It checks every vector's
shape/dtype/finite values/normalization, exact chunk/text/version mapping and
parameter/revision registry. Worker 0 can also run the query cell once; queries
are inference inputs, not training/calibration data. It saves `query_vectors/`
to Drive. None of these cells computes relevance metrics or selects settings.

## 5. Merge after all workers finish

Either enable `MERGE_ALL=True` on **one** notebook session or download all worker
folders for local merging. The central notebook cell restores all source batches
from the six bundles, copies worker outputs to local disk, runs full merge
validation, and copies the merged result to Drive. Other GPU sessions can stop.
It checks local disk space first; Drive is backup storage, not a SQLite workdir.

For local merging, restore:

```text
artifacts/dense_stage8_bge_structure/
  plan.json
  worker_0/ ... worker_5/   # complete worker directories downloaded from Drive
  query_vectors/          # worker 0 query output, if generated
```

```bash
python -m vibiomir.dense_stage8 merge \
  --plan artifacts/dense_stage8_bge_structure/plan.json \
  --chunk-dir artifacts/chunks_stage5_full_structure \
  --workers-dir artifacts/dense_stage8_bge_structure \
  --output-dir artifacts/dense_stage8_bge_structure/merged
```

The merger requires all six logical worker outputs, rejects mixed plans/model
families, verifies full mapping/vector/source coverage and enforces unique chunk
IDs/global row IDs in a local SQLite map. It supports restart of copied/committed
batches. It writes `merged/VALIDATION.json`; success is technical validation,
not retrieval quality acceptance.

Planning size for BGE vectors alone: 5,512,624 × 1,024 × 2 bytes ≈ **10.5 GiB**,
plus mapping, metadata and model caches. Worker copies plus the merged copy
duplicate that vector payload; budget at least twice the vector size plus
source, SQLite and temporary space for merge. These are calculated budgets,
not measured artifact sizes. The notebook uses a conservative larger free-disk
check. Drive capacity does not increase Colab's local disk/RAM.

## 6. Build the dense search index on CPU

Once merge is complete, run on a machine with the merged store locally:

```bash
python -m vibiomir.dense_index build \
  --vector-dir artifacts/dense_stage8_bge_structure/merged \
  --output-dir artifacts/dense_stage8_bge_structure/index_ivfpq
```

The default [FAISS IVF-PQ](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)
uses nlist 4096, m 64, nbits 8, nprobe 32; small datasets reduce centroid/bit
counts explicitly in the manifest. Training samples up to 200,000 corpus
vectors with seeded language-hint strata. True language and domain labels are
not available here: this is not a claim of clinically verified language/domain
stratification. No query vectors or relevance labels train the index. The
sample alone is about 0.76 GiB in float32 for BGE; FAISS needs additional RAM.
Vectors are added in bounded blocks rather than loading the full float32 corpus.

Index building checkpoints every 100 source batches; rerun the identical command
after interruption. Training or additions since the last checkpoint repeat.
The previous committed checkpoint is removed only after its replacement and
manifest are durable at the file level. No measured ANN recall is claimed.
The spec's recall@200 quality target remains a later audit, not automatically
satisfied by successfully writing an IVF-PQ file.

Correct but slower disk-exact fallback, also useful for small fixtures/pilots:

```bash
python -m vibiomir.dense_index build \
  --vector-dir artifacts/dense_stage8_bge_structure/merged \
  --output-dir artifacts/dense_stage8_bge_structure/index_exact --kind exact
```

Exact search streams float16 files in blocks and normalizes/scores in float32.
It avoids a full-RAM Flat index but scans the corpus for each query. It is not
recommended as an unmeasured fast full-query workload on a small local machine.

With worker 0's query vectors restored, generate candidates for S09:

```bash
python -m vibiomir.dense_index search \
  --vector-dir artifacts/dense_stage8_bge_structure/merged \
  --index-dir artifacts/dense_stage8_bge_structure/index_ivfpq \
  --query-dir artifacts/dense_stage8_bge_structure/query_vectors \
  --output-dir artifacts/candidates_stage8_bge
```

## Model, pooling and version contracts

BGE-M3 uses its [pinned public revision](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181),
CLS + L2 dense pooling, original `retrieval_text`, no added query instruction,
and no ICD prefix. Vector storage is float16; aggregation/scoring uses float32.
Each chunk's retrieval-text hash is retained, so later metadata-only ICD changes
do not invalidate vectors. Text/context/tokenizer/model/pooling changes require
a new plan and encoding run for the affected representation.

The optional mBERT control is implemented with masked mean pooling excluding
special/padding tokens at [revision 3f076fd](https://huggingface.co/google-bert/bert-base-multilingual-cased/commit/3f076fdb1ab68d5b2880cb87a0886f315b8146f8).
To activate later, copy dense config, change `model` to `mbert`, select a new
`run_dir` and tokenizer directory, then first run:

```bash
python -m vibiomir.dense_stage8 download-tokenizer \
  --model mbert --output-dir artifacts/tokenizers/mbert_stage8
```

Run `plan --config YOUR_CONFIG.json` and `bundle --config YOUR_CONFIG.json` for
that separate experiment. A plan never mixes model families across workers.
Long queries and alternate-tokenizer passages use complete overlapping token
windows. Each window is pooled/L2-normalized; their token-count-weighted mean
is L2-normalized again. Overlap tokens count in each window, an explicit
engineering policy rather than whole-document token-mean equivalence. No
content tokens are silently truncated; mapped window boundaries are tested
and window counts stored. Inputs above 200,000 characters fail explicitly.
Real model parameter counts must be <=15B. No model fine-tuning is launched.

## Outputs and later acceptance

Both retrievers write one signed `query-ID.json` containing stable chunk ID,
document/version ID, source batch/row/chunk order, retrieval-text hash and score.
The dense mapping also includes mapped window count. Their run manifests retain
the common chunk-source identity. These are **candidate artifacts for S09**,
not competition submission JSON and not local F2 evaluation reports. BM25,
TF-IDF, cosine and PQ scores must not be directly summed without S09 fusion.
Dense exact ties use stable global row IDs; lexical ties use stable chunk IDs.

Keep QA/clinical approval and final corpus freeze pending. Domain-stratified
ANN training, full ANN-vs-exact recovery checks, model comparisons, relevance
metrics, clinical review and threshold tuning remain later acceptance work.
No new corpus quality score, ANN recall, index size or runtime was measured by
this delivery. Spec A11/A12/A17 require owner-run evidence before acceptance.

GitHub source list: [stage78_source_files.txt](../sharing/stage78_source_files.txt).
Drive/upload plan: [drive_stage78_planned.txt](../sharing/drive_stage78_planned.txt).
No Git commit or push is executed by this delivery.
