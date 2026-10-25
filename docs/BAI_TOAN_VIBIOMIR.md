# ViBioMIR: multilingual biomedical information retrieval

This document consolidates the user-provided competition specification, with
additions from the Hugging Face dataset card, repository metadata, and official
Parquet files inspected on 2026-10-24. Evaluation, model restrictions, and
submission rules remain based on the user-provided specification: the dataset
card does not describe all of them. Official organizer announcements and the
official evaluator take precedence when updated.

The English translation preserves the original file path. Historical dataset
statistics below refer to the inspected revision. The user subsequently supplied
an updated reference list of **1,194,469 URLs: 714,469 VI, 240,000 ZH, 240,000 EN**.
See the [development specification](SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md) for the
version discrepancy and current scope: audit and process existing local data;
recrawling is deferred and complete URL acquisition is not a prerequisite.

## 1. Background

Biomedical knowledge is distributed unevenly across languages. Vietnamese
resources are growing but remain more limited in depth and specialization than
English and Chinese resources, creating an information-access gap for Vietnamese
users.

The competition asks teams to build multilingual biomedical retrieval systems:
queries are in Vietnamese, while relevant documents and passages may be in
Vietnamese, English, or Chinese. The task is **retrieval**; generating medical
answers is not required.

## 2. Formalization and objectives

Given Vietnamese queries `Q = {q₁, q₂, …, qₙ}` and organizer-provided or designated
biomedical sources, retrieve and rank relevant documents or content from the
three languages for each query.

Teams design acquisition → preprocessing → chunking → embedding/indexing →
retrieval → reranking/postprocessing. The organizers do not supply a ready-made
embedding or indexing pipeline.

The three core objectives are:

1. **Document retrieval:** identify relevant documents through cross-language
   retrieval, beyond keyword overlap.
2. **Chunk retrieval:** locate passages that actually contain relevant information.
   Teams choose segmentation methods and retrieval units.
3. **Vietnamese medical query understanding:** handle medical/clinical terms,
   concepts, and questions containing multiple facts or intents.

## 3. Official data

Source: [Hugging Face — AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR).

### 3.1. Queries: `query.parquet`

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | integer | Query identifier |
| `query` | string | Vietnamese medical question |

Original-language example:

```json
{"id": 1, "query": "Cần làm gì đối với tình trạng tắc nghẽn đường tiết niệu do sỏi thận?"}
```

Submit predictions for **all 1,200 queries released across both phases**. The
platform selects public/private phase queries for scoring; teams do not need to
know the split in advance.

### 3.2. Documents: `links_corpus_vi_en_zh.parquet`

**The actual Hugging Face filename is `links_corpus_vi_en_zh.parquet`**;
`links_corpus.parquet` is the name used in the original competition description.
EDA supports both names and requires explicit paths if multiple files are found.
References to `links_corpus.parquet` in the rules below retain the original naming.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | integer | Official document ID used for evaluation |
| `url` | string | Original article URL to acquire |

```json
{"id": 1, "url": "https://example.com/article"}
```

Teams acquire, process, segment, and build the knowledge base. In this evaluation
version, **all document labels use `id` from `links_corpus.parquet`**. Documents
outside the list do not count as positives. Preserve the mapping
`id → URL → text → chunk`, including redirects and duplicate content.

### 3.3. Statistics published in the inspected dataset card

| Metric | Value |
| --- | ---: |
| Queries | 1,200 |
| Query word count: median / P95 | 18 / 84 |
| Query word count: minimum–maximum | 5–283 |
| Total links in the inspected revision | 1,220,598 |
| Vietnamese links | 740,598 |
| English links | 240,000 |
| Chinese links | 240,000 |

English sources include PubMed documents. Language and word-count totals here
are **published statistics**, not EDA language-identification results. The two
Parquet files contain only the fields described above; they do not expose
language, intent, topic, or year labels for each document. The card includes
figures by category, year, and length, but this does not establish that those
labels are released.

Source: [dataset card at the downloaded revision](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR/blob/71be0e1239a19dfd7a717e2bc13e3bec21bd0334/README.md).

Direct query inspection using `str.split()` yields minimum/P50/P95/maximum
**5/18/84/281**. The maximum differs from the published 283; the published
counting method is unspecified. Keep published and measured statistics separate
and preserve query text.

### 3.4. Hugging Face configurations and splits

| Configuration | Split | File |
| --- | --- | --- |
| `query` | `train` | `query.parquet` |
| `corpus` | `train` | `links_corpus_vi_en_zh.parquet` |

`train` is a technical split name in Hugging Face metadata. It does not establish
a labeled training set or reveal the public/private split. The inspected
revision contains no qrels or chunk relevance labels. Directly read schemas:
query has `id: int64 not null`, `query: string not null`; corpus has
`id: int64`, `url: string`.

### 3.5. License and citation

The dataset card states **CC BY-NC 4.0** and requires attribution. Individual
source websites have their own terms for acquisition and use. The dataset-card
license does not establish the license of every linked article.

The dataset card recommends this citation:

```bibtex
@online{medkb,
  title   = {MedKB -- Medical Knowledge Base},
  url     = {https://medkb.tinix.ai/},
  urldate = {2026-09-29}
}
```

The BibTeX date is the value recommended by the source; actual retrieval time is
stored separately in the manifest.
[License linked by the dataset card](https://creativecommons.org/licenses/by-nc/4.0/).

### 3.6. Version and locally downloaded files

Repository: `AIGuruTinix/ViBioMIR`; downloaded commit:
`71be0e1239a19dfd7a717e2bc13e3bec21bd0334`.

```text
Data/official/ViBioMIR/
├── README.md
├── query.parquet
├── links_corpus_vi_en_zh.parquet
└── provenance.json
```

Verified metadata: 1,200 query rows and 1,220,598 corpus-list rows.
`provenance.json` records revision-specific URLs, retrieval time in Vietnam,
bytes, SHA-256, schemas, and Parquet row counts. Official files are stored
separately from crawled shards. Download another copy to a new output directory:

```bash
python -m exploration.download_official --output-dir Data/official/ViBioMIR_copy
```

This downloads the reference files without crawling article URLs. EDA stages
remain local; the separate download script requires network access.

## 4. External data and permitted models

- External data is permitted with clear citations and sufficient provenance for
  organizer verification.
- PLMs and LLMs whose training data and/or models are publicly available may be
  used, for example through Hugging Face or similar platforms.
- **Closed models are prohibited**, including the examples GPT-4o and Gemini.
- Models must have been released **before 2026-08-01 in Vietnam time**, with
  **at most 15B parameters**. The cutoff excludes August 1 itself.
- Teams may choose biomedical multilingual embeddings, indexing/search methods,
  reranking/postprocessing, and legally usable open medical data for auxiliary
  training or evaluation.

Maintain data-source manifests with URLs, collection dates, versions, and
licenses where available. For models, record the name, revision, parameter count,
and evidence of release date. These are recommended provenance records, not
additional required submission fields.

## 5. Evaluation

**Document** and **chunk** retrieval are evaluated separately using Precision,
Recall, and F2. Calculate each metric **per query**, then take its arithmetic
mean across queries (**macro-average**).

### 5.1. Document level

For one query, let `D` be the set of predicted document IDs and `G` the reference
relevant-document set:

```text
Precision_doc = |D ∩ G| / |D|
Recall_doc    = |D ∩ G| / |G|
```

`D` and `G` are sets; avoid duplicate IDs in `relevant_docs`.

### 5.2. Chunk matching

Compare a predicted chunk only with reference chunks for the **same query and
same `doc_id`**. Normalize both texts in this order:

1. Unicode NFKC normalization.
2. HTML entity decoding.
3. Lowercasing.
4. Collapse consecutive whitespace and trim leading/trailing whitespace.

For normalized predicted chunk `c` and reference chunk `g`:

```text
overlap(c, g) = LCS_tokens(c, g) / |g|
```

`LCS_tokens` is the length of the **longest common subsequence** of tokens:
order is preserved, but adjacency is not required. The denominator is the
**reference chunk's token count**. A prediction is correct if it matches
**at least one** reference chunk with overlap **≥ 0.40**.

### 5.3. Chunk level

```text
Precision_chunk = correct predicted chunks / total predicted chunks
Recall_chunk    = reference chunks found / total reference chunks
```

The numerators count different things: correct predictions versus covered
reference chunks. Do not assume one-to-one matching.

### 5.4. F2 and macro-average

For each level and query:

```text
F2 = 5 × Precision × Recall / (4 × Precision + Recall)
macro_P  = (Σ Pᵢ) / n
macro_R  = (Σ Rᵢ) / n
macro_F2 = (Σ F2ᵢ) / n
```

F2 weights Recall more heavily than Precision. **Do not** substitute macro_P
and macro_R into the F2 formula to compute macro_F2. Balance coverage against
the number of returned results; the supplied rules do not specify a maximum top-k.

### 5.5. Unspecified evaluator details

The supplied information does not define the LCS tokenizer, especially for
Chinese; zero-denominator/empty-gold conventions; duplicate-chunk handling; or
all scorer implementation details. Local implementation assumptions are not
official rules. EDA cannot calculate actual relevance Precision/Recall/F2 without
reference relevance labels.

## 6. Submission

The JSON file is an array with one object per query:

```json
[
  {
    "id": 1,
    "relevant_docs": [101, 202],
    "relevant_chunks": [
      {
        "doc_id": 101,
        "chunk_text": "Đoạn nguyên văn trích từ tài liệu có id 101.",
        "chunk_order": 0
      },
      {
        "doc_id": 202,
        "chunk_text": "Đoạn nguyên văn trích từ tài liệu có id 202."
      }
    ]
  }
]
```

This original-language example illustrates structure. Its IDs/text are not
actual retrieval results.

| Field | Requirement |
| --- | --- |
| `id` | Query ID, integer |
| `relevant_docs` | Relevant document IDs |
| `relevant_chunks` | Relevant chunks |
| `doc_id` | Original document ID from `links_corpus.parquet` |
| `chunk_text` | Content extracted from the original document; **not rewritten or generated** |
| `chunk_order` | Optional nonnegative integer; otherwise the chunk's array position is used |

The original example uses string document IDs such as `"doc_id1"`, while the
current description specifies integer IDs from the corpus file. The example
above follows the integer description. Check the official schema/validator
before submission; do not replace IDs with URLs or invented identifiers.

If no results are predicted at a level, provide its field with `[]`. Include
all 1,200 queries; missing questions or invalid formatting prevent evaluation.

### Submission procedure

1. Generate a schema-compliant `.json` containing every query.
2. Create a ZIP with **exactly one JSON file at its root**, without subdirectories.
3. Upload through **My Submissions** at
   [leaderboard.aiguru.com.vn](https://leaderboard.aiguru.com.vn/).

Daily submission limits apply to support fairness and prevent answer probing;
the provided information does not specify the number. Submissions missing the
file or questions are not evaluated and do not count toward the allowed maximum.

## 7. Collected data and download history

Initial inspection of `Data/ViBioMIR/`, before official files were downloaded:

```text
Data/ViBioMIR/
├── shards/{A,B,C}/<domain>/part-*.parquet
├── state_A.sqlite3
├── state_B.sqlite3
├── state_C.sqlite3
├── last_run_A.json
└── last_run_C.json
```

- 1,195 Parquet shards, totaling 2,428,459,143 bytes at inspection time.
- Metadata reports 1,105,160 rows across two schemas: 1,169 full-content shards
  and 26 crawl-metadata-only shards lacking content columns.
- Example columns: `id`, `domain`, `url`, `status`, `final_url`, `ts`, `text_len`,
  `ver`, `source`, `title`, `desc`, `canonical`, `method`, `blocks`, `text`, `html_trim`.
- Sample `blocks` values are JSON strings containing `[block_kind, text]` lists.
- SQLite contains `crawl_state(id, url, domain, status, final_url, ts)` and `state_meta`.
- A/B/C identify folders/collection groups; **they do not establish train/dev/test
  or public/private splits**.
- `last_run_*.json` describes only the latest run, not aggregate acquisition totals.
- The first inspection had no official query/corpus-list files. These were later
  added under `Data/official/ViBioMIR/` as described in section 3.6. Query EDA and
  reference coverage appear in the [report](../reports/eda/REPORT.md).

### Reconciliation against the stored official snapshot

| Metric | Value |
| --- | ---: |
| Valid, distinct query IDs | 1,200 |
| Distinct official document IDs | 1,220,598 |
| IDs with shards | 855,745 |
| IDs with nonempty HTTP 2xx text | 852,995 |
| Nonempty HTTP 2xx text coverage | 69.88% |
| Official IDs without shards | 364,853 |
| Shard IDs outside the official list | 0 |
| Collected IDs without any verbatim source-URL match | 0 |

The stored official list spans 94 domains. Missing IDs concentrate in six
domains. `pubmed.ncbi.nlm.nih.gov` has 240,000 official IDs, only 43 with shard
records, and **zero with nonempty HTTP 2xx text**. Two sources have no shards:
`nhathuoclongchau.com.vn` (79,598 IDs) and `ask.39.net` (32,772 IDs).
These are local reconciliation results for the stored snapshot, not dataset-card
statistics or retrieval Recall. They do not establish coverage of the updated list.

Details: [references_summary.json](../reports/eda/references_summary.json) and
[coverage by domain](../reports/eda/official_coverage_by_domain.csv).

## 8. Current implementation scope

Implemented work covers task consolidation and EDA of collected data: inventory,
schemas, crawl states, text lengths/quality, duplicate IDs/URLs/content, language
hints, optional block statistics, analysis of 1,200 queries, official ID/source-URL
reconciliation, and coverage by domain. Training, embeddings, indexing, retrieval,
reranking, and submission creation/upload have not been implemented.

The next development stage is the deeper S00 audit in the
[development specification](SPEC_DRIVEN_DEVELOPMENT_VIBIOMIR.md). Current scope
accepts incomplete acquisition and defers recrawling. See the [README](../README.md)
for commands and interpretation limits.
