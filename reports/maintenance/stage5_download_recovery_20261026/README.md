# Stage-5 semantic download recovery — 2026-10-26

**Historical recovery snapshot:** full structural and semantic local validation
subsequently passed, and redundant incomplete extractions were removed after
verification. The original ZIPs and loose originals remain backed up. Current
paths, cleanup evidence and remaining review scope are in
[the closeout record](../../stage5_validation_20261026/README.md) and
[handoff guide](../../../docs/STAGE5_RESULTS_AND_HANDOFF.md). The observations
below describe the earlier recovery, before that closeout.

The owner explicitly authorized inspection, recovery and organization of the
downloaded Colab results under `artifacts/temp/`. This operation used local
CPU/disk only. It did not rerun semantic inference, rechunk the corpus, install
dependencies or execute the full chunk validator locally.

## Recovered result

The four Drive ZIPs are complementary downloads of one folder. A batch's
`batch.json` and `documents.jsonl.gz` can be in different ZIPs; the ZIPs are not
four independent stores. Their original incomplete extractions were missing
17 files. Five were found loose in `temp/`; the ZIPs retained all 17 originals.

All 6,853 archived files, totaling 7,098,003,499 bytes (6.61 GiB), were recovered
into this canonical local layout:

```text
artifacts/semantic_parallel_stage5_full/
├── plan.json
├── merged/                 # Full semantic chunk store for downstream use
│   ├── manifest.json
│   ├── VALIDATION.json
│   └── batch-000000/ ... batch-001711/
├── shard_0/                # Retained backup and execution provenance
└── shard_1/                # Retained backup and execution provenance
```

| Store | State | Batches | Documents | Chunks |
| --- | --- | ---: | ---: | ---: |
| Merged semantic | Complete | 1,712 | 855,745 | 9,036,279 |
| Shard 0, global batches `[0, 863)` | Complete | 863 | 431,500 | 5,183,315 |
| Shard 1, global batches `[863, 1712)` | Complete | 849 | 424,245 | 3,852,964 |
| Local structural, manifest observed separately | Complete; full validation file absent | 1,712 | 855,745 | 5,512,624 |

The merged semantic identity is
`5eb330242915cec8f2a3d990ddab2ebb860028bb089e5b024cfefba3b83df6bc`.
Its input is the existing full prepared snapshot
`336ff927b965e75e5cf44057385245c8a5bc62929f7b2a2ffde31732ff57534e`.

## What was actually checked

The executed [recovery record](recover_download.py) checked:

- ZIP CRC while reading every member, plus original archive SHA-256 hashes.
- Every restored gzip batch's SHA-256, read again from the written destination.
- Every prepared-input gzip checksum, batch sequence and signature.
- Identity/plan signatures and the matching local code, config and tokenizer.
- Complete contiguous shard coverage and document geometry against inputs.
- Manifest counts reconciled against every batch descriptor.
- Each merged gzip payload's SHA-256 equals that of its corresponding shard.
- Imported full Colab validation passed and matches the restored totals/source.

These are complete download-integrity checks. Manifest/batch document and chunk
totals were reconciled using descriptors, not recounted from every JSONL row.
The original **full source-slice/token/coverage validation was run on Colab**;
its exact report was preserved, not represented as a fresh local full run.

An additional executed [bounded sample check](check_sample.py) used the pinned
local tokenizer and the original `verify_chunk` function: 63 documents across
32 batches, 410 semantic chunks and 287 structural chunks passed source-slice,
version, ID, context and actual token-budget checks. This is deterministic
sampling, not a random estimate of corpus-wide quality. It covers the repaired
payload batches and the shard boundary. Observed hints were Vietnamese,
Chinese and mixed-script; English-specific sample coverage is not established.
See [sample results](SAMPLE_CHECK.json).

## Misplaced loose files

Their content was identified using exact archive bytes/SHA-256, rather than
filenames alone. None belongs to a different stage.

| Original loose path under `artifacts/temp/` | Original archive-relative location, restored under the canonical root |
| --- | --- |
| `plan.json` | `plan.json` |
| `manifest.json` | `merged/manifest.json` |
| `VALIDATION.json` | `merged/VALIDATION.json` |
| `batch.json` | `shard_0/batch-000788/batch.json` |
| `documents.jsonl.gz` | `shard_0/batch-000442/documents.jsonl.gz` |

In particular, the loose `batch.json` and `documents.jsonl.gz` belong to
**different batches** and must not be placed together. Both also have exact
copies in the matching merged batches; all canonical copies are present.
The other 12 missing extracted files were recovered directly from the ZIPs.

All originals were preserved, then relocated after integrity checks passed:

```text
local_assets/archives/stage5_colab_download_20261026/
├── zip_parts/              # All four original ZIP downloads
├── extracted_snapshot/     # Original incomplete extraction directories
└── loose_files/            # Original five loose files, preserved as backups
```

`artifacts/temp/` is now empty. No source dataset, prior checkpoint, or original
download was deleted. [RECOVERY.json](RECOVERY.json) records every relocation,
all missing files and archive hashes. [restored_files.csv](restored_files.csv)
maps each recovered file to its ZIP member, size and SHA-256.

## Stage-5 completion checklist

1. **Done:** semantic full run/merge, imported passed full Colab validation,
   complete local download recovery and independent integrity checks.
2. **Pending:** full structural validation. Its manifest is complete, but no
   `artifacts/chunks_stage5_full_structure/VALIDATION.json` was present. Run
   manually from the project root in the existing stage-5 environment:

   ```bash
   python -m vibiomir validate-chunks \
     --input-dir artifacts/chunk_inputs_stage5_full \
     --output-dir artifacts/chunks_stage5_full_structure \
     --tokenizer-dir artifacts/tokenizers/bge_m3_stage5
   ```

3. **Pending:** human review. [REVIEW_SAMPLE.csv](REVIEW_SAMPLE.csv) contains 35
   original semantic/structural chunks with context and source coordinates.
   All rows remain `review_status=pending`. Check medical negation, doses,
   headings/Q&A/tables and coherent boundaries; extend sampling to English and
   rare formats rather than treating this small packet as complete QA.
4. **Pending for full S05 scope:** full fixed baseline (only the 200-document
   smoke result is currently recorded), declared overlap/semantic-boundary
   pilots and any remaining A07/A08 acceptance evidence. Follow the existing
   [stage-5 runbook](../../../docs/CHUNKING_STAGE5.md); no full fixed rerun was
   launched during this recovery.
5. Keep each strategy separate. Compare retrieval quality later; no final
   chunker or tuned threshold has been selected by recovering these outputs.

The Colab report records 2,774 documents without chunks (0.32%) and 3,109,735
chunks below the soft 128-token minimum (34.41%). Short chunks are review
candidates, not automatic validation failures. Semantic produced 1.639 times
the structural chunk count, so downstream embedding/index storage must be
budgeted from the actual 9,036,279 chunks. Language hints are heuristic labels,
not verified language annotations.

Human/clinical QA, official-ID reconciliation and corpus selection remain
pending. `human_qa`, `selection_is_final`, `ready_for_retrieval` and all original
manifests were retained unchanged. No claim of final S05 acceptance is made.

## Sharing

- GitHub: this small recovery report, file inventory, review packet, plan and
  new metadata snapshots under `reports/run_registry/`.
- Drive/downstream processing: the complete `merged/` store plus matching
  prepared inputs, tokenizer and config. Retain `plan.json` and both shards as
  recovery/execution provenance.
- No further Colab upload or GPU run is needed to import this result. Do not
  upload the redundant local ZIP/extraction backups again; the source Drive
  folder already contains the corresponding run.

The one-time recovery script refuses to overwrite a published destination;
it is retained as an execution record, not an instruction to run it again.
