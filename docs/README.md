# Documentation map

Start with the root [README](../README.md) for current progress and the
[team sharing guide](TEAM_REVIEW_AND_SHARING.md) for GitHub/Drive handoff.
Use one document for each purpose rather than copying its instructions elsewhere.

| Question | Canonical document |
| --- | --- |
| What are the competition rules, scoring and submission format? | [Task definition](BAI_TOAN_VIBIOMIR.md) |
| What should the complete system implement, and how is it accepted? | [Development specification](SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md) |
| What did the mentor originally say? | [Original mentor summary](../261012_R2_AI_S3_MentorDay1_summary.md), preserved in Vietnamese |
| Which languages and manual-execution conventions apply? | [Development conventions](DEVELOPMENT_CONVENTIONS.md) |
| How do I reproduce the older exploratory analysis? | [Exploration guide](EXPLORATION.md) |
| How do I run the S00 audit? | [Audit runbook](DATA_AUDIT.md) |
| How do I import versioned documents? | [Stage-3 runbook](DOCUMENT_STORE_STAGE3.md) |
| How do I recover/clean documents and review rules? | [Stage-4 runbook](CLEANING_STAGE4.md) |
| How do I prepare/chunk/validate locally and run semantic chunks on Colab? | [Stage-5 runbook](CHUNKING_STAGE5.md) |
| How do I move an unfinished full chunk run to Colab and reuse its batches? | [Full stage-5 Colab continuation](CHUNKING_STAGE5_COLAB_FULL.md) |
| How do I run semantic chunks on two GPUs and merge their results? | [Two-Colab semantic workflow](SEMANTIC_STAGE5_TWO_COLABS.md) |
| What passed full stage-5 validation, and what should the team upload/review? | [Results and handoff](STAGE5_RESULTS_AND_HANDOFF.md) |
| What must run now to proceed to S06, and which experiments are deferred? | [Current S05-to-S06 route](STAGE5_QA_AND_EXPERIMENTS.md#current-route-to-s06--owner-decision-2026-10-27) |
| What is complete in S06–S08, and what can S09 consume now? | [Baseline results and handoff](STAGES6_8_BASELINE_HANDOFF.md) |
| How do I build/validate ICD chapter labels and run optional semantic tagging on Colab? | [S06 ICD runbook](ICD_STAGE6.md) |
| How do I complete fast exact BM25/TF-IDF queries on the existing index? | [S07 compact search](LEXICAL_RETRIEVAL_STAGE7.md) |
| How do I run CPU lexical retrieval and dense embedding on six Colab GPUs in parallel? | [S07/S08 parallel runbook](RETRIEVAL_STAGES7_8.md) |
| Which S09 CPU outputs exist and what remains for owner-run Colab? | [S09 execution record](../reports/stage9_execution_20261029/README.md) |
| How do I get ranked documents/chunks from hybrid candidates and run the GPU reranker? | [S09 retrieval and result runbook](RETRIEVAL_STAGE9.md) |
| How do I complete QA, the full fixed baseline and controlled local comparisons? | [QA and experiments](STAGE5_QA_AND_EXPERIMENTS.md) |
| How should humans label clinical quality, questions and original gold spans? | [Annotation guide](STAGE5_ANNOTATION_GUIDE.md) |
| How were the misplaced semantic downloads recovered? | [Download recovery record](../reports/maintenance/stage5_download_recovery_20261026/README.md) |
| Which results and decisions support the current state? | [Report index](../reports/README.md) |
| What scored on the Dashboard, and which optimization should be tested next? | [Frozen submission baseline](../reports/baseline_submission_20261030/README.md) |
| What did the bounded S10 silver experiment establish? | [Pilot closeout](../reports/stage10_silver_20261029/CLOSEOUT.json), [comparison table](../reports/stage10_silver_20261029/SUMMARY.csv); human/full-corpus acceptance remains open |
| What was removed or moved during repository cleanup? | [Cleanup record](../reports/maintenance/repository_cleanup_20261025/README.md) |

The task definition, implementation specification and mentor source overlap in
subject matter but serve different roles. They remain separate to retain
organizer rules, project decisions and source provenance. Generated reports are
historical measurements; changing a runbook does not update those measurements.
