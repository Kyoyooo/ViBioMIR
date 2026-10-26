# Team review, GitHub and Google Drive handoff

Use GitHub for source, settings, documentation and small review evidence. Use
Drive for complete datasets, databases, generated artifacts and original media.
The supplied path lists are inventories, not upload commands; no files have
been uploaded or published by this cleanup.

## Reading order for reviewers

1. [Root README](../README.md): goal, measured progress and pending work.
2. [Task definition](BAI_TOAN_VIBIOMIR.md): scoring and evidence requirements.
3. [Development specification](SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md): intended
   system and acceptance criteria; this is broader than the implemented code.
4. [Full-audit analysis](../reports/data_audit/full_run_01/analysis/REPORT.md)
   and [reviewed processing plan](../reports/data_audit/full_run_01/analysis/PROCESSING_PLAN.md).
5. [Stage-4 decisions](../reports/cleaning_stage4_rule_review_01/DECISIONS.md)
   and [execution evidence](../reports/cleaning_stage4_rule_review_01/EXECUTION.md).
6. [Stage-5 results/handoff](STAGE5_RESULTS_AND_HANDOFF.md) and
   [runbook](CHUNKING_STAGE5.md), then `vibiomir/chunking.py`,
   `chunk_pipeline.py`, `chunk_batches.py`, `chunk_models.py` and their tests.

Use the [documentation map](README.md) and [report index](../reports/README.md)
for supporting material. Do not read generated audit draft plans as reviewed
cleaning policy. A technical validation pass is not clinical QA.

## What to put on GitHub

Candidate directories: [sharing/github_paths.txt](../sharing/github_paths.txt).
The expanded current file list is [sharing/github_files.txt](../sharing/github_files.txt).
Apply the root `.gitignore` to directory contents; the expanded inventory is a
dated snapshot and does not update automatically when new files are added.

| Paths | Why they belong in the review repository |
| --- | --- |
| `README.md`, `LICENSE`, `.gitignore`, `sharing/` | Entry point, code license, file-selection rules |
| `docs/`, original mentor summary | Rules, design, runbooks and source interpretation |
| `vibiomir/`, `exploration/`, `scripts/` | Implementation and manual helper commands |
| `tests/`, `configs/`, `requirements-*.txt` | Reviewable fixtures, shipped settings and dependencies |
| `colab/` | Semantic GPU notebook |
| Small files in `reports/` | Observed counts, decisions, test logs and validation evidence |
| `reports/run_registry/` | Small copies of successful-run control metadata, readable without downloading databases |

All source modules are kept at their existing paths. Multiple stage runbooks
describe different operations; they are not alternative versions of one file.
Requirements files form a dependency chain and must remain together. Historical
test logs and rule-review counterexamples are retained for reviewers.

The large datasets, artifacts, original media, model weights, SQLite/Parquet
files, virtual environment and bytecode are excluded. Previously tracked
bytecode files were removed from the working tree; stage those deletions too.

Use one explicitly selected file per add/commit, following the owner's
publication convention. For example, for a future source change:

```bash
git add -- path/to/changed_file.py
git diff --cached --name-only
git commit -m "fix(chunking): describe the concrete change"
```

For the 2026-10-26 closeout, the owner explicitly requested that the assistant
create local commits. After those complete, the owner only needs to push:

```bash
git push -u origin main
```

The exact changed-file inventory is in
[sharing/git_stage5_files.txt](../sharing/git_stage5_files.txt). Give reviewers the
commit ID alongside the Drive folder so they can match code to data. The code
license does not replace upstream dataset/model/media licenses; source citation
and dataset licensing are documented in the task definition.

## What to put on Drive

Stable existing directories: [sharing/drive_paths.txt](../sharing/drive_paths.txt).
Preserve the relative folder tree under a shared `ViBioMIR/` folder:

```text
ViBioMIR/
  Data/
  artifacts/
  reports/
  local_assets/
```

| Directory | Approximate observed disk usage | Role / priority |
| --- | ---: | --- |
| `Data/ViBioMIR/` | 2.6 GiB | Original collected shards and crawl metadata; source verification |
| `Data/official/` | 37 MiB | Official links/queries, dataset card and provenance |
| `artifacts/document_store_stage3_full_v2/` | 24 GiB | Successful versioned document store and exports |
| `artifacts/cleaning_stage4_full_apply/` | 18 GiB | Successful cleaned regions and selections |
| `artifacts/chunk_inputs_stage5_full/` | 2.0 GiB | Prepared inputs reused by all chunkers; full semantic handoff |
| `artifacts/tokenizers/bge_m3_stage5/` | 17 MiB | Pinned tokenizer and local checksum registry |
| Stage-5 smoke input/fixed/structure directories | Under 2 MiB combined | Quick reproducible review cases |
| `reports/data_audit/` | About 12 GiB | Audit checkpoints, per-record exports and reviewed analysis |
| `reports/eda/` | About 454 MiB | Historical exploration, including metrics SQLite |
| `artifacts/cleaning_stage4_full_dry/` | 18 GiB | Validated baseline needed to reproduce dry-run/apply comparison |
| Stage-4 pilot directories | Under 8 MiB combined | Historical small dry-run and reviewed pilot |
| `local_assets/archives/` | About 2.3 GiB | Original dataset ZIP downloads; optional recovery archive |
| `local_assets/mentor_video/` | About 4.4 GiB | Original mentor video ZIP and extracted media; optional for code review |

Sizes are rounded filesystem observations on 2026-10-25, not future capacity
guarantees. The existing stable inventory is far below the owner's approximately
4 TB capacity; future embeddings/indexes will add their own storage. Original
ZIPs and extracted shards are intentionally both retained as recovery/source
assets. They are not failed pipeline versions.

For a quick code review, reviewers can use GitHub and the tiny smoke artifacts.
For source/cleaning verification, prioritize `Data/`, stage-3 `full_v2`, stage-4
full apply and the complete audit reports. For chunking experimentation, also
download the prepared inputs and tokenizer. Copy whole successful store
directories, including manifests and exported parts, rather than selecting only
a database or report.

## Full stage-5 outputs

**2026-10-26 update:** semantic full run and merge completed on Colab and passed
full validation. Full structural and semantic local validation also passed,
covering 14,548,903 chunks. Use [the results/handoff guide](STAGE5_RESULTS_AND_HANDOFF.md).
The semantic download is recovered locally at
`artifacts/semantic_parallel_stage5_full/merged/`; original relative paths and
all 1,712 batches are restored. See [the recovery record](../reports/maintenance/stage5_download_recovery_20261026/README.md)
for checks, backups and the remaining stage-5 checklist. The source Drive run
already contains this output; uploading local ZIP/extraction duplicates is
unnecessary. Share the new small report and metadata snapshots on GitHub.

[sharing/drive_after_stage5.txt](../sharing/drive_after_stage5.txt) lists
`artifacts/chunks_stage5_full_structure/` separately. Its build is now complete,
and its full `VALIDATION.json` is present and passed as of 2026-10-26.

Both full outputs are ready for a technical handoff; their writers have stopped.
Upload the complete structural output directory: `manifest.json`,
`VALIDATION.json`, and every committed `batch-*` directory. Do not upload a
changing folder and label the result a reproducible snapshot. Keep code/config
hashes compatible if resuming; this cleanup did not modify pipeline code.

## Smallest optional pilot upload

For a future small semantic pilot, the files are in
[sharing/colab_pilot_paths.txt](../sharing/colab_pilot_paths.txt):

- `colab/stage5_semantic.ipynb`, opened in Colab.
- `artifacts/stage5_colab_pilot.zip`, generated manually with the bundle command
  in the stage-5 runbook and uploaded to Drive.

The bundle contains source/configuration, the tokenizer and prepared smoke
batches. It is **not created by listing its filename here**. The smoke pilot
does not depend on the running full structural build. A full semantic run can
instead bundle `chunk_inputs_stage5_full` and use a different output directory.
Neither GPU handoff needs the stage-3/4 SQLite databases or the raw Data folder.

## Review boundaries

Small copied manifests under `reports/run_registry/` are review metadata, not
checkpoints. Their referenced payloads are on Drive. Do not run resume against
the registry, edit original manifests to change status, or flatten restored
input paths. A successfully completed bounded pilot remains distinct from a
full-corpus run. Current full structural and semantic validation are recorded
in the dated closeout evidence; human QA and corpus freeze are still separate.
