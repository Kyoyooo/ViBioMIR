# Repository cleanup — 2026-10-25

The owner authorized removal of failed old runs and repository reorganization
while full stage-5 structural chunking was running. No pipeline, model job or
test was launched for this maintenance operation.

## Removed failed stores

| Removed directory | Verified reason | Successful replacement |
| --- | --- | --- |
| `artifacts/document_store_stage3_full` | `state=partial`, zero imported records, no completed export | `artifacts/document_store_stage3_full_v2` |
| `artifacts/document_store_stage3_pilot` | `state=partial`, zero imported records, no completed export | `artifacts/document_store_stage3_full_v2` |

Their manifests and reports were preserved in the two sibling subdirectories
before deleting their unusable SQLite files. The removed directories accounted
for **495,411,521 logical bytes**, approximately 472.5 MiB, before subtracting
the small retained evidence copies. Actual filesystem space released may differ.
Exclusive nonblocking locks were acquired before removing either old store.
Python bytecode caches in the four source/test packages were also removed.

## Preserved working data

- All of `Data/`, successful stage-3 `full_v2`, all completed stage-4 outputs,
  successful audit/pilot outputs, completed stage-5 input batches and smoke runs.
- The **building** `artifacts/chunks_stage5_full_structure` output, its pending
  directories, tokenizer and all input paths.
- Successful full dry-run remains available for before/after comparison; it is
  not an error run or redundant copy of the revised apply output.
- The 15-document stage-4 pilot was complete, with no validation file present;
  it was retained without claiming independent validation.

Code, configurations, requirements and stable upstream/input control files were
hashed before and after the filesystem cleanup: 52 protected files matched.
[protected_files.json](protected_files.json) contains those hashes.
No source module or pipeline configuration was refactored, moved or renamed;
their hashes are part of resume compatibility.

## Relocated original assets

| Old root location | New local/Drive location |
| --- | --- |
| `ViBioMIR-20261023T181256Z-1-001.zip` | `local_assets/archives/ViBioMIR-20261023T181256Z-1-001.zip` |
| `ViBioMIR-20261023T181256Z-1-002.zip` | `local_assets/archives/ViBioMIR-20261023T181256Z-1-002.zip` |
| `video.zip` | `local_assets/mentor_video/video.zip` |
| `video/` | `local_assets/mentor_video/video/` |

These were filesystem renames, not deletions or re-encodings. Pipeline paths
under `Data/`, `reports/data_audit/` and `artifacts/` stayed unchanged.

## Documentation organization

The root README is now a short entry point. Older EDA instructions moved to
`docs/EXPLORATION.md`; stage runbooks remain canonical for commands. A docs map,
report index, small run registry and GitHub/Drive sharing lists explain which
files to review. Original mentor material and generated historical reports
were preserved. Automatically generated draft plans are clearly distinguished
from the reviewed processing plan.

See [cleanup.json](cleanup.json) for the exact deletion/move inventory and
[the sharing guide](../../../docs/TEAM_REVIEW_AND_SHARING.md) for handoff.
