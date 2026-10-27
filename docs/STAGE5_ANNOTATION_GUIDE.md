# Stage 5 — annotation guide

Use alongside [the execution runbook](STAGE5_QA_AND_EXPERIMENTS.md). Preserve
original VI/EN/ZH text. Humans supply questions, relevance and clinical judgments;
templates and model predictions are not gold labels.

## Content and clinical QA

Edit a copy of `reviews_template.csv`, preserving its header and assigned
`doc_id`/`role`. Save UTF-8 CSV; quote fields containing commas or newlines.

| Field | Allowed content |
| --- | --- |
| `doc_id` | Assigned integer document ID |
| `role` | `primary`, assigned `secondary`, or added `adjudication` |
| `reviewer` | Actual identity registered in the reviewer JSON |
| `language_reviewed` | `vi`, `en`, `zh`, or `+` combination for mixed evidence |
| `main_content` | `pass`, `fail`, `uncertain`: correct article body and useful content |
| `meaning_preserved` | `pass`, `fail`, `uncertain`: source meaning, negation, conditions, dosage and units retained |
| `chunk_structure` | `pass`, `fail`, `uncertain`: all displayed strategies retain usable boundaries/context |
| `decision` | `accept`, `needs_fix`, `quarantine`, `uncertain` |
| `reason` | Required for every non-accept decision; identify source/region/strategy |
| `issue_codes` | Optional semicolon-separated codes, e.g. `boilerplate;lost_negation` |

Compare stored source → cleaned regions → each strategy's evidence and retrieval
context. Check title attribution, headings, list continuity, question/answer
pairing, table rows, contraindications, decimal values and units. Context must
not be mistaken for source evidence. Source fidelity may already be limited by
the initial crawl; record uncertainty rather than reconstructing missing text.
The stored page can be checked against its source URL manually, but a changed
live page does not replace the pinned source snapshot.

`accept` requires all checks passing. Use `needs_fix` for recoverable extraction
or boundary issues, `quarantine` for unusable/unreliable content, `uncertain` for
insufficient evidence or competence. `language_reviewed=unknown` permits only
uncertain checks/decision. Unfilled rows remain pending. Reviewer competence
is declared per language; mixed content needs all relevant languages.

Primary and secondary reviewers must be different people. Resolve differing
checks, decisions or actual language through an added third-person
`adjudication` row; retain both original judgments. Clinical confirmation requires
a qualified reviewer whose completed judgment agrees with the final result.
More than the minimum 20% secondary review can be recorded where necessary.
Do not invent a clinician identity or mark a competence flag merely to pass.

## Human-authored local questions

`queries.json` contains an array of objects. Fill each blank query and author,
verify its Vietnamese wording and source, then set `vietnamese_confirmed` and
`source_verified` to JSON `true`. These are human confirmations. Keep integer
IDs unique. Fill `gold_languages`, `intent`, `icd`, `gold_domains`, `tags` with
arrays of strings verified by annotators; unknown fields may remain empty.
`gold_languages` describes the evidence language, not the Vietnamese query.

Keep related questions, translations and origin sources together through
`case_group` and a single `split`: `train`, `dev`, `holdout`. The compiler rejects
duplicate normalized questions and origin documents shared across splits, but
humans must also detect paraphrases and related cases with different IDs.
Write questions before looking at compared chunk boundaries. Initial templates
are not automatically balanced by language or clinical intent.

## Document relevance and original gold spans

In `pooled_judgments.csv`, fill `judged=yes` only after review, then
`relevant=yes` or `no`, `annotator`, and an independent `reviewer` where assigned.
`judged=no` means unjudged and requires blank relevance. Predictions do not
establish relevance. Inspect every compared top result, especially on holdout.
For explicitly verified no-positive questions, record `goldless_confirmed_by`
in the query; otherwise absence of positives blocks compilation.

Gold spans in `spans.csv` use these columns:

```text
query_id,doc_id,derived_version_id,region_id,start,end,annotator,reviewer
```

Choose the medically sufficient original passage independently of any chunker.
Use an evidence export's exact region text, not generated summaries or retrieved
chunk boundaries. `start` is inclusive and `end` exclusive **within that region**,
using Python Unicode code points (not UTF-8 bytes or JavaScript UTF-16 units).
The compiler extracts the text and calculates source offsets/hashes. Each
positive document needs at least one gold span. Multiple medically distinct
spans are allowed; overlapping labels should reflect meaningful references,
not repeated copies intended to inflate recall.

To avoid calculating Unicode offsets by hand, copy the exact original passage
to a UTF-8 text file and run, substituting a real integer ID:

```bash
python -m vibiomir.local_benchmark locate-span \
  --evidence-dir artifacts/local_eval_v1/evidence \
  --doc-id 123 --text-file artifacts/local_eval_v1/selected_passage.txt \
  --output-file artifacts/local_eval_v1/span_candidates_123.json \
  --strip-final-newline
```

The optional flag removes one editor-added terminal newline only. Matching
does not normalize/translate evidence. If a passage occurs multiple times,
choose the correct region/occurrence manually and copy its offsets/version to
the span CSV, with query and annotator IDs. Nothing is labeled automatically.

Independently re-review at least 20% of all judged document and gold-span labels.
The compiler checks different annotator/reviewer IDs and coverage; annotators
must resolve disagreements before compiling. It does not verify credentials or
implement a separate blind label-adjudication UI. Preserve annotation history
and reviewer rationale outside the compiled result.

## Decision record

After examining dev reports, record: reviewed benchmark version; source snapshot;
configuration IDs; document/chunk macro F2; paired uncertainty; language/intent/
ICD/domain support; tokenization sensitivity; annotation gaps; latency/storage/
GPU cost and unresolved clinical issues. A small 30-query pilot has limited
statistical power. Freeze configurations before holdout, report the holdout
once labels are complete, and keep any later retuning in a new experiment.
