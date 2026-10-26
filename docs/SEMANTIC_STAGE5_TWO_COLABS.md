# Semantic stage 5 on two Colab GPUs

This delivery splits the remaining semantic input into two disjoint intervals,
uses one GPU per Colab, and merges the completed outputs into the ordinary
stage-5 chunk store. **The assistant has not run tests, packaging, corpus jobs or
GPU jobs.** All commands and notebook cells are for manual execution.

## Execution layout

```text
Completed prepared input (shared, immutable)
                  |
       One shared partition plan
       /                       \
Colab A: shard 0          Colab B: shard 1
CPU preparation          CPU preparation
one CUDA encoder         one CUDA encoder
own local checkpoint     own local checkpoint
own Drive backup         own Drive backup
       \                       /
    Optional old semantic prefix + merge
                  |
      Original full validate-chunks
```

Both Colabs must see the **same physical shared Drive folder**, not merely
directories with the same names in different accounts. Share the folder and
make it accessible in both runtimes, editing paths as needed. Compare the
printed plan signature on both machines. Only A creates the plan; B reads it.
Each worker writes only its own `shard_0` or `shard_1` directory. Use independently
available resources; this implementation does not coordinate accounts or bypass
resource allocations.

## What is optimized

- CPU preparers run as separate spawned processes. They discover the original
  chunker's exact sentence/atom requests before GPU inference. At most twice
  the CPU-worker count is queued ahead; there is no corpus-sized task queue.
- GPU calls combine requests from multiple documents/sections. A bounded window
  is sorted by token length, padded only to its longest member, and split by
  maximum batch size **and padded-token cost**.
- The encoder remains the pinned BGE-M3, FP16, CLS + L2 and inference-only. No
  evidence truncation, synthetic text or different semantic threshold is added.
- CUDA OOM splits the current microbatch and retries, then lowers the effective
  batch size. Previously committed batches stay intact. Failure on even one atom
  stops with an actionable error rather than dropping that atom.
- Vectors are written into a temporary local disk-backed array per wave.
  Semantic replay reads only the needed vectors; only two decoded Python-float
  rows are cached at once. Output JSONL is streamed document by document.
- The original chunker performs both request discovery and final replay. Replay
  checks the exact request text/order before using a vector. This avoids a second
  implementation of evidence offsets, question context or boundary rules.

The extra CPU discovery/replay pass has a cost. Performance improvements and an
approximately twofold end-to-end speedup are **not measured or guaranteed**.
Legacy Python cosine calculations and final validation still consume CPU time.
FP16 values can differ with padding, batch shape and hardware; exact bitwise
boundaries across different runtimes are not promised. The algorithm/model
identity and source evidence rules remain unchanged, and execution details are
recorded in each shard manifest.

## Settings for approximately 51 GB system RAM / 15 GB GPU RAM

| Setting | Initial value | Meaning |
| --- | ---: | --- |
| `CPU_WORKERS` | min(2, available CPUs) | CPU-only tokenizer/request preparation; no CUDA model in workers |
| `MAX_BATCH_SIZE` | 64 | Upper bound on sentences per GPU microbatch |
| `MAX_PADDED_TOKENS` | 8192 | `batch_size × longest_token_length`, not total unpadded tokens |
| `WAVE_DOCUMENTS` | 16 | Upper bound on documents gathered into a normal wave |
| `WAVE_SENTENCES` | 1024 | Normal wave/length-sorting window budget |
| Source characters per normal wave | 1,000,000 | Additional CPU staging limit |
| Model dtype | FP16 | One model copy per GPU/runtime |

These are engineering starting points, **not a measured memory guarantee**. For
512-token atoms, the 8192 padded-token budget permits at most 16 per microbatch,
even though the batch-size cap is 64. Shorter atoms can use larger batches.

Do not attempt to fill all 51 GB RAM. One exceptionally large document may
exceed normal wave limits and is processed alone; its vectors spill to disk.
The current document and bounded CPU prefetch still require memory. Disk must
hold the extracted input, new output and scratch vectors.

Start with two new source batches on each GPU. Inspect `compute_seconds`,
`backup_seconds`, `gpu_allocated_gib`, `gpu_reserved_gib`,
`gpu_peak_allocated_gib`, `parent_peak_rss_gib` and `encoder_stats`. Parent RSS
excludes CPU workers and OS file cache. GPU statistics describe PyTorch memory,
not a guaranteed bound on all processes on the device.

If GPU memory pressure persists, use `MAX_BATCH_SIZE=32` and
`MAX_PADDED_TOKENS=4096`. If CPU RAM is high, reduce `CPU_WORKERS` to 1 or 0,
`WAVE_DOCUMENTS` to 4/8 and `WAVE_SENTENCES` to 256/512. Resume with the same
plan; these runtime settings do not change the logical input assignment.

## 1. Package once, without rerunning preparation

From the current repository on the local machine:

```bash
python scripts/make_stage5_semantic_parallel_bundle.py \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --output artifacts/stage5_semantic_parallel.zip
```

The package contains the existing full prepared input, tokenizer, code, tests,
notebooks, requirements and guides. It does not include source databases,
structural chunks, raw Data or weights. `prepare-chunk-inputs` is already done;
do not repeat it. Upload this one ZIP to a location both Colabs can read.

## 2. Preserve any semantic work already completed

Stop the old single-run semantic job and wait for it to exit. Ensure the last
completed batches are backed up using its existing notebook/checkpoint helper.
Keep that old output unchanged as a recovery copy.

On Colab A, set `LEGACY_SEED` to that **semantic** output/backup directory. If
there is no semantic output to reuse, leave it `None`. Do not point to structural
chunks. A snapshots committed prefix batches into the new shared run directory.
Where a local writer lock exists, a live writer is rejected. Drive backups have
no local lock: the owner must stop the old writer before snapshotting them.

The plan reuses the actual committed prefix, even if its old manifest still says
`building` or `paused`. If batches 0–99 are present, only batches 100 onward are
assigned to the two new shards. The old chunks are copied byte-for-byte at merge;
they are not sent to the new encoders again.

Do not append to or clean up that prefix after partitioning. Its checksums and
descriptors are bound into the plan and verified again at merge.

## 3. Configure and start both notebooks

Open [stage5_semantic_two_shards.ipynb](../colab/stage5_semantic_two_shards.ipynb)
on two separate CUDA runtimes. The same notebook supports both roles:

| Value | Colab A | Colab B |
| --- | --- | --- |
| `SHARD_ID` | `0` | `1` |
| `SHARED_ROOT` | Shared physical run folder | The same folder, possibly via a different mount path |
| `LEGACY_SEED` | Old stopped semantic output, or `None` | `None` |
| Plan creation | Once, if `plan.json` does not exist | Never |
| Local output | `semantic_shard_0` | `semantic_shard_1` |
| Drive output | `SHARED_ROOT/shard_0` | `SHARED_ROOT/shard_1` |

Run setup and the small new CPU fixture tests on both. Run A's plan cell first,
then B's plan cell. Compare the printed plan signature. Each interval is
`[start, end)`, over global input-batch indices. They cover the unfinished input
once, without overlaps/gaps. The split balances compressed input bytes as a
cheap length proxy; it does not promise equal GPU runtime or equal document count.

Start the run cells on both machines. They initially pause after two new source
batches. Review memory/timing, set `STOP_AFTER_BATCHES=None`, and rerun each run
cell to continue. Hardware/resource differences may cause one shard to finish
earlier; no automatic work stealing or reassignment changes the frozen plan.

## 4. Resume each shard independently

For a same-runtime interruption, rerun its run cell. For a disconnected runtime,
rerun setup, checks and plan-read cells, then run. Use the same `SHARD_ID`, bundle,
plan and backup path. It restores the committed local prefix for **that shard**
and skips its completed batches. A batch interrupted before its Drive commit may
need recomputation. A completed shard returns without loading another GPU model.

Shard-local batch numbers start at zero. The log's `global_input_batch` identifies
the source batch in the original full input; do not rename directories to make
local numbers look global. Never point both jobs at the same output folder.

## 5. Merge and validate on A after both finish

Both shard manifests must say `complete`, both Drive backups must be current,
and both writers must have stopped. Run the final cell on A. It:

1. Checks plan, model/config/input identity, source checksums and complete
   nonoverlapping batch coverage, including the optional old prefix.
2. Copies gzip payloads unchanged into a normal full chunk store, adjusting only
   batch metadata from shard-local to global order. Chunk IDs/text/offsets are not
   rewritten. Merge can resume from its committed prefix if interrupted.
3. Backs up the merged output, runs the original complete `validate-chunks`, and
   backs up `VALIDATION.json` on success.

Final output: `SHARED_ROOT/merged`. Keep it with the matching prepared input and
tokenizer. Shards are partial by design; do not run the original full validator
on one shard and interpret missing full-corpus accounting as failure. Human QA
and retrieval-ready flags remain provisional after technical validation.

If merging from a separate local machine after downloading both shard outputs:

```bash
python -m vibiomir.semantic_shards merge \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --plan /path/to/shared_run/plan.json \
  --shard-0 /path/to/shared_run/shard_0 \
  --shard-1 /path/to/shared_run/shard_1 \
  --output-dir artifacts/chunks_stage5_semantic_merged
```

Add `--seed-dir /path/to/shared_run/legacy_prefix` if the plan reused old batches.
Then run `python -m vibiomir validate-chunks` with the full input, merged output
and matching tokenizer, as in the original stage-5 guide.

## Manual tests and implementation limits

```bash
python -m pip install -r requirements-stage5.txt 'numpy>=1.26,<3'
python -m unittest discover -s tests -p 'test_stage5_semantic*.py' -v
```

Fixtures use deterministic fake vectors: they check original/replayed chunk
equality, empty request handling, wave/token budgets, OOM split/index mapping,
two-part coverage, old-prefix reuse, resume, invalid plans, incomplete parts and
the ordinary full validator after merge. They do not establish CUDA correctness,
GPU memory fit or performance; the two-batch hardware pilot remains necessary.

Local fixture tests do not need Torch/CUDA. Colab installs
`requirements-stage5-semantic.txt`. An optional real GPU encoder-order test runs
only when `VIBIOMIR_RUN_CUDA_TESTS=1` and `VIBIOMIR_TOKENIZER_DIR` points to the
unpacked tokenizer; it may download the pinned weights. It uses one model copy.

Implementation references: [dynamic padding](https://huggingface.co/docs/transformers/main/pad_truncation),
[PyTorch inference mode](https://docs.pytorch.org/docs/stable/notes/autograd)
and [disk-backed arrays](https://numpy.org/doc/stable/reference/generated/numpy.memmap.html).
