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

## Observed progress — 2026-10-26

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Audit | Full automatic audit finished: 1,195 shards / 1,105,160 rows | [Analysis](reports/data_audit/full_run_01/analysis/REPORT.md) |
| Stage 3 | `document_store_stage3_full_v2`: 913,969 versions / 855,745 document IDs; validated | [Validation snapshot](reports/run_registry/stage3_full_v2/VALIDATION.json) |
| Stage 4 | Full apply complete and validated: 1,029,307 regions across all versions; zero failed documents | [Execution evidence](reports/cleaning_stage4_rule_review_01/EXECUTION.md) |
| Stage 5 inputs | Full preparation complete: 855,745 documents / 1,712 batches | [Manifest snapshot](reports/run_registry/stage5_inputs_full/manifest.json) |
| Stage 5 smoke | Fixed: 765 chunks; structural: 687 chunks, each over 200 documents; both validated | [Run registry](reports/run_registry/README.md) |
| Stage 5 full structural chunks | Full local validation passed: 855,745 documents / 5,512,624 chunks | [Validation](reports/run_registry/stage5_full_structure_20261026/VALIDATION.json) |
| Stage 5 full semantic chunks | Full local validation passed: 855,745 documents / 9,036,279 chunks; report matches the original Colab full validation | [Validation](reports/run_registry/stage5_full_semantic/VALIDATION.json) |
| ICD / indexes / retrieval | Not implemented; full fixed baseline and stage-5 acceptance/QA still pending | [Stage-5 runbook](docs/CHUNKING_STAGE5.md) |

These are technical processing results. Human/clinical QA, official-ID
eligibility, corpus freeze and retrieval evaluation remain pending. Published URL
counts are reference snapshots, not an acquisition quota; recrawling is deferred.

For stage-5 output paths, full validation evidence, cleanup and GitHub/Drive
handoff, read [Stage-5 results and handoff](docs/STAGE5_RESULTS_AND_HANDOFF.md).

**2026-10-27 source delivery:** tools and fixtures for human/clinical QA, a
resumable full fixed baseline, intrinsic/parameter comparisons and a human-labeled
local retrieval benchmark are available in
[QA and experiments](docs/STAGE5_QA_AND_EXPERIMENTS.md). Their new tests and
pipelines await owner execution; no new quality scores or clinical approval
are claimed. Existing observed processing results above remain unchanged.

**Current execution priority, 2026-10-27:** move directly to S06 ICD implementation
using the validated structural/semantic inputs. No further stage-5 run is needed
for unchanged inputs. Full fixed, extended QA and comparison campaigns are
deferred until after S07–S09 retrieval; final acceptance stays pending. Follow
the [current handoff route](docs/STAGE5_QA_AND_EXPERIMENTS.md#current-route-to-s06--owner-decision-2026-10-27).

**S06 source delivery, 2026-10-27:** chapter-level ICD rules, semantic/combined
annotation, source-backed evidence, resumable batches, technical validator and
a Colab GPU handoff are implemented in source. Start with the
[S06 runbook](docs/ICD_STAGE6.md): local rules + validation is the fast route
to S07; CUDA semantic/combined execution is optional now. Tests/jobs remain
owner-run and unexecuted by the assistant. Clinical audit/calibration is deferred;
ICD boosts and hard filters remain disabled. No corpus labels are claimed yet.

**S07/S08 source delivery, 2026-10-27:** disk-backed BM25/TF-IDF for local CPU
and dense encoding across up to six independent Colab GPU workers are supplied.
Both default to the same validated structural chunk snapshot. See
[parallel S07/S08 instructions](docs/RETRIEVAL_STAGES7_8.md) for fixture checks,
six worker bundles, resume/merge, IVF-PQ and candidate generation. These new
jobs/tests have not been run by the assistant; final quality checks stay deferred.

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
| Resume full chunks on Colab; separate full semantic experiment | [Full Colab handoff](docs/CHUNKING_STAGE5_COLAB_FULL.md) | Structural: parallel CPU; semantic: CUDA |
| Split remaining semantic chunks across two Colabs and merge | [Two-Colab semantic workflow](docs/SEMANTIC_STAGE5_TWO_COLABS.md) | One CUDA GPU per shard |
| Human/clinical QA, full fixed baseline and local comparisons | [Stage-5 QA and experiments](docs/STAGE5_QA_AND_EXPERIMENTS.md) | Local CPU/humans; new semantic pilots use CUDA |
| ICD chapter metadata; rules and optional semantic/combined | [Stage 6](docs/ICD_STAGE6.md) | Rules: CPU; semantic/combined: Colab CUDA |
| Global BM25/TF-IDF and six-worker dense retrieval | [Stages 7–8](docs/RETRIEVAL_STAGES7_8.md) | Local CPU and up to six CUDA sessions, independently |

## Repository layout

```text
configs/        Shipped settings and selected pilot IDs/shards
vibiomir/       Stage 3–8 implementation and CLI
taxonomy/       Versioned ICD chapter topics and multilingual seed phrases
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
