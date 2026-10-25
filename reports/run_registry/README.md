# Run metadata for team review

These are small copies observed on 2026-10-25, not runnable checkpoints.
[index.json](index.json) records original source directories and observation
time. Original artifacts and all their data parts belong on Drive. A registry
copy must never replace an original manifest or be used to resume a run.

| Snapshot | Observed outcome | Review metadata |
| --- | --- | --- |
| `stage3_full_v2` | Completed provisional export; validation passed | [Manifest](stage3_full_v2/run_manifest.json), [validation](stage3_full_v2/VALIDATION.json) |
| `stage4_full_apply` | Completed provisional apply; validation passed | [Report](stage4_full_apply/REPORT.md), [validation](stage4_full_apply/VALIDATION.json) |
| `stage4_full_dry` | Completed dry-run; validation passed | [Report](stage4_full_dry/REPORT.md), [validation](stage4_full_dry/VALIDATION.json) |
| `stage4_reviewed_pilot_dry` | Completed reviewed pilot; validation passed | [Validation](stage4_reviewed_pilot_dry/VALIDATION.json) |
| `stage4_pilot_dry` | Completed 15-document dry-run; no standalone validation file present | [Manifest](stage4_pilot_dry/run_manifest.json) |
| `stage5_inputs_full` | Prepared all 855,745 documents, 1,712 batches | [Manifest](stage5_inputs_full/manifest.json) |
| `stage5_inputs_smoke` | Prepared 200 documents, 4 batches | [Manifest](stage5_inputs_smoke/manifest.json) |
| `stage5_smoke_fixed` | 200 documents / 765 chunks; validation passed | [Validation](stage5_smoke_fixed/VALIDATION.json) |
| `stage5_smoke_structure` | 200 documents / 687 chunks; validation passed | [Validation](stage5_smoke_structure/VALIDATION.json) |
| `stage5_full_structure_observed` | **Building when observed**; preserved as active work | [Observed manifest](stage5_full_structure_observed/manifest.json) |
| `stage5_tokenizer` | Local tokenizer provenance and file checksums | [Registry](stage5_tokenizer/REGISTRY.json) |

`chunks: 0` in a `prepare-chunk-inputs` manifest means that preparation has
finished before chunk generation starts; it does not mean the source is empty.

Human/clinical QA and official-ID eligibility remain pending. Technical
validation does not imply a final corpus freeze or measured retrieval quality.
The active full-chunk snapshot does not update automatically. After the owner
finishes and validates that output, replace its observed metadata through a
new dated review snapshot; do not label the current copy complete retroactively.
