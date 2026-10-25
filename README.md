# ViBioMIR — multilingual biomedical retrieval

Retrieve original Vietnamese, English and Chinese medical documents and passages
for Vietnamese queries. Evidence must remain traceable to the original source.
Code and technical documentation use English; corpus text retains its language.

## Start here

1. [Task definition](docs/BAI_TOAN_VIBIOMIR.md): competition rules, evaluation and submission.
2. [Development specification](docs/SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md): architecture, backlog and acceptance criteria.
3. [Team review and sharing](docs/TEAM_REVIEW_AND_SHARING.md): what goes on GitHub/Drive and what to review first.
4. [Documentation map](docs/README.md) and [report index](reports/README.md): one entry point for each topic.

The [original mentor summary](261012_R2_AI_S3_MentorDay1_summary.md) is preserved
in Vietnamese. See [development conventions](docs/DEVELOPMENT_CONVENTIONS.md)
for the manual-execution policy. The owner runs processing, tests and model jobs.

## Observed progress — 2026-10-25

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Audit | Full automatic audit finished: 1,195 shards / 1,105,160 rows | [Analysis](reports/data_audit/full_run_01/analysis/REPORT.md) |
| Stage 3 | `document_store_stage3_full_v2`: 913,969 versions / 855,745 document IDs; validated | [Validation snapshot](reports/run_registry/stage3_full_v2/VALIDATION.json) |
| Stage 4 | Full apply complete and validated: 1,029,307 regions across all versions; zero failed documents | [Execution evidence](reports/cleaning_stage4_rule_review_01/EXECUTION.md) |
| Stage 5 inputs | Full preparation complete: 855,745 documents / 1,712 batches | [Manifest snapshot](reports/run_registry/stage5_inputs_full/manifest.json) |
| Stage 5 smoke | Fixed: 765 chunks; structural: 687 chunks, each over 200 documents; both validated | [Run registry](reports/run_registry/README.md) |
| Stage 5 full structural chunks | **Running when observed**; do not move, delete or upload as a completed result yet | [Observed metadata](reports/run_registry/stage5_full_structure_observed/manifest.json) |
| Semantic / ICD / indexes / retrieval | Semantic code and Colab handoff supplied; no completed GPU run recorded; later retrieval stages pending | [Stage-5 runbook](docs/CHUNKING_STAGE5.md) |

These are technical processing results. Human/clinical QA, official-ID
eligibility, corpus freeze and retrieval evaluation remain pending. Published URL
counts are reference snapshots, not an acquisition quota; recrawling is deferred.

## Run instructions

Use the runbook for the operation you need; commands are not duplicated here.
Run tests and a small pilot before a new full run.

| Operation | Canonical guide | Compute |
| --- | --- | --- |
| Earlier EDA and official-reference snapshot | [Exploration](docs/EXPLORATION.md) | Local CPU |
| S00 audit | [Data audit](docs/DATA_AUDIT.md) | Local CPU |
| Versioned import and metadata | [Stage 3](docs/DOCUMENT_STORE_STAGE3.md) | Local CPU |
| Offline recovery and reviewed cleaning | [Stage 4](docs/CLEANING_STAGE4.md) | Local CPU |
| Prepare inputs, fixed/structural chunks, validation | [Stage 5](docs/CHUNKING_STAGE5.md) | Local CPU; tokenizer only |
| Semantic boundary pilot | [Stage-5 Colab notebook](colab/stage5_semantic.ipynb) | CUDA GPU |

## Repository layout

```text
configs/        Shipped settings and selected pilot IDs/shards
vibiomir/       Stage 3–5 implementation and CLI
exploration/    Earlier EDA, audit and audit analysis
scripts/        Rule-review, checkpoint and Colab-bundle helpers
tests/          Offline fixtures and optional real-tokenizer checks
colab/          GPU notebook; no CPU-only notebook
docs/           Task/specification and canonical runbooks
reports/        Small review evidence on GitHub; large stores on Drive
sharing/        Explicit GitHub, Drive and Colab upload lists
Data/           Original collected/official data — local and Drive
artifacts/      Successful/generated outputs — local and Drive
local_assets/   Original dataset ZIPs and mentor videos — local and Drive
```

`.gitignore` separates source/review material from runtime data, databases,
weights, caches and local environments. Successful artifact paths stay stable
because manifests and resume checks reference them. Old failed stage-3 stores
were removed; [the cleanup record](reports/maintenance/repository_cleanup_20261025/README.md)
records evidence and original-asset relocations.
