# Delivery 3: versioned document store and metadata

**Current status:** the owner completed and validated `artifacts/document_store_stage3_full_v2`.
The commands below are reproduction instructions, not a request to rebuild that
successful store. Review metadata is in the [run registry](../reports/run_registry/README.md).
**GPU required: no. Run locally.** No model download, embedding, training or
external API is involved. No Colab notebook is supplied for this CPU delivery.

This implements D3-01–D3-03 of the
[full-audit processing plan](../reports/data_audit/full_run_01/analysis/PROCESSING_PLAN.md).
It is the first part of S04, with the identity/checkpoint scaffolding needed from
S01. It does not complete the S04 cleaning and main-content QA gates.

The successful store contains 1,105,160 records, 913,969 versions and 855,745
document IDs, with original source checksums verified. Selections remain
provisional. Continue with
[the stage-4 local runbook](CLEANING_STAGE4.md), using that completed store as
read-only input. Do not resume/rebuild stage 3 just to start stage 4.

## 1. Inputs and scope

Use the source shards already audited in delivery 2, with these three artifacts:

```text
Data/ViBioMIR/shards/{A,B,C}/<domain>/*.parquet
reports/data_audit/full_run_01/input_manifest.json
reports/data_audit/full_run_01/scan_status.json
reports/data_audit/full_run_01/record_audit.parquet
```

The audit must be finished and non-partial. Its manifest selects corpus inputs;
other Parquet files, the official links table, queries and crawl databases are
not imported as documents. **The approximately 10 GB audit SQLite database is
not required.** Source shards and their paths relative to `Data/` must match the
audit. New crawls require a new audit/run rather than silently joining an old
checkpoint. Published URL counts and allowlist reconciliation do not block this
local import. `official_match` remains `unverified`.

## 2. Environment and tests: run these yourself

Run commands from the project root. Linux is supported (`fcntl` supplies
the single-writer lock); Windows users should use WSL. Use Python 3.10+ and a
separate virtual environment if needed:

```bash
python -m pip install -r requirements-stage3.txt
python -m unittest discover -s tests -p 'test_stage3*.py' -v
```

The new tests only create temporary synthetic fixtures. They use the existing
S00 inspector to build compatible diagnostic inputs. They cover multilingual
evidence/offsets, metadata roles/conflicts, dates, retries versus versions,
cross-ID aliases, provisional selection, row-group rollback, pause/resume,
input mutations and export corruption. They do not establish medical
correctness or full-corpus performance. Stop and investigate
failures before running the corpus. To check the shared reader/audit regressions
as well, run the existing suite manually:

```bash
python -m unittest discover -s tests -v
```

No tests from this delivery have been executed by the assistant. The pinned
PyArrow version is the project's existing baseline, not a newly tested
environment lock. No GPU libraries are needed.

## 3. Preflight and representative-shard import

Check disk space first. The store duplicates text in SQLite and Parquet, stores
blocks and provenance, and needs temporary/export/backup space. Full-run size,
speed and peak RAM have **not been measured**. Inspect a representative run
before deciding whether your local machine has enough disk. The reader
batch size limits rows, not bytes; one very large document or HTML payload can
still require substantial memory. Default read batch size is 16.

This preview checks source hashes and inputs and writes no store directory.
It still reads the selected source files and hashes the audit artifacts:

```bash
python -m vibiomir prepare-documents \
  --data-dir Data \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir artifacts/document_store_stage3_pilot_reproduction \
  --config configs/document_store_stage3.json \
  --shard-list configs/audit_pilot_shards.json \
  --dry-run
```

After tests and preview succeed, import the seven previously selected pilot
shards. This is a representative engineering check, not a statistical estimate:

```bash
python -m vibiomir prepare-documents \
  --data-dir Data \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir artifacts/document_store_stage3_pilot_reproduction \
  --config configs/document_store_stage3.json \
  --shard-list configs/audit_pilot_shards.json

python -m vibiomir validate-documents \
  --store-dir artifacts/document_store_stage3_pilot_reproduction \
  --data-dir Data
```

The original full audit is used for both the pilot and full import. The audit
diagnostics are streamed, but only selected records are cached in the new
SQLite database. `--max-files N` is also available; it selects sorted paths,
not a random or representative sample. Such runs record `selected_sources_only`.

Inspect `REPORT.md`, `metadata_coverage.json`, `run_manifest.json` and the
exported tables. Compare representative documents with their raw source rows,
especially conflicting same-ID versions and metadata candidates. A successful
validator establishes structural consistency, not the spec's 98% main-content
gate or clinical review.

## 4. Full import and resume

Use a new output directory for the full run. Omit both subset options:

```bash
python -m vibiomir prepare-documents \
  --data-dir Data \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir artifacts/document_store_stage3_full_reproduction \
  --config configs/document_store_stage3.json
```

To process a bounded number of new row groups, add `--stop-after-groups 100`.
This commits whole groups and returns `paused` without publishing partial
exports; reaching the last group can still finish the export. To continue, use
the same output directory, config and source selection, with `--resume`:

```bash
python -m vibiomir prepare-documents \
  --data-dir Data \
  --audit-dir reports/data_audit/full_run_01 \
  --output-dir artifacts/document_store_stage3_full_reproduction \
  --config configs/document_store_stage3.json \
  --resume --stop-after-groups 100
```

Repeat bounded runs, or omit `--stop-after-groups` to finish. Resume can change
read batch size and the stopping budget. It cannot change config, source set,
code, audit bytes, Python version (including patch), Unicode database or PyArrow
version. These are fingerprinted to prevent mixing processing semantics. Input
root paths may move if relative paths, bytes and environment remain the same.
Checkpoints cannot be resumed across different Python versions. Use the same
environment for subsequent invocations; start a new run when it changes.

Fresh runs refuse nonempty output directories. One builder or validator may
hold a store at a time. Row-group failures roll back that group's
inputs and versions, leave diagnostic errors in the manifest, and result in
`partial` rather than a successful corpus. Other groups can still be retained.
An unchanged retry can use `--resume`. Changed source/audit/config/code requires
a new output run. `interrupted`, `failed` and `partial` are not complete exports.
An export interruption can be resumed to rebuild the exports. Only files named
in a completed manifest are authoritative; leftover parts from an interrupted
export must not be globbed as a dataset.

When complete, validate manually:

```bash
python -m vibiomir validate-documents \
  --store-dir artifacts/document_store_stage3_full_reproduction \
  --data-dir Data
```

Expected success state is `complete_provisional`, with `exports_ready: true`,
`ready_for_retrieval: false`, `human_qa_status: pending`. Validation writes
`VALIDATION.json` after checking part hashes, row coverage, checkpoint relations,
metadata provenance, original source slices, grouping and review coverage.
Supplying `--data-dir` also rechecks the original source-file hashes. Each resume
invalidates an earlier validation report; validate again after the build.

## 5. Output contracts

```text
<store>/
  run_manifest.json
  document_store.sqlite3
  REPORT.md
  metadata_coverage.json
  VALIDATION.json                 # only after manual validation
  input_records/part-*.parquet
  document_versions/part-*.parquet
  document_selection/part-*.parquet
  version_review/part-*.parquet
  blocks/part-*.parquet
  content_groups/part-*.parquet
  document_aliases/part-*.parquet
```

Each table's part paths, counts, hashes and schema are in `run_manifest.json`.
Empty tables have a typed manifest with no parts. Consumers must handle that
case rather than calling `read_table()` on an empty directory.

| Table | Meaning |
| --- | --- |
| `input_records` | Every input row, its record ID, raw ID, source shard/hash/row-group/offset, crawl fields, audit flags and version link; invalid IDs remain quarantined rows |
| `document_versions` | Immutable source text, raw blocks, hashes, raw source metadata, extracted metadata with provenance, inferred hints and fidelity/scope labels |
| `document_selection` | One provisional choice or explicit unavailable/quarantine state per valid local doc ID; every choice needs review |
| `version_review` | One decision for every version, including retained alternatives, quality rank and reasons; never final rejection/deletion |
| `blocks` | Every parseable legacy block, its original text/kind/order, conservative heading path and exact offsets when available |
| `content_groups` | Groups of normalized text hashes; comparison only |
| `document_aliases` | Versions sharing a normalized hash, retaining each original doc/version/text hash; no ID merge |

Identity rules:

- `record_id` matches S00: SHA-256 of source SHA, relative shard path, row group
  and offset separated by NUL. Every row survives, including retries.
- `version_id` includes doc ID, verbatim text/state, raw blocks, HTML hash,
  source metadata, extractor/policy revision and mapping signature. Identical
  retries differing only in crawl `ts`/HTTP `status` can share a version; both
  input rows and their crawl fields remain available. Changed text, metadata,
  HTML or source extraction method yields a separate version.
- `representative_record_id` identifies the best-ranked retry for a version;
  stable hash ordering breaks ties. The original HTML remains in the source
  shard, addressable via input locators; it is not duplicated in each version.
- Normalization is a comparison view only: NFKC → HTML decode → lowercase →
  whitespace collapse. Verbatim evidence and multilingual characters stay
  unchanged. No translation or LLM-generated evidence is introduced.

Provisional ranking uses, in order: nonempty text and valid HTTP(S) URL; absence
of a narrow placeholder/binary-redirect signal; fewer critical flags; exact
legacy-block character coverage; successful HTTP status; fewer weak flags;
explicit metadata count. Stable version hash breaks ties. Length and crawl time
do not determine the winner. The rank vector and reasons are exported for
review. Broad UI/captcha/CJK title-overlap flags do not automatically quarantine
an entire article. A short or non-2xx useful body can remain a candidate. Exact
soft-error placeholders and strongly indicated image bytes remain in history
with no provisional content selection. This does not replace cleaning QA.

Offsets are Python Unicode code-point indices `[start, end)`, not UTF-8 byte or
token indices. Blocks map monotonically to exact substrings of unchanged text.
Normalization-only matches, out-of-order matches and missing blocks are
`unmapped_exact` with null offsets. Legacy `h` gives a flat current heading;
only explicit `h1`–`h6` defines nesting. Tables and Q&A are not reconstructed
from missing structure. Invalid raw blocks are retained for later repair.

## 6. Metadata roles and provenance

`metadata_json.fields` contains `title`, `description`, `abstract`, `authors`,
`published_date`, `updated_date` and `category`. Each field has `value`,
`selected`, `candidates` and `conflict`. Candidates retain raw/normalized value,
origin, source locator, precedence, qualitative confidence, selectability,
date precision and parsing issues. Conflicts are retained for review.

Default precedence: existing explicit column → explicit HTML meta → Article
JSON-LD → HTML title. This is a reviewable engineering default, not a guarantee
that the first source is correct. HTML title can be a website title. Author
`@id` references are resolved only within the local JSON-LD graph. Organization
or website dates are not article dates. No remote resources are loaded.

`desc` maps to description, never automatically abstract. An abstract requires
an explicit abstract source. Crawl `ts`, patient admission dates, the latest
related-feed date, cited doctors and featured experts are not article metadata.
Unknown values remain null. ISO/Chinese dates preserve available precision;
ambiguous slash dates stay unresolved unless a configured locale supports their
interpretation. Timezone offsets are retained when supplied, not invented.

Body bylines and the reviewed familydoctor header format yield source-mapped
**candidates only** by default. Generic body dates are ignored. Candidate
selection requires an explicit, versioned config change after review. Hints
for language/type are inherited S00 heuristics, not gold labels, trained LID or
confirmed publication metadata. Clinical validity, source eligibility and
final main-content selection remain pending.

## 7. Local checkpoints

### Troubleshooting: validation refuses an incomplete export

`A completed export is required; resume the build first` means the manifest is
not `complete_provisional` with `exports_ready: true`. Read `REPORT.md` and the
row-group errors in `run_manifest.json` before retrying. A paused/interrupted
run with unchanged code and inputs can use `--resume`. A failed run needs its
underlying error investigated first.

The initial delivery contained an input INSERT with eight placeholders for a
seven-column SQLite table. Its diagnostic is `table inputs has 7 columns but
8 values were supplied`; affected row groups rolled back and produced no
complete export. The INSERT now names its seven columns and uses seven values,
with a dedicated persistence regression test. New row-group failures are
printed to stderr; per-file logs report actual file state rather than claiming
that a failed file committed.

For a store created with that earlier code, run the tests manually and start a
new output directory such as `artifacts/document_store_stage3_full_reproduction_v2`. Do not
use `--resume` on the old store after changing code: its fingerprint correctly
rejects a different implementation. Keep the old store for diagnostics; do not
edit its fingerprint or mark it complete manually. Validate the new output only
after its import/export completes.

### Preserve local progress

`document_store.sqlite3` and `run_manifest.json` preserve progress. Keep the
whole store directory, the original source shards and the three audit files.
Resume with the commands in section 4. To keep a backup, first let the process
exit, then copy the entire idle store directory to a separate local location.
Do not copy a live database. Keep its environment/config/code compatible if
you intend to resume the backup later. No Colab transfer or multi-account
distribution is needed for this delivery.

## 8. Completion and next delivery

Manual acceptance for this delivery requires passing synthetic tests, a
validated representative run, full-run accounting if you choose to execute it,
and review of selected versus retained versions and metadata. Record the actual
run environment, source scope, output path and failures. Do not treat supplied
code as proof of completed data processing.

Delivery 4 can consume the retained versions to implement offline re-extraction,
region-based cleaning and meaning-preservation regression fixtures. It must
create derived versions with transformation/source mappings before finalizing
content choices. Chunking, tokenization, ICD, indexing, embeddings, retrieval,
GPU encoding and competition submissions remain subsequent deliveries.
