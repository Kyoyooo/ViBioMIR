# S00 data audit tooling — delivery 1

The audit inspects existing local data without downloading sources or rewriting
corpus files. Delivery 1 implements the tooling and validates fixtures plus a
small, deliberately selected real-data pilot. Delivery 2 subsequently completed
the full automatic scan and source-consistency checks; see
[the full-audit report](../reports/data_audit/full_run_01/analysis/REPORT.md) and
[processing plan](../reports/data_audit/full_run_01/analysis/PROCESSING_PLAN.md).
Independent human/clinical review remains pending. Subsequent stage-4 technical
cleaning validation completed; see [the execution evidence](../reports/cleaning_stage4_rule_review_01/EXECUTION.md).

## Run requirements

Use Python 3.10+ on Linux/Colab and the existing `requirements-eda.txt` dependency.
The implementation was tested with Python 3.13.5 and PyArrow 22.0.0. Only PyArrow
and Python's standard library are used. No GPU, models, network access, official
allowlist, or complete acquisition is required.

```bash
python -m pip install -r requirements-eda.txt
python -m exploration.audit --help
```

The audit command is `exploration.audit`. Document preparation, cleaning and
chunking now use the implemented `vibiomir` CLI; see the [documentation map](README.md).

## Reproduce the pilot

`configs/audit_pilot_shards.json` is a JSON array of paths **relative to `Data/`**.
It selects seven shards covering VI articles, ZH articles/Q&A, metadata-only
failed acquisition, and historical HTML/encoding/block-page/long-text signals.
These are engineering coverage cases, not a probability sample of the corpus.
PubMed's selected shard has no text; do not treat it as English-content coverage.
English content and HTML metadata paths are covered by synthetic fixtures.

Use a fresh output directory:

```bash
python -m exploration.audit \
  --data-dir Data \
  --output-dir reports/data_audit/pilot_reproduction \
  --shard-list configs/audit_pilot_shards.json \
  --batch-size 8 --sample-size 60 --stop-after-groups 2
```

Resume with the same input selection, thresholds, sample size, and seed:

```bash
python -m exploration.audit \
  --data-dir Data \
  --output-dir reports/data_audit/pilot_reproduction \
  --shard-list configs/audit_pilot_shards.json \
  --batch-size 8 --sample-size 60 --resume
```

Batch size and `--workers` may change between compatible invocations: they do
not alter metric semantics. `--workers 4` enables a bounded CPU process pool
while source reads and all SQLite checkpoint writes stay in the parent process.
The default remains one worker. Use fewer workers when RAM/CPU are limited;
reported process peak RSS covers the parent, not aggregate worker memory.
Code, input checksums, PyArrow version, semantic settings, selection, and input
membership must remain compatible. After a code/data/config change, start a new
output directory rather than mixing metric versions.

The delivered [pilot report](../reports/data_audit/pilot_stage1/REPORT.md) and
[validation record](../reports/data_audit/pilot_stage1/PILOT_VALIDATION.md) provide
actual scope and results. The report remains `partial=true` even when all seven
selected shards finish, because the remaining corpus was not inspected.

## Full-corpus command for delivery 2

The following command is available but was **not run for delivery 1**:

```bash
python -m exploration.audit \
  --data-dir Data \
  --output-dir reports/data_audit/full_run_01 \
  --batch-size 32 --sample-size 600
```

Resume with the same arguments plus `--resume`. To inspect only the first few
sorted corpus files, add `--max-files N`; this is explicitly partial and not a
representative sample. Do not combine it with a shard list unless an intentionally
smaller subset of that list is wanted.

Inventory/fingerprint without scanning record contents or creating outputs:

```bash
python -m exploration.audit \
  --data-dir Data \
  --output-dir reports/data_audit/full_run_01 --dry-run
```

Dry-run still reads Parquet footers and streams selected files for SHA-256.
Input counts come from actual files, not the announced 1,194,469 URLs. Plan
output disk from pilot artifact sizes and actual row counts before a full run.

## Implemented checks

- Reuses `common.parquet_batches`, normalization/script patterns, existing
  `corpus.metrics` and quality flags, CSV output, and Markdown table rendering.
- Inventories every local file; separates reference Parquet, corpus shards,
  SQLite, archives, and other files. Metadata-only shards under `shards/` remain
  corpus inputs. Corrupt Parquet retains a failed state and unknown row count.
- Hashes selected corpus files before/after inspection. Changed inputs are
  quarantined; their derived rows are discarded. Other files receive size/mtime/
  schema cataloging, not a claim of full checksum verification.
- Keeps per-record shard/checksum/row-group/row-offset locators, including records
  with missing/invalid IDs. Records expose both raw and normalized text hashes.
- Checks text/metadata types, empty/short text, source/domain conflicts, title
  signals, entities/markup, Unicode replacement, repeated lines/characters,
  suspected gates/UI, full-text script ratios, and mapped number/unit/negation
  examples. Short text or HTTP status alone never deletes a record.
- Parses every available blocks value; records kinds, headings-only cases,
  normalized text–block agreement, and candidate Q&A/article structure.
- Reads `html_trim` separately with aligned batches, inspecting visible text,
  tags, title/meta and JSON-LD field availability. It does not fetch linked
  resources or assert that trimmed/tolerantly parsed HTML is complete.
- Profiles existing metadata and HTML extraction candidates. Crawl timestamps
  are not converted into publication dates, and `desc` is not invented as an abstract.
- Groups duplicate IDs/content on disk, flags conflicting versions/source URLs,
  and collects repeated short-block candidates by domain. No source IDs are merged.

All action categories (`usable_as_is`, `needs_cleaning`, `needs_reextraction`,
`needs_review`, `unusable_currently`) are **provisional automatic signals**.
Even `usable_as_is` has `review_status=unreviewed`. No cleaning rule is enabled.

## Checkpoints and failure behavior

The output SQLite database is the checkpoint authority, with one writer guarded
by a local file lock. Each row group commits records, template counts, and
completion status together. A failed or interrupted group is rolled back and
can be retried; prior successful groups remain. Repeating a completed invocation
must not multiply rows.

Record-inspection exceptions retain a failure record/locator and make the audit
partial. Read failures are recorded per file/row group. `Ctrl+C` produces an
interrupted report from committed work; a hard kill may leave stale report files,
but `--resume` reconstructs them from committed database state. Uncommitted work
in the active row group is repeated. Output must be outside the input directory;
a new run refuses to overwrite nonempty output.

Source SQLite databases are **cataloged only and never opened**, including when
WAL is present. Their contents are not reconciled by this command. This avoids
claiming a consistent source-state view while a crawler may still be writing.
Selected Parquet must be stable; stop or isolate active Parquet writers.

## Output contracts

| Artifact | Meaning |
| --- | --- |
| `input_manifest.json` | Frozen input catalog, selected files/checksums, code/config/environment fingerprints |
| `audit.sqlite3` | Internal checkpoint state and metrics; a single local writer |
| `scan_status.json` | Scope, counts, errors, file/group states, automatic findings, runtime measurements, limitations |
| `record_audit.parquet` | Stable typed record metrics; nested diagnostics are UTF-8 `*_json` columns |
| `domain_quality.csv` | Row-based dispositions and text availability by domain |
| `metadata_profile.csv` | Metadata field states with domain denominators |
| `structure_profile.csv` | Block/HTML/type hints and text–block agreement |
| `duplicate_versions.parquet` | Repeated IDs with normalized-version/source-URL counts |
| `duplicate_content.parquet` | Nonempty normalized-text duplicate groups and distinct-ID counts |
| `repeated_block_candidates.csv` | Up to 1,000 repeated short-block/line candidates; not validated boilerplate |
| `review_samples.jsonl` | Random domain-stratified samples with probabilities/weights, plus separate targeted examples |
| `review_decisions.csv` | Human-review queue; existing notes/custom columns are retained and new sample IDs appended on resume |
| `processing_plan.md`, `proposed_rules.yaml` | Draft suggestions requiring full audit/review; every proposed rule is disabled |
| `REPORT.md` | English summary, explicit partial scope, links, and limitations |

`row_offset` is zero-based **within its row group**. Text signal offsets are
Python Unicode code points into the original `text`, with exclusive end offsets.
`snippet_origin` distinguishes original text, joined-block preview, and visible
HTML preview; previews are not submission evidence. Missing values remain null.
Source text and snippets retain their original VI/EN/ZH languages.

Review random and targeted samples separately. Random samples use seeded stable
hash ordering within domains; their stratum inclusion probability is `n/N` and
weight is `N/n`. A small sample budget may not provide three records/domain or
cover every language/type/disposition. Complete that coverage in delivery 2.
Targeted examples have no probability weight and cannot estimate prevalence.

The review queue preserves annotations from earlier packets even if current
sample membership changes. If a user changes its format so `record_id` is absent,
the original remains intact and a `review_decisions_pending.csv` queue is emitted;
`scan_status.json.review_queue` identifies the active queue. Before manually
editing generated draft plans/rules, copy the reviewed version outside the audit
output; derived reports/plans are rebuilt on resume.

## Full-audit interpretation

After the full scan has finished with `partial=false`, derive document-level
counts, metadata availability and processing priorities into a separate output:

```bash
python -m exploration.audit_analysis \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir reports/data_audit/full_run_01/analysis
```

This command rejects partial input and reconciles record counts and quality
flags before interpreting documents. It distinguishes repeated records,
conflicting text versions, and documents with at least one candidate version.
Its automatic candidate counts do not establish clinical validity or relevance.

Verify the review packet directly against source row locators and original text:

```bash
python -m exploration.audit_sample_check \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir reports/data_audit/full_run_01/sample_validation
```

Delivery 2 checked 646 unique packet records, including all 600 random samples,
across 442 source files. All 3,161 sampled signal spans matched their original
text offsets. This establishes source consistency, not human/clinical correctness.

The first full run includes an explicit runtime-only checkpoint migration in
`RUNTIME_UPGRADE.json` and preserves `input_manifest.pre_parallel.json`. The
record checker, reader, report implementation and prior source checksums were
verified unchanged; byte-equivalence tests cover parallel resume. This is a
recorded migration, not permission to bypass fingerprint checks for arbitrary
code changes. General incompatible changes still require a new run.

## Verification and remaining scope

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Tests cover multilingual fixtures, invalid types, missing content/offline recovery,
HTML/JSON-LD candidates, correct source spans, record/file/group failures, input
mutation, pause/interruption/resume equality, review-note preservation, live source
WAL cataloging, output guards, single-writer locking, and existing EDA behavior.

Delivery 1 does not complete the S00 exit gate: full audit and reviewed decisions
are still pending. Near-duplicate semantic classification, main-content semantic
verification, trained LID, actual cleanup/metadata extraction, and retrieval are
not implemented here. Full-text token/script checks remain heuristics. Memory
is bounded by row batches, SQLite caching, and streamed exports, but a very large
single record or Parquet decoder allocation can exceed the ordinary batch budget.
The pilot's process peak RSS is a measurement, not a guaranteed full-run bound.
