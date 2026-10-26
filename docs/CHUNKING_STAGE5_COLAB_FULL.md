# Full stage 5 on Colab — reuse local work and measure the remaining cost

For the newer two-GPU semantic workflow, use
[semantic stage 5 on two Colabs](SEMANTIC_STAGE5_TWO_COLABS.md). It can preserve
the stopped single-run semantic prefix and partitions only the unfinished input.

The owner requested full Colab execution on 2026-10-26 after the sequential
structural run became too slow locally. This delivery adds a portable full
bundle, an ordered CPU process runner with exact tokenizer caching, and a
notebook supporting both CPU baselines and the CUDA semantic strategy.
**The assistant has not executed tests, downloads, packaging, pipelines or GPU
jobs for this delivery.** All commands below are manual.

## What actually uses GPU

| Strategy | Compute in the supplied full notebook | Reuse existing structural batches? |
| --- | --- | --- |
| `structure` | Colab CPU processes + cached tokenizer requests | Yes, with matching original code/config/tokenizer/input |
| `fixed` | Colab CPU processes + cached tokenizer requests | No; use a separate fixed output |
| `semantic` | BGE-M3 sentence encoder on CUDA; preprocessing/tokenization still CPU | No; different chunk boundaries and output identity |

Selecting a GPU runtime does not make the current structural algorithm run on
CUDA. There is no local LLM inference to move to GPU here. To preserve the
existing experiment, keep `STRATEGY='structure'`; its acceleration comes from
parallel CPU workers and reuse of exact repeated tokenizer results. If the
Colab runtime has fewer CPU resources than the local machine, it may still be
slower. **No speedup factor has been measured or promised.**

Use `semantic` when you want the separate required semantic experiment. It
genuinely runs the public BGE-M3 encoder on CUDA, but the extra embeddings do
not guarantee a shorter total runtime than structural chunking. T4/L4/A100 and
other CUDA devices use the same encoder; TPU is not implemented. Start with one
Colab account/session and one writer for each output/backup directory.

The [Tokenizers documentation](https://huggingface.co/docs/tokenizers/index)
describes its CPU/Rust implementation; [Python process executors](https://docs.python.org/3.13/library/concurrent.futures.html#processpoolexecutor)
provide CPU parallelism. Colab resources and GPU/CPU availability are variable;
see its [official FAQ](https://research.google.com/colaboratory/faq.html).

## 1. Preserve the completed preparation; stop only the slow build

Your log already reports:

```text
operation = prepare-chunk-inputs
state = complete
documents = 855745
batches = 1712
```

**Do not repeat `prepare-chunk-inputs`.** Its `chunks: 0` is expected.

In the terminal running `build-chunks`, press Ctrl+C and wait until it exits and
the prompt returns. A partially processed batch may need recomputation; every
committed `batch-*` directory stays usable. Do not edit its manifest or copy it
while the local writer is active. The new packager checks the writer lock.
For example, if batches 0–399 are committed, Colab starts at batch 400. The
packager counts actual committed directories, rather than trusting stale totals
in a `building` manifest.

The original five modules used by `code_signature()` have not been changed in
this delivery. The new runner constructs the original output identity, retains
the original chunker and preserves chunk IDs. Worker count and cache details
are recorded as execution metadata instead of changing evidence identity.
Equivalence fixtures are supplied below; execution is pending your tests.

## 2. Run the small tests manually

Use the existing stage-5 environment:

```bash
source .venv-stage5/bin/activate
VIBIOMIR_TOKENIZER_DIR=artifacts/tokenizers/bge_m3_stage5 \
  python -m unittest discover -s tests -p 'test_stage5*.py' -v
```

The new tests check cached/uncached output equality, chunk IDs/budgets,
unchanged legacy checkpoint bytes, resumed vs fresh output, ordered scheduling,
live-writer rejection and full bundle contents. With the environment variable
set, a real two-process tokenizer test also runs offline. Without it, the real
tokenizer/process tests are explicitly skipped. Fake scheduling tests alone
are not proof of real process compatibility or GPU performance.

## 3. Package full inputs and the stopped structural checkpoint

```bash
python scripts/make_stage5_full_colab_bundle.py \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --checkpoint-dir artifacts/chunks_stage5_full_structure \
  --config configs/chunking_stage5.json \
  --output artifacts/stage5_full_colab.zip
```

The command does not run chunking or download weights. It verifies batch
checksums and source/config/tokenizer/code compatibility. It takes a shared
nonblocking lock on the stopped checkpoint for the whole archive operation.
If the lock is busy, stop the original build and retry; do not bypass the lock.

The ZIP contains:

```text
vibiomir/                   Original modules + new execution helpers
exploration/                Shared dependencies used by the CLI
tests/test_stage5*.py       Offline and optional real-tokenizer checks
colab/stage5*.ipynb         Notebook contracts needed by the tests
requirements-stage*.txt
configs/chunking_stage5.json
tokenizer/                  Tokenizer files + REGISTRY.json
inputs/                     Completed prepared input and every input batch
checkpoint/                 Existing manifest + every committed output batch
```

It excludes pending batches, locks, original Data, source SQLite stores,
`Upload_to_Drive/` and model weights. The approximately 2 GiB prepared input is
copied into a ZIP along with whatever checkpoint data already exists. ZIP64 is
enabled; already-compressed batches are stored without another compression pass.
Allow enough local and Colab disk for the archive, extraction, restored old
output and new chunks. The archive is not created merely because code exists.

If you want only a fresh semantic experiment, omit `--checkpoint-dir` and use a
different archive name. Reuse the existing prepared input and tokenizer.

## 4. Upload and configure Colab

1. Upload `artifacts/stage5_full_colab.zip` to
   `MyDrive/ViBioMIR/stage5_full_colab.zip`.
2. Open [colab/stage5_full.ipynb](../colab/stage5_full.ipynb) in Colab.
3. Keep `STRATEGY='structure'` to finish the current job. CPU runtime is enough;
   a GPU runtime can run it but CUDA remains unused for this strategy.
4. `WORKERS` defaults to at most four and the runtime's logical CPU count.
   Compare 1 vs 2/4 workers on the same input scope using separate output
   directories if you need a reliable benchmark. More workers use more RAM;
   each loads its own tokenizer. Start with 2 if the runtime is memory-limited.
5. For the independent semantic experiment, select a CUDA GPU runtime and set
   `STRATEGY='semantic'`. Structural seed batches are ignored, and the notebook
   uses separate output and backup paths. Begin with encoder batch size 8;
   reduce to 4 or 2 after a CUDA memory error. These are unmeasured starting
   settings. Requirements and pinned model revision match the original pilot.
6. Edit `BUNDLE` and `BACKUP_DIR` if your Drive layout differs. Keep output
   directories separate between strategies/configurations. Do not run two
   sessions against one backup directory.

Run the setup/preflight/tests cells in order. The notebook launches an
importable Python CLI subprocess, so spawned workers do not depend on notebook
function pickling. It does not load CUDA weights for fixed/structural runs.

## 5. Measure two new batches, then continue

The default `STOP_AFTER_BATCHES=2` pauses after **two new batches** beyond the
restored prefix. For fixed/structural runs, check the `committed` logs:

- `build_seconds`: read, tokenize/chunk and commit this batch.
- `backup_seconds`: copy/checksum this new batch on Drive.
- `documents_per_second_this_invocation`: running rate, including backup time.
- `estimated_remaining_seconds`: rough estimate using that rate, not a deadline.

The notebook also prints total runner invocation time for every strategy.
Semantic runs keep the original batch logs; their invocation time includes
model startup and a first-use weight download when necessary.

The first batches may include worker startup. Different documents have very
different lengths and chunk counts, so two batches are a timing smoke check,
not a full-corpus benchmark. Input verification and checkpoint restoration also
take time and are logged as separate phases.

If the timing is acceptable, set `STOP_AFTER_BATCHES=None` and rerun the run
cell. It resumes without redoing prepared inputs or committed chunks. The
runner keeps at most one document batch pending and writes results in original
document order. No approximate token counts or GPU retokenization are used.

If the Colab CPUs do not improve throughput, do not spend a long GPU session
assuming CUDA will fix structural tokenization. The same parallel/cache runner
also runs locally; step 7 gives the command.

## 6. Recover from disconnect and validate

Processing and file locks stay on Colab local disk. The parent copies each new
committed batch to Drive and checksums it before continuing. After disconnect:

1. Reconnect and rerun setup cells with the same bundle/strategy/configuration.
2. The runner chooses the longest compatible prefix from the bundled local
   checkpoint and the newer Drive backup. It never combines different identities.
3. It restores to local disk, then resumes. A batch whose backup copy was
   interrupted can require recomputation; previously copied batches are reused.

A same-runtime local checkpoint takes precedence over restoration. A different
input/configuration/code identity fails explicitly. Do not edit manifest hashes
to force compatibility. Keep the original local output as a backup until the
Colab result has passed validation.

The last notebook cell validates **only after the manifest becomes complete**,
using the original `validate-chunks` command and actual tokenizer lengths. It
copies `VALIDATION.json` to the Drive output along with all batches. Keep the
full backup directory and the matching prepared input/tokenizer. Human QA and
retrieval-readiness flags remain provisional.

## 7. Optional local continuation with the same improvements

This does not require rebuilding or changing the old checkpoint:

```bash
python -m vibiomir.chunk_parallel \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/chunks_stage5_full_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --config configs/chunking_stage5.json \
  --strategy structure --workers 4 --resume
```

Choose workers from actual CPU/RAM availability. Stop the original sequential
writer first. The new runner records its own code hash/worker count in execution
metadata; it does not rewrite the original checkpoint's identity or old batches.
