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
for the manual-execution policy and the authorized baseline closeout exception.

## Observed progress — 2026-10-30

**Submitted baseline:** the owner reported Dashboard `FINAL_SCORE=0.0916`,
document macro F2 **0.1323**, and chunk macro F2 **0.0509**. The
[baseline score and optimization record](reports/baseline_submission_20261030/README.md)
binds the exact local JSON/ZIP and configuration, preserves all seven metrics,
and lists experiments to distinguish corpus, candidate and selection losses.
The scored phase/submission ID were not provided. No optimization gain or
human-reviewed acceptance is implied by this observation.

| Stage | Observed state | Evidence |
| --- | --- | --- |
| Audit | Full automatic audit finished: 1,195 shards / 1,105,160 rows | [Analysis](reports/data_audit/full_run_01/analysis/REPORT.md) |
| Stage 3 | `document_store_stage3_full_v2`: 913,969 versions / 855,745 document IDs; validated | [Validation snapshot](reports/run_registry/stage3_full_v2/VALIDATION.json) |
| Stage 4 | Full apply complete and validated: 1,029,307 regions across all versions; zero failed documents | [Execution evidence](reports/cleaning_stage4_rule_review_01/EXECUTION.md) |
| Stage 5 inputs | Full preparation complete: 855,745 documents / 1,712 batches | [Manifest snapshot](reports/run_registry/stage5_inputs_full/manifest.json) |
| Stage 5 smoke | Fixed: 765 chunks; structural: 687 chunks, each over 200 documents; both validated | [Run registry](reports/run_registry/README.md) |
| Stage 5 full structural chunks | Full local validation passed: 855,745 documents / 5,512,624 chunks | [Validation](reports/run_registry/stage5_full_structure_20261026/VALIDATION.json) |
| Stage 5 full semantic chunks | Full local validation passed: 855,745 documents / 9,036,279 chunks; report matches the original Colab full validation | [Validation](reports/run_registry/stage5_full_semantic/VALIDATION.json) |
| S06 rules | 855,745 document annotations; existing full validation and snapshot seals verified | [Baseline handoff](docs/STAGES6_8_BASELINE_HANDOFF.md) |
| S07 BM25/TF-IDF | Full queries complete: 1,200 queries / 600,000 validated candidates per method; original 5,512,624-chunk index retained | [Query completion](reports/lexical_stage7_20261029/README.md) |
| S08 BGE-M3 | 5,512,624 vectors indexed; all 1,200 queries have 500 validated dense candidates | [Execution evidence](reports/stage678_baseline_20261029/README.md) |
| S09 reranked hybrid | Two owner-run Colab workers complete; all 1,200 reranked results validated, 24,000 document predictions / 41,086 chunks | [Result snapshot](reports/run_registry/stage9_hybrid_reranked_20261030/VALIDATION.json) |
| S10 silver pilot | 30 assistant-authored questions / 60 documents / 25 configurations; real CPU models, source validation passed; human/clinical and full-corpus acceptance pending | [Pilot closeout](reports/stage10_silver_20261029/CLOSEOUT.json) |
| Provisional submission | Full 1,200-query JSON/ZIP; local official ID, schema and integrity checks passed; Dashboard scores reported by owner | [Scored baseline](reports/baseline_submission_20261030/README.md) |

Human/clinical QA, a final corpus freeze and full reviewed retrieval evaluation
remain pending. Exported IDs were checked against the saved official allowlist;
the later Dashboard result is recorded separately from historical validations. Published URL
counts are reference snapshots, not an acquisition quota; recrawling is deferred.

For stage-5 output paths, full validation evidence, cleanup and GitHub/Drive
handoff, read [Stage-5 results and handoff](docs/STAGE5_RESULTS_AND_HANDOFF.md).

**Current handoff:** [S06–S08 baseline results](docs/STAGES6_8_BASELINE_HANDOFF.md)
records completed technical checks. The [later S07 completion](docs/LEXICAL_RETRIEVAL_STAGE7.md)
provides the lexical branch for hybrid S09.
The authorized closeout executed 40 tests: 39 passed, one optional mBERT CPU
pooling test skipped because PyTorch is absent. S07 query completion added seven passing fixture checks and 48 real-index SQL
comparisons. Both lexical methods now have full validated candidate sets.

S10 completed bounded fixed/structural/semantic, ICD, mBERT/BGE and reranker
comparisons using silver labels. Human/clinical QA, ICD calibration/audit,
full-corpus reviewed comparisons and production ANN recall remain open.
The Dashboard result above is separate from the silver proxy metrics. Existing QA and
experiment tools remain available in [QA and experiments](docs/STAGE5_QA_AND_EXPERIMENTS.md).

**Current baseline:** reranked results are at
`artifacts/results_stage9_hybrid_reranked/predictions.json`; the exact
provisional submission is under
`artifacts/submissions/stage9_hybrid_reranked_provisional/`.
The [S09 runbook](docs/RETRIEVAL_STAGE9.md) retains reproduction/resume commands;
no repeat GPU job is needed to inspect these outputs. The
[closeout checks](reports/baseline_submission_20261030/TESTS.json) reran 100
offline tests with zero failures, errors or skips. Frozen production code and
result hashes were verified without rebuilding indexes or embeddings.

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
| Hybrid candidate fusion, reranking, document/chunk results | [Stage 9](docs/RETRIEVAL_STAGE9.md) | CPU baseline/export; reranking uses Colab CUDA |
| Global BM25/TF-IDF and six-worker dense retrieval | [Stages 7–8](docs/RETRIEVAL_STAGES7_8.md) | Local CPU and up to six CUDA sessions, independently |

## Repository layout

```text
configs/        Shipped settings and selected pilot IDs/shards
vibiomir/       Stage 3–10 implementation and CLI
native/         Optional C++ helper for the compact S07 postings scan
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
