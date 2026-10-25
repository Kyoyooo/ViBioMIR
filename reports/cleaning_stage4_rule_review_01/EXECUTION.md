# Authorized stage-4 execution

The owner explicitly authorized the assistant to resolve the reviewed-config
blocker and run this part of stage 4 on 2026-10-25. The general project policy
for later deliveries remains manual execution unless separately authorized.

## Completed checks before full apply

- Original full dry-run: owner-completed and validated, covering 913,969 parent
  versions and 855,745 documents.
- Rule review: [decisions](DECISIONS.md), [source evidence](SOURCE_CASES.jsonl),
  [technical checks and sampling provenance](SUMMARY.json).
- Six enabled approved rules; two disabled proposals. Vinmec revision 2 uses
  exact verified footer alternatives and retains the appended clinical heading.
- [Functional tests](TESTS.log): 36 passed.
- [Checkpoint IO tests](CHECKPOINT_IO_TESTS.log): 2 passed.
- [Reviewed pilot run](PILOT.log): 185 documents / 201 versions completed.
- [Reviewed pilot validation](PILOT_VALIDATION.log): passed, including source
  slices and pinned parent checksums. Human/clinical QA remains pending.

## Full apply and follow-up

Output: `artifacts/cleaning_stage4_full_apply`.

The first invocation is recorded in [APPLY.log](APPLY.log). It was interrupted
after about 30,000 committed documents for checkpoint IO tuning. The resumed
invocation uses `scripts/run_reviewed_stage4_apply.py` with WAL/NORMAL, recorded
in [APPLY_RESUME.log](APPLY_RESUME.log). No extraction/rule engine source was
changed and the original completed input stores remain intact.

**Final status: complete.** Finished at 2026-10-25 21:35:37 Asia/Ho_Chi_Minh
(14:35:37 UTC). Full apply, the standard validator and the independent
cross-run comparison all completed successfully.

| Result | Observed value |
| --- | ---: |
| Parent versions processed | 913,969 |
| Documents completed | 855,745 |
| Documents failed | 0 |
| Evidence regions exported | 1,029,307 |
| Transformation records, including vetoes | 186,208 |
| Applied removals | 147,803 |
| Removed source characters | 7,535,268 |
| Original proposals retained by the revised review profile | 3,676 |
| Additional source characters preserved versus the original dry-run | 612,750 |
| Specific heading counterexample spans preserved verbatim | 5 |

## Final verification evidence

- [Completion summary](COMPLETION.json): full-scope completion, validator and
  comparison results.
- [Full validator output](APPLY_VALIDATION.log): passed on all 913,969 versions
  and 855,745 selections, including source slices, parent store checksums,
  reproducibility, metadata and source partition checks.
- [Cross-run comparison](CROSS_RUN_CHECK.json): exactly 147,803 expected and
  actual removal events, with matching character totals for every enabled rule.
  No removal was introduced outside the reviewed subset of the validated
  original dry-run proposals.
- [Real-source counterexample preservation](COUNTEREXAMPLE_PRESERVATION.json):
  all five Thanh Nien/Vinmec heading spans remain verbatim in the apply output.
- The output's `VALIDATION.json`, `run_manifest.json`, `REPORT.md`,
  `processing_summary.json` and Parquet parts are in
  `artifacts/cleaning_stage4_full_apply/`.

The original stage-3 store and full dry-run were preserved. This is completed
technical processing/validation, not independent clinical/main-content QA or a
final working-corpus freeze. The output intentionally retains
`ready_for_retrieval: false`, `selection_is_final: false` and human QA pending.
