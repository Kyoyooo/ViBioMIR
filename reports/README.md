# Results and review evidence

The root [README](../README.md) is the progress entry point. This directory
retains measured results and review evidence; it does not contain alternative
instructions for running the pipeline.

| Evidence | Read first | Role |
| --- | --- | --- |
| Full audit and analysis | [Analysis report](data_audit/full_run_01/analysis/REPORT.md) | Main full-corpus interpretation |
| Processing roadmap | [Reviewed processing plan](data_audit/full_run_01/analysis/PROCESSING_PLAN.md) | Canonical post-audit processing plan |
| Detailed processing decisions | [Decisions](data_audit/full_run_01/analysis/PROCESSING_DECISIONS.md) | Reasons behind the roadmap |
| Full audit accounting | [Automatic report](data_audit/full_run_01/REPORT.md) | Raw automatic counts, with validation JSON alongside |
| Audit pilot | [Pilot validation](data_audit/pilot_stage1/PILOT_VALIDATION.md) | Successful selected-shard tooling check; not a full audit |
| Older EDA | [Historical EDA report](eda/REPORT.md) | Earlier exploratory baseline and official-reference comparison |
| Stage-4 cleaning | [Execution evidence](cleaning_stage4_rule_review_01/EXECUTION.md) | Completed apply, validation and before/after comparison |
| Cleaning rule decisions | [Rule review](cleaning_stage4_rule_review_01/DECISIONS.md) | Approved/disabled rules and real-source counterexamples |
| S06–S08 baseline closeout | [Executed checks and results](stage678_baseline_20261029/README.md) | Verified rules/lexical index, full dense index and 600,000 candidates; lexical queries deferred |
| Stage-3–8 metadata | [Run registry](run_registry/README.md) | Small manifests/validation snapshots readable without Drive data |
| Full semantic recovery, 2026-10-26 | [Recovery and completion checklist](maintenance/stage5_download_recovery_20261026/README.md) | Restored 4 ZIPs, 17 missing files and 5 loose files; integrity checks passed; review packet and pending acceptance |
| Stage-5 full local validation and closeout | [Execution evidence](stage5_validation_20261026/README.md) | Structural and semantic: 14,548,903 chunks passed; tests, cleanup record and handoff |
| Repository maintenance | [Cleanup record](maintenance/repository_cleanup_20261025/README.md) | Exactly what was removed, retained and relocated |

`data_audit/*/processing_plan.md` is an automatically generated **draft**.
Use `data_audit/full_run_01/analysis/PROCESSING_PLAN.md` for the reviewed roadmap;
the generated drafts remain unchanged as part of their original audit outputs.
Likewise, pilot reports and full reports describe different scopes and are not
duplicate runs to combine or delete.

GitHub contains the small text/JSON/CSV/SVG/log evidence in this directory.
SQLite and Parquet files are ignored by Git and belong on Drive. Keep original
paths when restoring Drive files, because provenance records reference them.
