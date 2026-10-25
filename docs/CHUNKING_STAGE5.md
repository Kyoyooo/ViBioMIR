# Stage 5 — verbatim chunk store (S05)

This delivery supplies code, offline tests, configuration and a semantic GPU
notebook. **The assistant has not run any stage-5 code, tests, downloads,
packaging or corpus processing.** The owner executes the commands below from
the project root. Python 3.10+ on Linux is required; the local implementation
uses the project's existing `fcntl` locks.

## Input and scope

Use these existing successful outputs:

- `artifacts/document_store_stage3_full_v2`
- `artifacts/cleaning_stage4_full_apply` and its passed `VALIDATION.json`

The recorded stage-4 snapshot contains 913,969 versions, 855,745 document
selections and 1,029,307 regions across **all versions**. Stage 5 uses only the
selected candidate version of each document, so its region/chunk counts will
differ. Quarantined/unavailable documents remain explicit ledger rows with
`chunks: []`. Human QA, official ID reconciliation and final corpus freeze are
still pending; outputs retain `is_final: false` and `ready_for_retrieval: false`.

No crawling, cleaning rerun, synthetic evidence, full-corpus embedding or index
build is part of this delivery. Exact duplicate candidates receive a grouping
hash without deleting any document ID or copying evidence between aliases.
Near-duplicate clustering and retrieval-quality comparison remain later work.

| Operation | Where | GPU |
| --- | --- | --- |
| Input preparation and source-slice checks | Local CPU | No |
| Tokenizer-only download, fixed and structural chunks | Local CPU | No |
| Chunk validation, including actual tokenizer lengths | Local CPU | No |
| Semantic sentence embeddings and boundary pilot | Colab CUDA | Required by supplied runner |

The semantic runner supports CUDA GPU runtimes; TPU is not implemented. Start
with one account. There is no need for simultaneous accounts for the pilot.

## 1. Install and run the small offline tests

Use your existing project environment, or create a separate virtual environment:

```bash
python -m venv .venv-stage5
source .venv-stage5/bin/activate
python -m pip install -r requirements-stage5.txt
python -m unittest discover -s tests -p 'test_stage5*.py' -v
```

The fixture tests need no network, weights or GPU. The real-tokenizer test is
explicitly skipped until the environment variable in step 2 is set. A skip is
not proof that real BGE-M3 offsets work. Existing stage-3/4 tests are unchanged.

## 2. Download only the pinned tokenizer, then test real Unicode offsets

This is an explicit network operation; it does not download model weights:

```bash
python -m vibiomir download-chunk-tokenizer \
  --output-dir artifacts/tokenizers/bge_m3_stage5

VIBIOMIR_TOKENIZER_DIR=artifacts/tokenizers/bge_m3_stage5 \
  python -m unittest discover -s tests -p 'test_stage5*.py' -v
```

The tokenizer is subsequently loaded offline and its local files are checked
against `REGISTRY.json`. If a download fails, choose a new empty tokenizer
directory for the retry and use that directory in subsequent commands.

The public checkpoint is `BAAI/bge-m3` at revision
`5617a9f61b028005a4858fdac845db406aefb181`, dated
[2024-07-03](https://huggingface.co/BAAI/bge-m3/commit/5617a9f61b028005a4858fdac845db406aefb181),
before the competition cutoff. The
[pinned model card](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)
describes its 1,024-dimensional multilingual embeddings. The semantic runner
uses dense CLS pooling with L2 normalization, records the actual parameter count
and rejects a model above 15B. It does not use closed-model services.
[Fast-tokenizer offsets](https://huggingface.co/docs/transformers/v4.57.1/main_classes/tokenizer)
map to original text; the code never decodes tokens to create evidence.

## 3. Prepare a small smoke snapshot on CPU

```bash
python -m vibiomir prepare-chunk-inputs \
  --cleaned-dir artifacts/cleaning_stage4_full_apply \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --output-dir artifacts/chunk_inputs_stage5_smoke \
  --max-documents 200 --batch-documents 50
```

This is the first 200 selected IDs, **a functional smoke check, not a random or
language-balanced quality sample**. It includes explicit rows for unavailable
IDs. Use it to check the handoff and costs; do not tune semantic thresholds on
the competition test queries. The full input snapshot is separate in step 5.

Preparation opens both stores read-only, checks parent manifest/validation
links, verifies every used region against its original stage-3 source, and
reuses exact mapped blocks for headings/paragraphs/table rows. Unmapped blocks
are counted and fall back to source lines and sentence/token windows.

To avoid repeated scans of the approximately 17 GB and 15 GB databases, this
stage trusts the existing stage-4 validation for the frozen snapshot rather
than hashing both whole SQLite files again. Keep them idle and unchanged.
Per-used-source text/hash and version relationships are checked again. This is
not a new complete verification of all legacy input files or the old exports.
If future stage-4 outputs contain recovered HTML, add `--data-dir Data` so the
existing loader can verify the referenced original HTML shard; the current
validated full output contains only `stage3_text` regions.

## 4. Build and validate both CPU baselines

All referenced configuration files are shipped. No additional reviewed config
or approval command is required.

```bash
python -m vibiomir build-chunks \
  --input-dir artifacts/chunk_inputs_stage5_smoke \
  --output-dir artifacts/chunks_stage5_smoke_fixed \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --strategy fixed --config configs/chunking_stage5.json

python -m vibiomir validate-chunks \
  --input-dir artifacts/chunk_inputs_stage5_smoke \
  --output-dir artifacts/chunks_stage5_smoke_fixed \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5

python -m vibiomir build-chunks \
  --input-dir artifacts/chunk_inputs_stage5_smoke \
  --output-dir artifacts/chunks_stage5_smoke_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --strategy structure --config configs/chunking_stage5.json

python -m vibiomir validate-chunks \
  --input-dir artifacts/chunk_inputs_stage5_smoke \
  --output-dir artifacts/chunks_stage5_smoke_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5
```

Inspect `VALIDATION.json`, plus original and retrieved text in a few batches.
It reports all documents, empty outputs, short chunks, counts/tokens by language
hint, overlapping characters, explicit question regions split into several
chunks, table regions and unmapped blocks. These are engineering signals;
language hints and detected Q&A are not reviewed annotations.

## 5. Full CPU processing and resume

After the smoke tests and a manual sample look reasonable:

```bash
python -m vibiomir prepare-chunk-inputs \
  --cleaned-dir artifacts/cleaning_stage4_full_apply \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --output-dir artifacts/chunk_inputs_stage5_full \
  --batch-documents 500

python -m vibiomir build-chunks \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/chunks_stage5_full_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --strategy structure --config configs/chunking_stage5.json

python -m vibiomir validate-chunks \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/chunks_stage5_full_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5
```

For a full fixed baseline, rerun the build/validation commands with
`--strategy fixed` and `artifacts/chunks_stage5_full_fixed` as output. The shared
prepared input is reused.

Each committed batch logs its ID, document count and chunk count. Memory is
bounded by one document batch and its chunks, rather than the corpus. Reduce
`--batch-documents` at preparation time if necessary; use a new input directory
when changing it. Storage consumption is not premeasured: the input snapshot
copies selected evidence once into gzip, and each chunk strategy adds its own
compressed evidence and context. Check space after the smoke run and scale by
document size/chunk counts, allowing for unusually long documents.

To resume, repeat the **identical command with `--resume` added**. Input scope,
batch size, tokenizer, strategy, config and relevant code must be unchanged.
An existing output without `--resume` gives an actionable error. A completed
output with `--resume` returns its manifest without rebuilding it.

For a controlled interruption test, add `--stop-after-batches 1` to either
preparation or chunk building, then repeat with `--resume` and omit the stop
option. This pauses the operation; it does not reduce its final input scope.
By contrast, `--max-documents` creates a permanently bounded prepared snapshot.
If a stop occurs exactly at the final batch, the next resume marks it complete.

There is one atomic directory commit per batch on local Linux. A process exit
before that commit leaves an ignored `.pending-*` directory; a committed batch
is recovered even if the final manifest update was interrupted. Checksums are
verified on resume. Do not delete committed data to bypass a mismatch. Choose a
new output if inputs/config/code changed. Small batches reduce lost work.

## 6. Semantic GPU pilot on Colab

Build a portable bundle **locally and manually** using the same smoke snapshot:

```bash
python scripts/make_stage5_colab_bundle.py \
  --input-dir artifacts/chunk_inputs_stage5_smoke \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --output artifacts/stage5_colab_pilot.zip
```

1. Upload `colab/stage5_semantic.ipynb` to Colab.
2. Put the ZIP at `MyDrive/ViBioMIR/stage5_colab_pilot.zip`, or edit `BUNDLE`.
3. Select an available CUDA GPU. T4 with encoder batch 8 is an engineering
   starting point, **not a measured memory guarantee**. L4/A100 and other CUDA
   GPUs use the same runner. If your advertised G4 runtime exposes CUDA it can
   use this runner; otherwise it is unsupported. TPU v5e/v6e is not supported.
4. Edit `BACKUP_DIR` to a new Drive directory for this input/config. Run the cells
   in order. Network is needed for dependencies and the pinned public weights.
5. If CUDA runs out of memory, reduce `ENCODER_BATCH_SIZE` to 4 or 2, restart the
   runtime, rerun the setup cells and resume. Model parameters, dtype and pooling
   stay fixed. Execution metadata records GPU/runtime versions.
6. Computation uses local Colab disk; each committed batch is copied and
   checksummed on Drive before the next batch. After disconnecting, rerun the
   notebook to restore and resume. A copy interrupted before its commit may
   require recomputing that batch. Use one writer per backup directory. Drive
   is not used for locks; checksum failures are errors, not warnings to ignore.
7. Keep/download `BACKUP_DIR` with `manifest.json`, `VALIDATION.json` and all
   `batch-*` directories. Validation uses CPU tokenizer work and can also be
   rerun locally with the same input snapshot and tokenizer.

No source SQLite, raw Data directory or full weights belong in the upload ZIP.
For a later full semantic run, package the full prepared input and use a new
Drive output directory; budget storage/runtime from measured pilot costs
first. Separate accounts can process separate prepared input snapshots with
separate outputs, but this delivery does not merge sharded runs or coordinate
three accounts automatically.

The default semantic threshold 0.65 is a starting parameter, not a dev-tuned
result. Select it on a reviewed development set in later experiments. Full
embedding/index creation belongs to S08.

## Contracts and limitations

- Default budgets: target 384, maximum 512, overlap 64; semantic minimum 128.
  Counts include special tokens and added context in the **final retrieval
  text**, using the pinned BGE-M3 tokenizer. Target/minimum are soft at final or
  short sections; maximum is checked for every chunk.
- Fixed windows respect section and kept-region boundaries. Structural chunks
  combine adjacent sentences/paragraphs inside a section, using complete
  sentence overlap when it fits. Semantic chunks add adjacent-vector cosine
  boundaries within those sections. Oversized individual sentences/rows use
  exact token-offset windows. No strategy crosses a removed source gap.
- An explicit contiguous short Q&A that fits the maximum budget stays together
  even when its length is above the target, including in the fixed baseline.
- Short Q&A is preserved together when contiguous within a section/region and
  within the cap. Explicit question-line context can accompany long answers;
  arbitrary question detection and lost Q&A metadata are not magically fixed.
- Generic `h` remains flat; h1–h6 retain source hierarchy. Numbered-heading
  inference uses a small explicit medical-heading allowlist. Ambiguous lists
  and dosage numbers are not promoted to headings.
- Legacy `tr` provides row text without reliable column/header semantics. A
  first-row prefix is context-only; the implementation does not assert it is a
  true header or fabricate table cells. Large rows may split at token bounds.
- Title/heading/question/row context is bounded and may be shortened. It is
  recorded separately as `context_prefix`; it never becomes submitted evidence.
- `chunk_text` is the exact kept-region substring; `region_start/end` are local
  Python code-point offsets. `source_start/end` map to the parent text, legacy
  block or raw HTML node identified by `source_locator_json`. Unicode/entities
  in evidence remain unchanged. IDs bind the strategy/config/model snapshot,
  document/version, region and span, independent of iteration/resume order.
- Validation checks file hashes, input/output accounting, selected versions,
  source slices, token lengths, duplicate/orphan IDs and coverage of every
  non-whitespace source character. It does not rerun the chunker or clinical QA.
- Chunk boundaries are provisional. Do not compare strategy quality on unequal
  corpora or freeze a strategy before the retrieval experiments.

Acceptance coverage is supplied, not yet executed: A07 has multilingual
headings/Q&A/table/oversized-span fixtures for three strategies; A08 has exact
offset/version/text corruption tests and real-tokenizer integration. A09 is
limited here to normalized exact grouping hashes with every original doc/span
retained; near-duplicate candidates and cross-ID alias tables are not delivered.

## Reading the output

Prepared inputs and chunk stores use the same deliberately small layout:

```text
manifest.json
.store.lock
batch-000000/
  batch.json
  documents.jsonl.gz
batch-000001/...
VALIDATION.json              # chunk output after successful validation
```

One gzip line is one document. Prepared lines contain selected regions and
structure units. Chunk output lines contain `doc_id`, `derived_version_id`,
`availability` and `chunks: [...]`. This is an internal store, **not** the
competition submission JSON. Future indexers can flatten it without loading the
whole corpus:

```python
from vibiomir.chunk_batches import batches, read_rows

for path, info in batches('artifacts/chunks_stage5_full_structure'):
    for document in read_rows(path):
        for chunk in document['chunks']:
            index_text = chunk['retrieval_text']
            original_evidence = chunk['chunk_text']
            # Use chunk_id for index mapping; preserve doc_id and source offsets.
```

Keep the prepared input snapshot with all three strategy outputs. Existing
stage-3/4 history remains authoritative; no raw or cleaned input is modified.
