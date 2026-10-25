# Stage-4 rule review before the authorized apply run

Date: 2026-10-25. Reviewer: assistant, explicitly authorized by the owner to
review this configuration and execute this stage-4 apply/validation task.
Scope: extraction structure, source regions and processing behavior. This is
not independent human/clinical review or medical fact verification.

The owner already completed and validated `artifacts/cleaning_stage4_full_dry`:
913,969 versions, 855,745 documents and no failed documents. The actual reviewed
config still had all eight rules enabled as proposals. No apply output existed.

## Review method and artifacts

- Ran the stage-4 suite: the original 33 tests passed.
- `scripts/review_stage4_rules.py` checked all 151,479 proposed removals against
  their exact rule patterns and the existing protected-signal guards. Every
  proposal passed those technical checks. This alone does not prove UI status.
- Collected 159 deterministic reservoir examples across rule/decision groups
  with exact source slices and before/after context. Inspected source-context
  examples, all distinct accepted Vinmec/Baonghean templates, Chinese feed
  structure and specific counterexamples. This is targeted structural review,
  not a population clinical-error estimate.
- `SUMMARY.json` records counts, seed and source manifest/validation checksums;
  `SOURCE_CASES.jsonl` contains the reproducible source-context packet.
- `CONFIG_BEFORE.json` preserves the owner's config before this intervention.
  `VINMEC_APPROVED_TEMPLATES.json`, `VINMEC_HEADING_COUNTEREXAMPLES.json` and
  `THANHNIEN_COUNTEREXAMPLES.json` retain concrete supporting evidence.
- The reviewed-profile verification pilot selects 185 original record IDs,
  covering all collected cases, distinct Vinmec templates, counterexamples and
  the original targeted pilot. Its actual scope/counts are recorded by its run.

## 120ask-complaint-control

Approve unchanged. All 111,263 accepted spans are the standalone site control
`投诉`, with or without a following newline. Source-context checks place these
between question/answer or related-widget regions. Clinical questions, answers
and advice remain untouched. Existing short-prefix and other vetoes remain.

## familydoctor-feedback-control

Disable for this apply profile; leave proposal status. The full dry-run had
10,188 vetoed matches and no accepted removals, mostly header-position controls.
There is no demonstrated accepted population to promote here. No body is deleted.

## cnkang-related-tail

Approve unchanged for this pinned corpus/profile. The 1,277 accepted removals
contain 153 distinct explicit related-article tails. Source-context examples
follow the completed article. Structural checks found short feed/title lines
(maximum 73 characters), and samples preserve the preceding medical paragraphs.
The protective/size vetoes remain and leave ambiguous tails unchanged.

## familydoctor-related-tail

Approve unchanged. Inspect the sole accepted case (doc 3978196): an explicit
latest-articles marker followed by a list of promotional hospital/story titles.
It is a feed tail, not the article body. The other 10,213 original vetoes are
not promoted into removals; the existing guards remain in force.

## vov-recaptcha-footer

Approve unchanged. All 8,614 accepted spans share the exact reCAPTCHA/Google
Privacy Policy/Terms of Service footer line. Source-context samples preserve
the substantive article and existing clinical cautions. Removing this line
does not claim that every other widget or related story has been cleaned.

## vinmec-booking-footer

Approve revision 2 with a narrower pattern, rather than the original `.+` tail.
There are 14 distinct raw accepted spans, including newline variants. After
stripping line edges, 11 verified booking/app-promotion templates are approved
as exact full-line alternatives. The observed line containing an appended
`Lưu ý khi thực hiện:` heading is excluded and remains in source evidence.
No new deletion templates are introduced. This revised config needs its own
dry-run pilot and validation before the full apply invocation.

## baonghean-inline-related-link

Approve unchanged. Inspected all 32 distinct accepted link texts (109 accepted
occurrences) and surrounding examples. They are isolated arrow-prefixed related
news teasers, including an unrelated political teaser inside medical reporting.
Removing these spans preserves distinct source regions around the gap.

## thanhnien-related-link

Disable; leave proposal status. The broad `>>` pattern has a concrete false
positive: `>> Sự thực là:` appears repeatedly as the main explanation heading in
doc 589690, between pregnancy/childcare misconceptions and substantive article
paragraphs. The original guard does not detect this heading because it contains
no number/unit/negation token. Preserve this entire rule's matches until a more
precise, separately tested rule distinguishes actual links from body headings.

## Execution status

The reviewed configuration has six enabled approved rules and two disabled
proposals. The general engine, source inputs and the original dry-run artifacts
are unchanged. Main-content/clinical QA and all final-corpus claims remain
pending. Execution logs, manifests and validator reports record subsequent
pilot/apply runs; do not infer their completion from this decision document.

The reviewed-profile test suite passed 36 tests, including regressions for the
Vinmec appended heading and Thanh Nien body heading. The reviewed pilot completed
185 documents / 201 versions and passed source-map validation. The original
apply invocation committed about 30,000 documents before interruption for IO
tuning. Execution resumed from its recoverable checkpoint using
`scripts/run_reviewed_stage4_apply.py`, with SQLite WAL, NORMAL synchronization
and 1,000-page auto-checkpoints. Two additional connection/resume tests passed.
This modifies storage IO only; the rule/extraction engine and logical outputs
are unchanged. WAL transactions are atomic; recent commits can be lost after an
OS/power failure and are reprocessed on resume. Logs and `EXECUTION_SETTINGS.json`
record the actual invocation/result. This owner-authorized execution is an
exception for this incident; the default manual-execution policy remains.
