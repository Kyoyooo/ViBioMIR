# S07 — completed lexical queries and compact exact CPU search

The existing validated index remains at `artifacts/lexical_stage7_structure/`.
It contains 5,512,624 structural chunks with global statistics and norms. This
runner completes BM25 and cosine TF-IDF query retrieval without rebuilding that
index, changing its analyzer or modifying the frozen S07/S08 source modules.
The owner authorized S07 execution and optimization on 2026-10-29, superseding
the earlier decision to defer its expensive SQL query workload.

Current execution evidence and measured totals belong in the
[completion report](../reports/lexical_stage7_20261029/README.md). These are
technical results; no relevance labels, F2 or clinical quality are inferred.

## Why this implementation is faster

The original `lexical_stage7.search` follows the correct scoring definitions
but joins matching postings to their chunk rows for every query. Its secondary
term index does not contain TF, so repeated term-to-primary-row lookups cause
large amounts of scattered I/O. Merely increasing process count repeats this
work and can increase memory pressure and disk contention.

The new implementation instead:

1. Verifies the original index seal, code, analyzer registry and source snapshot.
2. Analyzes the actual 1,200 queries with the original PyVi/jieba analyzer.
3. Reads the primary postings table sequentially once, extracting every posting
   for every known query term. No stopwords, high-frequency terms or matching
   chunks are dropped. Global DF, document lengths and TF-IDF norms are retained.
4. Stores compact memory-mapped chunk IDs/TF and metadata, with fixed-width
   stable chunk keys for exact tie handling.
5. Computes both scorers together using float64 NumPy arrays, a bounded shared
   LRU of term weights, bounded pending work and a small thread pool.
6. Reads original JSON mappings only for the final top-k chunks, instead of
   joining them for every matching posting.

The default query set has 2,819 known terms and 234,349,921 selected postings.
The projection occupies about 2.2 GiB. It is a disposable acceleration artifact,
not a second independently tokenized corpus or an approximate search index.
Changing the query set requires a new projection directory. The original
lexical index itself remains reusable.

## Outputs

| Path | Role |
| --- | --- |
| `artifacts/lexical_stage7_structure/` | Original sealed SQLite index; preserve unchanged |
| `artifacts/lexical_stage7_query_cache/` | Query-bound compact postings, metadata and validation; reusable but rebuildable |
| `artifacts/candidates_stage7_bm25/` | One signed top-500 candidate file per query, manifest and validation |
| `artifacts/candidates_stage7_tfidf/` | Same contract for TF-IDF |

Candidate files retain integer document/global row IDs, original chunk/version
IDs, source batch/row/order, retrieval-text hash and score. A query with fewer
than 500 matches returns fewer candidates; an unmatched query returns an empty
list. Padding with unrelated chunks is never performed.

## Reproduction and resume

CPU only; no GPU, model download or new Colab notebook is needed. Use the
existing `.venv-stage5` or the tested CPU environment lock. The original analyzer
package/resource versions must match the index registry.

```bash
source .venv-stage5/bin/activate
python -m unittest discover -s tests -p 'test_stage7_search.py' -v

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  python -m vibiomir.lexical_search prepare

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  python -m vibiomir.lexical_search run --workers 4 --weight-cache-mib 1024

python -m vibiomir.lexical_search validate \
  --output-dir artifacts/candidates_stage7_bm25
python -m vibiomir.lexical_search validate \
  --output-dir artifacts/candidates_stage7_tfidf
```

Completed results need not be regenerated. The new entrypoint is
`vibiomir.lexical_search`; the original `vibiomir.lexical_stage7 search` remains
available as a SQL reference for small corpora/checks and is not the fast full
workload command. The original index/manifest are not re-signed for this change.

The native projection builder uses Linux, a C++17 `g++` compiler and the
installed `libsqlite3.so.0` runtime. The helper uses only the stable public
SQLite C ABI and needs no SQLite development headers. It opens the source
database read-only and never loads extensions. Compilation occurs locally;
the executable is a runtime cache, not a Git artifact. A portable fallback is:

```bash
python -m vibiomir.lexical_search prepare --builder python
```

Both builders produce identical arrays on fixtures. The Python fallback scans
the same data and can take longer; it does not change retrieval semantics.
Hosts must be little-endian; this compact layout supports up to 2^32−1 chunks
and stable chunk IDs of at most 64 UTF-8 bytes, including the current SHA-256 IDs.

To pause after a few newly completed queries:

```bash
python -m vibiomir.lexical_search run --workers 2 --stop-after-queries 4
python -m vibiomir.lexical_search run --workers 4
```

Resume checks signed completed files and skips them. An interrupted query can
repeat; completed query files are atomic checkpoints. An unfinished projection
scan restarts, while a completed projection is checksum-verified and reused.
Input/source/scoring-code/top-k changes require new output paths. Worker count
and weight-cache size can change between query invocations. For alternate
outputs, use `--bm25-output`, `--tfidf-output` and `--projection-dir`; do not
delete or overwrite a successful output just to change settings.

## CPU and memory controls

Four query threads were selected for this local 16-logical-CPU, roughly 14 GiB
RAM machine after trying 1/2/4 threads on the same twelve queries. The benchmark
has different cache states across runs and is indicative, not a controlled
parallel speedup claim. All three worker counts produced identical candidates.
Full-run measurements are reported separately.

Use `--workers 1` or `2` to leave more CPU for other work. More threads can
increase memory bandwidth contention and need not improve throughput. NumPy
shares immutable memory maps and cached weights across threads; separate
processes would duplicate more of that working set. `OMP_NUM_THREADS=1` and
`OPENBLAS_NUM_THREADS=1` avoid nested math-library oversubscription.

`--weight-cache-mib 1024` bounds derived weight arrays, not total process RSS.
Each active query also needs score arrays/temporary vectors, mappings and
memory-mapped pages. The OS page cache uses spare RAM and can reclaim it.
Keep the SQLite source and projection on local SSD. RAM/CPU timing observations
must not be generalized to another device or cache state.

## Validation and S09 handoff

The new fixtures compare both scorers and rankings against the original SQL,
including repeated terms, absent terms, ties, one/two workers, pause/resume,
native/Python parity, bounded cache reuse and downstream S09 input binding.
Additional checks on the real index use its global statistics and compare
48 low-DF reference cases with the SQL implementation. See the completion
report for the measured maximum score discrepancy.

Full output validation checks all 1,200 IDs, signed query/run identity, top-k,
finite sorted scores, duplicate chunks and every emitted mapping against the
sealed original index. It is distinct from relevance Recall or F2 evaluation.

The current [S09 config](../configs/retrieval_stage9.json) uses **BM25 + BGE-M3
dense via RRF**. TF-IDF is retained as a separate baseline, rather than being
added as another correlated lexical branch by default. Run S09 preparation
after both S07 BM25 and S08 dense candidate validation pass:

```bash
python -m vibiomir.stage9_pipeline prepare
```

The prepared output is now `artifacts/retrieval_stage9_hybrid_rrf/`. S09 CPU
export can inspect fused results before GPU reranking. The full S09 workflow
and its owner-run GPU steps remain in [RETRIEVAL_STAGE9.md](RETRIEVAL_STAGE9.md).
No S09 full processing or GPU run is launched as part of S07 completion.
