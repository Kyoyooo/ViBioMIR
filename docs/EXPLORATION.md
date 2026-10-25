# Legacy exploratory analysis (EDA)

These commands reproduce the older exploration tool. For current audit and processing results, start with the [report index](../reports/README.md). Stage-specific commands live in their [own runbooks](README.md).

Python 3.10 or later is required. Install PyArrow if it is not available:

```bash
python -m pip install -r requirements-eda.txt
```

Run from the project root with a **new output directory**:

```bash
python -m exploration --data-dir Data --output-dir reports/eda_new
```

Read `REPORT.md` in the output directory. Outputs include JSON summaries, CSV
files, and three SVG charts. `metrics.sqlite3` stores per-row metrics and
ID/URL/shard references without copying all source text or HTML. CSV files use
UTF-8 with a BOM for Excel compatibility. The existing `reports/eda/` directory
contains the previous analysis; use a different directory for a new scan.

Smoke-test the first two shards in path order. This is **not a random or
representative sample**:

```bash
python -m exploration --output-dir reports/smoke --max-files 2 --inspect-blocks
```

Inspect `blocks` across the full corpus:

```bash
python -m exploration --output-dir reports/eda_blocks --inspect-blocks
```

`--batch-size 512` limits rows per batch, not bytes; a single long record can
still consume substantial memory. The current reader selects columns and does
not read `html_trim`. Full analysis can take several minutes or longer and
requires disk space for SQLite, indexes, and temporary files. Aggregations use
on-disk SQLite instead of retaining the entire corpus in RAM. The deeper S00
audit now has a separate `python -m exploration.audit` command. Its tooling is
validated on fixtures and selected real shards. Delivery 2 has completed the
full automatic scan (1,195 shards, 1,105,160 records), source-checksum validation,
and source-consistency checks for all 600 random samples plus targeted cases.
Assistant structural reviews and a processing plan are available; independent
human/clinical sample review remains pending. See the audit guide for limits.

## Independent stages

```bash
python -m exploration --stage inventory --output-dir reports/inventory
python -m exploration --stage corpus --output-dir reports/corpus
python -m exploration --stage references --output-dir reports/corpus
python -m exploration --stage report --output-dir reports/corpus
```

`corpus` and `all` refuse to overwrite an existing `metrics.sqlite3` to avoid
mixing runs. `inventory`, `references`, and `report` may update their outputs
in an existing directory. Use separate output directories when comparing runs.
`run_config.json` records the arguments for `all`.

## Official Hugging Face snapshot

`Data/official/ViBioMIR/` contains `query.parquet`,
`links_corpus_vi_en_zh.parquet`, the original dataset card `README.md`, and
`provenance.json`. Source:
[AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR),
revision `71be0e1239a19dfd7a717e2bc13e3bec21bd0334`.
The manifest records revision-specific URLs, SHA-256 hashes, retrieval time,
schemas, and row counts.

This stored snapshot has 1,220,598 document IDs. The user subsequently supplied
an updated reference count of 1,194,469 URLs (714,469 VI, 240,000 ZH, 240,000 EN).
The specification distinguishes these versions; the existing coverage report
belongs to the stored snapshot. Local processing does not require acquiring all
reference URLs or resolving the new reference before audit/indexing. Official
ID eligibility must be checked before a competition submission.

Download another copy into a new directory when needed:

```bash
python -m exploration.download_official --output-dir Data/official/ViBioMIR_copy
```

When several copies exist, supply explicit file paths to avoid selecting the
wrong snapshot. The dataset card states CC BY-NC 4.0; see
[license and citation details](BAI_TOAN_VIBIOMIR.md#35-license-and-citation).

EDA discovers `query.parquet` and `links_corpus_vi_en_zh.parquet` (or the older
name `links_corpus.parquet`) under `Data/`. Missing files are reported without
automatic downloads. Update the reference comparison and report using existing
corpus metrics without rescanning source text:

```bash
python -m exploration --stage references --output-dir reports/eda \
  --query-file Data/official/ViBioMIR/query.parquet \
  --links-file Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet
python -m exploration --stage report --output-dir reports/eda
```

Query checks cover ID types and duplicates, empty text, normalized duplicate
queries, lengths, and the expected 1,200 IDs. Official-link checks compare IDs
and URLs against scanned records: IDs with shards, nonempty HTTP 2xx text,
out-of-list IDs, missing-ID examples, and IDs without a verbatim source URL
match. `official_coverage_by_domain.csv` counts distinct IDs by domain;
`official_url_mismatch_examples.csv` supports ID–URL review. These are
**acquisition coverage metrics, not retrieval Recall**. Relevance labels are
required to evaluate Precision, Recall, and F2.

## Interpret results within their scope

- A/B/C are collection folder labels; they do not establish train/dev/test or
  public/private splits.
- Distributions count **collected rows**; retries can repeat IDs. Distinct-ID
  counts are also provided. Content hashes exclude empty text and use the
  specified normalization.
- Metadata-only shards remain in the analysis with `missing_text_column` flags.
  They are not silently skipped or treated as successful text acquisition.
- `latin_undetermined` does not mean English; `cjk_likely_zh` does not confirm
  Chinese. Current language hints inspect only a text prefix.
- Whitespace units are not embedding or evaluator tokens; they do not provide
  comparable information-length measurements across Chinese and Vietnamese/English.
- HTTP 2xx can contain a block page, navigation, or unrelated content. Quality
  flags suggest review; they do not delete or filter source records.
- `quality_examples.csv` contains at most 20 rows per flag; duplicate CSVs contain
  at most 1,000 groups. JSON totals cover the scanned scope. Original IDs/URLs
  remain in the metrics database.
- Inventory records read errors. The current corpus stage skips failed shards
  and marks the run `partial`. `--max-files` also marks a run partial. Such
  results are not full-corpus estimates; JSON retains error details.
- `last_run_*.json` describes only the most recent crawler run. Aggregate crawl
  counts come from the SQLite state databases.
- Stop active writers and checkpoint source SQLite WAL before using the current
  EDA: source databases are opened read-only/immutable to avoid sidecars, and
  active writes or uncheckpointed WAL may not be reflected. Source files are
  not modified. The S00 audit adds explicit consistency handling; see [the audit runbook](DATA_AUDIT.md).

Example duplicate-ID inspection with the SQLite CLI, if installed:

```bash
sqlite3 reports/eda/metrics.sqlite3 \
  'SELECT doc_id, COUNT(*) AS n FROM documents GROUP BY doc_id HAVING n > 1 LIMIT 20;'
```

Run the existing normalization, metrics, crawl reconciliation, and official
reference checks:

```bash
python -m unittest discover -s tests -v
```
