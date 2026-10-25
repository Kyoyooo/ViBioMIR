# Delivery 4: offline recovery and source-mapped cleaning

**GPU required: no. Local CPU only.** The assistant supplies code, tests,
configuration and these manual instructions. The initial code delivery was
not executed by the assistant. The owner subsequently authorized the assistant
to review the full dry-run results and execute this apply/validation incident;
see [the execution record](../reports/cleaning_stage4_rule_review_01/EXECUTION.md)
for actual results and logs. No Colab notebook is needed or supplied. Nothing in
the existing stage-3 outputs is regenerated.

## 1. Start from the results you already produced

The owner completed and validated
`artifacts/document_store_stage3_full_v2/`. Its saved report and validation show:

| Observed stage-3 metric | Value |
| --- | ---: |
| Input records | 1,105,160 |
| Unique document versions | 913,969 |
| Distinct document IDs | 855,745 |
| Exported legacy blocks | 28,320,695 |
| Invalid-ID records | 0 |
| Source checksum validation | Passed |
| Final selection / independent clinical QA | Pending |

The saved metadata report counts **versions**, not independent documents:
abstract 0; authors 565; category 5,376; publication date 5,453; updated date 183;
title 911,146; description 812,091. There are 792 title conflicts and 164
description conflicts. These are observed inputs, not newly measured stage-4
results. Sparse metadata stays unknown; stage 4 never substitutes descriptions
for abstracts or patient/crawl dates for publication dates.

Use the **full_v2** store, not the earlier failed `document_store_stage3_full` or
the old incomplete pilot. Keep the stage-3 directory intact, including its
`document_store.sqlite3`, `run_manifest.json`, `VALIDATION.json`, `.store.lock`
and exports. Do not rebuild or resume stage 3 merely to start stage 4.

## 2. Implemented scope

This delivery implements the pipeline for D4-01–D4-06 in the
[processing plan](../reports/data_audit/full_run_01/analysis/PROCESSING_PLAN.md):

- Inspect **every parent version**, including alternatives that stage 3 did not
  select. Keep a derived decision for each. Compare provisional parent choices
  before and after processing, without selecting by longest text/latest crawl.
- Retain existing source text when it is not a narrowly identified failed
  payload. When missing/failed, try stored blocks; then try checksum-verified
  local HTML with a single explicit article root, or a single main root when
  no article root exists. Insufficient/ambiguous payload stays unavailable or
  quarantined. No recrawl or invented medical text.
- Identify exact error placeholders, exact gate-only pages, and strongly
  indicated image/binary content. A replacement character alone is a review
  signal; it is never guessed/repaired. Captcha mentions within useful articles
  do not reject the whole document.
- Propose domain-specific removal of standalone controls, booking footers,
  related-story links and explicit related-feed boundaries. Retain each removed
  slice, its offsets and rule revision. Unreviewed rules default to proposals.
- Report repeated lines, CJK-aware title/body overlap, retained legacy flags,
  vetoed removals, recovery methods and per-domain changes. Repetition and
  title mismatch never independently delete medical content.
- Produce per-region normalized comparison views from the unchanged source.
  Do not normalize or rewrite the evidence field itself. Preserve original
  negation, numbers, units, headings, table text and Q&A in retained regions.

The initial rulebook targets eight explicitly scoped cases from audit findings.
Exact patterns and thresholds are **engineering proposals**, not rules proven
across a domain. Unsupported menus, feeds, embedded teasers and ambiguous main
content remain review work. Rules do not infer medical relevance or eliminate
entire domains. This delivery does not establish a universally clean corpus or
the spec's 98% main-content gate.

The subsequently authorized review/apply run is complete; see the execution
record linked above. `artifacts/cleaning_stage4_full_apply/VALIDATION.json`
records a passed full validator on 913,969 versions / 855,745 documents.
The independent comparison also passed: 147,803 applied removals exactly match
the reviewed subset of the original proposals. The reviewed config enables six
rules, disables the broad Thanh Nien link rule and unused familydoctor feedback
rule, and restricts Vinmec revision 2 to 11 verified footer templates. Do not
replace it with blanket approval of the original proposal config.

## 3. Install and test manually

Run from the project root with the existing Linux environment (Python 3.10+):

```bash
python -m pip install -r requirements-stage4.txt
python -m unittest discover -s tests -p 'test_stage4*.py' -v
```

The tests use temporary synthetic data and the actual stage-3 importer/validator.
They cover source-preserving region splits, medical counterexamples, UI-rule
scope and vetoes, CJK/mixed text, binary/soft-404 handling, blocks/HTML recovery,
raw-shard hashes, SQL persistence, parent immutability, resumability, interrupted
transactions and output corruption. They were initially supplied without
execution. The subsequently authorized run passed 36 functional tests and two
additional WAL/checkpoint tests; see the execution record for the actual logs.

To check shared code and the previous insertion regression as well:

```bash
python -m unittest discover -s tests -v
```

Investigate failures before a corpus run. Passing fixtures does not establish
full-data main-content or clinical accuracy.

### HTML parser regression in the initial delivery

The initial parser named its absolute-position helper `offset()`, colliding
with the integer `offset` attribute owned by `HTMLParser`'s base class. This
caused `TypeError: 'int' object is not callable` in HTML recovery. In a pipeline
test, the document failure prevented exports and surfaced later as
`KeyError: 'tables'`. The helper is now `_source_offset()` at every call site.
A regression test covers inherited column tracking, multiline/fragmented feeds
and raw entity spans; the pipeline test also asserts successful completion
before reading its exports. The subsequent owner-authorized functional run
passed these regression tests; its logs are linked in the execution record.
The command above remains available for future manual runs.

If you only ran synthetic tests, no corpus checkpoint needs migration. If you
already created a stage-4 pilot/full output using the earlier implementation,
start in a fresh output directory after this fix; its code fingerprint will
reject `--resume` across the change. Keep the completed stage-3 store unchanged.

## 4. Representative dry-run

The pilot list contains 15 record IDs from the existing source-review findings.
Each ID selects **all versions of its document**, allowing version comparisons.
This covers Q&A, news, Chinese articles, binary failures, metadata-only sources,
clinical cases and caution paragraphs. It is a targeted engineering pilot,
not a random or representative error-rate estimate.

```bash
python -m vibiomir clean-documents \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data \
  --output-dir artifacts/cleaning_stage4_pilot_dry \
  --config configs/cleaning_stage4.json \
  --record-ids-file configs/cleaning_pilot_record_ids.json \
  --mode dry-run
```

An optional `--preflight-only` checks the inputs/selection/fingerprint and
creates no output directory. It is different from `--mode dry-run`, which
performs processing and writes proposed regions/changes for inspection.
`--max-documents 20` is available for a cheap-scope smoke run; it chooses the
first sorted document IDs and is **not** the targeted pilot. Do not combine the
two subset options.

After `complete_dry_run`, validate the pilot:

```bash
python -m vibiomir validate-cleaned \
  --output-dir artifacts/cleaning_stage4_pilot_dry \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data
```

Read `REPORT.md`, `processing_summary.json`, `review_samples.jsonl` and the full
exported transformation/region rows. Compare with the parent version and source
row. Samples have bounded **preview-only** excerpts; offsets/hashes describe
the complete source slices in the Parquet exports, not the truncated previews.
The queues cover changed, vetoed, recovered, unavailable/quarantined and retained
counterexamples. They are deterministic, can overlap, and are not a statistical
sample or relevance labels.

## 5. Full dry-run and progress checkpoints

After the pilot tests/checks, run without subset options into a new directory:

```bash
python -m vibiomir clean-documents \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data \
  --output-dir artifacts/cleaning_stage4_full_dry \
  --config configs/cleaning_stage4.json \
  --mode dry-run
```

For bounded execution, add `--stop-after-documents 10000`. Resume with identical
inputs/config/mode/selection and `--resume`, optionally keeping the stopping
budget. Each transaction commits an entire document and all its versions:

```bash
python -m vibiomir clean-documents \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data \
  --output-dir artifacts/cleaning_stage4_full_dry \
  --config configs/cleaning_stage4.json \
  --mode dry-run --resume --stop-after-documents 10000
```

`paused` is an intentional checkpoint. `partial` means documents failed: inspect
`stats.failed_examples`, stderr and the local checkpoint's `jobs` table. Failed
documents roll back all their versions; nothing is silently skipped as complete.
`interrupted` can resume the unfinished document. A killed process may leave a
`processing` manifest; SQLite recovery and completed jobs determine progress on
resume. Only completed exports may be validated. A stopping budget that reaches
the final document can also finish the export.

Validate a finished full dry-run by replacing the pilot output directory in
the validation command. The expected full scope is 913,969 parent versions and
855,745 document selections for this particular validated parent store. Counts
of recovered, removed or quarantined content must come from your actual run.

## 6. Review rules before applying

Section 5.3 of the [development spec](SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md#53-metadata-extraction-and-cleaning)
requires dry-run evidence, positive/negative fixtures and reviewed rules before
bulk cleaning. The default rulebook marks every rule `proposal`. Consequently,
`--mode apply` refuses that configuration rather than treating proposals as
validated rules.

After running tests and inspecting source examples, copy the config to
`configs/cleaning_stage4_reviewed.json`. Mark only reviewed rules
`"review_status": "approved"`; set `"enabled": false` on unreviewed rules.
Keep review evidence, counterexamples and the config alongside your run. This
manual status records your decision; the program cannot verify that human
review actually happened. Do not mark all rules approved merely to bypass the
check. Any changed pattern/threshold requires a fresh dry-run and review.

The reviewed config is **not created automatically** by a successful dry-run.
First validate the completed dry-run output:

```bash
python -m vibiomir validate-cleaned \
  --output-dir artifacts/cleaning_stage4_full_dry \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data
```

Then create the editable review copy without overwriting an existing one:

```bash
cp -n configs/cleaning_stage4.json configs/cleaning_stage4_reviewed.json
```

Copying leaves rules at `proposal`; it does not approve them. Edit the copy
according to the review decisions above before using apply. A completed dry-run
means processing finished, not that all proposed removals are approved. If apply
fails before starting, its output directory does not exist and cannot yet be
validated. The `&&` below runs validation only after apply succeeds.

```bash
python -m vibiomir clean-documents \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data \
  --output-dir artifacts/cleaning_stage4_full_apply \
  --config configs/cleaning_stage4_reviewed.json \
  --mode apply && \

python -m vibiomir validate-cleaned \
  --output-dir artifacts/cleaning_stage4_full_apply \
  --store-dir artifacts/document_store_stage3_full_v2 \
  --data-dir Data
```

Run apply into a **new directory**, never resume a dry-run as apply. Apply
still vetoes removals containing digits, recognized negation/unit signals,
insufficient preceding body, excessive individual/cumulative deletion or
overlapping edits. Vetoes retain original text and produce review entries.
The default 80-character body guard and 50% maximum deletion are conservative
engineering defaults, not validated per-domain thresholds. They can leave UI
or long feeds untouched. Adjust only with source evidence and counterexamples.

## 7. Outputs and evidence contract

```text
<stage4-output>/
  run_manifest.json
  cleaning.sqlite3
  REPORT.md
  processing_summary.json
  review_samples.jsonl
  VALIDATION.json                   # after manual successful validation
  cleaning_versions/part-*.parquet
  evidence_regions/part-*.parquet
  transformations/part-*.parquet
  document_selection/part-*.parquet
```

`cleaning_versions` has one record per parent version, its processing method,
provisional availability, inherited metadata/hints, diagnostics and character
accounting. It does not copy the original crawl history or 28 million legacy
blocks; those remain in stage 3 and are referenced by parent version ID.

`evidence_regions` contains contiguous raw source slices. Its `source_kind` is
`stage3_text`, `legacy_block` or `html_text_node`. Offsets are Unicode code-point
indices `[source_start, source_end)`, relative to the identified text, block
string or raw `html_trim`. HTML locators retain the original record/shard/hash,
row group/offset and article root. Entities remain raw in evidence; normalized
text is a separate comparison view. Inline HTML formatting can produce separate
nodes and must be reviewed when later reconstructing reading order/context.

Never claim that disjoint regions form one continuous original chunk. The
pipeline uses synthetic newlines only in an internal diagnostic/comparison
view; no such joined text is exported as a source-evidence field. A downstream
chunker must respect region boundaries or explicitly represent multiple source
spans. Normalized hashes are comparison groups, not document identity merges.
Repeated processing always starts from the unchanged parent, never recursively
decodes an earlier normalized view. Original legacy structure remains available
for the next chunking delivery; no table columns/headings are fabricated here.

`transformations` retains exact removed/proposed/vetoed slices and rule revisions.
Kept plus actually removed slices partition every extracted source unit. Failed
original text stays in stage 3 and is accounted as quarantined characters when
there is no safe extracted unit. `source_chars` for recovered HTML counts chosen
raw text nodes, not the complete markup, scripts or excluded DOM subtrees.
The HTML extractor's restricted root/skip policy is separate from rule removals.

`document_selection` compares stage-3 and stage-4 parent choices, retains all
alternatives in `cleaning_versions`, and never claims a final working corpus.
`processing_summary.json` reports per-domain/method/availability counts, rule
actions, review flags and selection changes. Missing metadata remains unchanged.

## 8. Validation, resources and limitations

The validator checks part hashes/schemas/counts, complete parent coverage,
recomputes results from pinned inputs/config, verifies exact source slices,
kept/removed partitions, protected-signal vetoes, unchanged metadata and
provisional version selection. Raw HTML recovery additionally rechecks its
source files and original HTML values. It does not rehash all raw shards for
ordinary stage-3-text regions: those map to the already validated stage-3 store.
This distinction is explicit in `VALIDATION.json`. Clinical correctness,
relevance, complete article extraction and the 98% QA gate are not certified.

The observed stage-3 SQLite file is about 17 GB. Stage 4 reads it in place and
does not copy its audit/input/block tables. It hashes that file at input pinning
and before completed export/validation; these checks involve substantial IO even
for a small pilot. An idle shared source lock prevents normal concurrent writers.
Keep source stores on local disk and do not modify stage 3 during a run.

New results also need SQLite, Parquet and temporary disk space. Full-run disk,
RAM and time have not been measured. Inspect a pilot before starting a full run;
row batches do not bound a single huge document. HTML recovery has a configurable
size guard and a bounded cache. No GPU, neural LID, translation, embedding or
external service is used. Use one local process per output directory.

Resume fingerprints the exact parent files, semantic implementation files,
config, mode, selection, Python patch, Unicode and PyArrow. Any change requires
a new stage-4 output. Read-only consumption of completed stage-3 artifacts is
supported after this code update; resuming an old stage-3 build with changed
project code is a separate operation and is not needed here.

After a validated apply run and source review, delivery 5 can use the region
maps and parent blocks to implement structural chunking. Semantic chunking,
embeddings, ICD and retrieval remain later work with their own GPU decisions.
