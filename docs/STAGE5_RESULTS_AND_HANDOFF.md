# Stage 5 — results, validation and team handoff

Observation date: 2026-10-26. **Full local validation passed for structural and
semantic outputs: 14,548,903 chunks checked across both strategies.**
See [execution evidence](../reports/stage5_validation_20261026/README.md) and
[machine-readable results](../reports/stage5_validation_20261026/RESULTS.json).

| Strategy | Documents | Chunks | Local full validation | Runtime with 4 workers |
| --- | ---: | ---: | --- | ---: |
| Structural | 855,745 | 5,512,624 | Passed | 654.2 seconds |
| Semantic | 855,745 | 9,036,279 | Passed; same report as Colab | 744.5 seconds |

The two validations ran concurrently. The stage-5 test suite ran 51 tests:
50 passed, one optional CUDA integration test skipped. Human QA remains pending.

## Canonical data

All strategies use the same prepared snapshot: 855,745 document ledger rows
and 1,712 batches. A document without usable content remains a row with no
chunks; it is not silently dropped. Do not compare the collected document
count to a published URL count as an acquisition gate.

| Artifact | Canonical local path | Observed size | Purpose |
| --- | --- | ---: | --- |
| Prepared inputs | `artifacts/chunk_inputs_stage5_full/` | 1.95 GiB | Read-only cleaned source packets |
| Structural chunks | `artifacts/chunks_stage5_full_structure/` | 2.58 GiB | 5,512,624 chunks |
| Semantic chunks | `artifacts/semantic_parallel_stage5_full/merged/` | 3.31 GiB | 9,036,279 chunks; use this full store downstream |
| Semantic plan | `artifacts/semantic_parallel_stage5_full/plan.json` | Small JSON | Shard coverage and exact execution identity |
| Semantic shards | `artifacts/semantic_parallel_stage5_full/shard_0/`, `shard_1/` | 3.31 GiB combined | Completed recovery/provenance copies |
| Tokenizer | `artifacts/tokenizers/bge_m3_stage5/` | 16.3 MiB | Local pinned BGE-M3 tokenizer and checksums |
| Configuration | `configs/chunking_stage5.json` | Small JSON | Target 384, max 512, overlap 64, semantic min 128, context 48, threshold 0.65 |

The source encoder/tokenizer is `BAAI/bge-m3`, revision
`5617a9f61b028005a4858fdac845db406aefb181`. The semantic model registry records
567,754,752 parameters, FP16, CLS pooling and L2 normalization. Different GPU
execution/batching settings are retained in the merged manifest's history.

Each store must include its own `manifest.json`, `VALIDATION.json` and every
`batch-*/batch.json` and `batch-*/documents.jsonl.gz`. Restore whole stores with
their relative paths intact. Registry copies in Git are metadata only.

## Full local validation

The CPU runner below calls the **original** `validate_chunks` implementation
for every batch, using temporary one-batch views with unchanged gzip payloads
and identity signatures. It reconciles global sequence, signatures, hashes,
document geometry and totals before writing the ordinary `VALIDATION.json`.
Per-batch checkpoints allow interruption/resume; each invocation verifies source
checksums again before reusing previous row-validation results. Temporary views
are removed automatically. No encoder, CUDA, network or new chunk build is used.

From the project root, activate `.venv-stage5` and run:

```bash
python -m vibiomir.chunk_validation \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/chunks_stage5_full_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --checkpoint-dir artifacts/validation_stage5/structure \
  --workers 4 --resume

python -m vibiomir.chunk_validation \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/semantic_parallel_stage5_full/merged \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 \
  --checkpoint-dir artifacts/validation_stage5/semantic \
  --workers 4 --resume
```

Use 1–2 workers on a machine with limited free RAM. The closeout execution used
four workers for each of two concurrent validations on the owner's local CPU.
Only the validator checkpoint's code/input identity controls cache reuse;
changed code/config/data requires a new validation checkpoint directory.

The existing sequential command remains available:

```bash
python -m vibiomir validate-chunks \
  --input-dir artifacts/chunk_inputs_stage5_full \
  --output-dir artifacts/chunks_stage5_full_structure \
  --tokenizer-dir artifacts/tokenizers/bge_m3_stage5
```

Checks cover every document/chunk: exact original source slices and offsets,
version/source mapping, chunk IDs, separate retrieval context, actual tokenizer
lengths up to 512 tokens, duplicate/orphan IDs, non-whitespace source coverage,
empty-document accounting and manifest totals. The report must say
`validation=passed`. Original chunk-building code and data identities remain
unchanged.

## Publication and Drive layout

- [GitHub source/review inventory](../sharing/github_files.txt) lists all
  publishable project files; [changed-file inventory](../sharing/git_stage5_files.txt)
  lists this closeout's files. The owner requests one file per Conventional
  Commit. The assistant creates local commits; the owner runs `git push`.
- [Required Drive paths](../sharing/drive_paths.txt) provide the reproducible
  working data. [Stage-5 incremental upload](../sharing/drive_after_stage5.txt)
  identifies the new results. [Optional historical paths](../sharing/drive_optional_paths.txt)
  separate pilots and previous comparisons from current full stores.
- The semantic source already exists on Drive under
  `semantic_parallel_run01/merged`; a local folder name change does not require
  uploading the same payloads again. Keep a note of the local-to-Drive mapping.
- Upload the completed structural store, the current validation/execution
  reports, and missing matching inputs/tokenizer. Reuse matching Drive copies.
- Do not use `Upload_to_Drive/` as the current release: it is an owner-managed
  historical snapshot, excluded from Git. Do not upload old package ZIPs,
  incomplete extractions, caches, or local environments as current results.
- No new Colab upload or GPU run is required for validation or this handoff.
  Rebuilding semantic chunks later still follows the existing two-Colab guide.

## Acceptance boundary and next work

Technical source fidelity is different from clinical/content QA. Keep
`human_qa=pending`, `selection_is_final=false` and `ready_for_retrieval=false`
until the corresponding review/selection gates have been performed; validation
does not change those flags. The default semantic threshold is not dev-tuned.

The 200-document fixed smoke baseline is validated; a full fixed baseline is
not present. It remains a separate CPU experiment if the full three-strategy
comparison is required. Overlap/threshold sweeps and retrieval comparisons
remain declared S05/S10 experiment work. No full fixed build was implicitly
launched as part of the owner's request to validate existing outputs.

Review the original chunk examples, extend sampling to English and rare
formats, resolve inherited stage-4 selection/clinical QA, and record reviewer
decisions before freezing the working corpus. Then use the validated stores
for lexical/dense retrieval pilots. Semantic boundary embeddings are not a
persisted dense index; indexing still requires encoding the final retrieval
texts with stable chunk-ID mappings.
