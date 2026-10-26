# Stage-5 local validation and closeout — 2026-10-26

The owner explicitly authorized full validation, cleanup, documentation and
local Git commits. These operations ran on the owner's local CPU. The existing
semantic and structural chunks were read in full; no new full chunk strategy
or GPU inference was launched. The owner will push the local commits.

## Executed results

| Strategy | Documents | Batches | Chunks verified | Validation | Runtime |
| --- | ---: | ---: | ---: | --- | ---: |
| Structural | 855,745 | 1,712 | 5,512,624 | Passed | 654.186 seconds |
| Semantic | 855,745 | 1,712 | 9,036,279 | Passed | 744.470 seconds |

The two runs executed concurrently with four CPU workers each. The two document
counts refer to the same prepared snapshot, not 1,711,490 distinct documents.
All **14,548,903 chunks** were checked. Both runs exited successfully.

The runner uses the unchanged original `validate_chunks` on every batch, with
an independently initialized pinned local tokenizer per process. It creates
temporary batch-local views, preserves actual evidence/identity/gzip payloads,
and reconciles global sequence, checksums, geometry and totals before writing
the full report. No sampling substitutes for full row/token checks here.

Checks include exact source substrings/offsets, parent/derived versions, stable
chunk IDs, retrieval context, actual token counts and the 512-token cap,
duplicate/orphan IDs, complete non-whitespace source coverage, and empty rows.
Completed batch reports are checkpointed under `artifacts/validation_stage5/`.
Every resume rechecks source hashes; compatible completed batch results can be
reused. Original chunk-building code and output manifests were not changed.

Evidence:

- [Machine-readable results](RESULTS.json).
- [Structural full validation](STRUCTURE_VALIDATION.json),
  [execution identity](STRUCTURE_EXECUTION.json), [log](structure.log).
- [Semantic full validation](SEMANTIC_VALIDATION.json),
  [execution identity](SEMANTIC_EXECUTION.json), [log](semantic.log).
- [Original Colab semantic validation](COLAB_SEMANTIC_VALIDATION.json): its
  complete JSON result equals the independently rerun local semantic report.
- [Stage-5 tests](tests_stage5.log): **51 run, 50 passed, 1 skipped**. The skipped
  test requires optional real CUDA inference. The suite includes original vs
  parallel validation equivalence, altered evidence, lost source coverage,
  false totals, checkpoint reuse and post-checkpoint source corruption. Real
  tokenizer and real spawned CPU worker checks were enabled.

## Cleanup actually performed

After both full validations passed:

- Verified every file in the incomplete Drive extractions against the recovered
  SHA-256 inventory; removed the redundant `extracted_snapshot/` tree.
- Verified every file of `artifacts/stage5_full_colab/` against the retained
  ZIP's paths, sizes and CRCs, including a complete ZIP CRC read.
- Verified all 457 retired structural checkpoint batches equal the corresponding
  batches in the completed, fully validated structural store; removed the old
  extracted bundle and its partial checkpoint.
- Moved the two consumed handoff ZIPs out of `artifacts/` into
  `local_assets/archives/stage5_handoff_20261026/`.
- Removed the now-empty `artifacts/temp/` directory.

**11,227 redundant files / 10,151,697,136 bytes (9.45 GiB) were removed.**
The four original Drive ZIPs, the five loose originals, completed semantic
shards, successful stage-3/4 stores and stage-5 smoke cases were retained.
`Upload_to_Drive/` was retained as an owner-managed historical snapshot and is
now explicitly excluded from Git/current upload instructions.

[CLEANUP.json](CLEANUP.json) records retired paths, archive hashes and byte
counts. [cleanup_files.csv](cleanup_files.csv) lists every removed file and its
reason. [The retired manifest](RETIRED_CHECKPOINT_MANIFEST.json) preserves the
partial checkpoint's metadata. [Cleanup execution](cleanup_superseded.py) is a
one-time record with preconditions; it must not be rerun against the cleaned
workspace. [cleanup.log](cleanup.log) records successful execution.

## Use and publication

Read [Stage-5 results and handoff](../../docs/STAGE5_RESULTS_AND_HANDOFF.md)
for exact local/Drive paths, validation commands and next review steps.
Upload the full structural store and these small current reports. The original
semantic Drive store can be reused because the full local report matches its
Colab report; uploading another copy of the same gzip data is unnecessary.

The Git publication contains source, tests, configuration, notebooks and small
review evidence. Runtime data, environments and upload snapshots stay excluded.
See [changed-file inventory](../../sharing/git_stage5_files.txt) and
[one-file commit plan](../../sharing/git_stage5_commit_plan.json).

This closes **full technical validation of the existing structural and semantic
stores**. Human/clinical QA, official-ID eligibility, final corpus selection,
the full fixed comparative baseline, parameter sweeps and retrieval evaluation
remain declared work. The original provisional QA/readiness flags stay false
or pending. They are not overwritten to manufacture full S05 acceptance.
