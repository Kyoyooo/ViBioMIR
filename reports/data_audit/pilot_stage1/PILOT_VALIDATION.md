# Delivery 1 pilot validation

This is engineering validation on selected shards, not a full audit or a corpus-quality estimate.

## Checks

- 23 tests pass: 18 audit checks/integration cases and 5 existing EDA tests.
- 7 selected shards, 6,040 committed rows, and 6,040 unique record IDs.
- Pause after 2 row groups: 2,000 rows committed; resume completes 6,040.
- A second resume leaves the record Parquet checksum unchanged and adds no rows.
- Selected source SHA-256 checksums still match the pre-scan manifest.
- Every current review sample has a queue entry; human review remains pending.
- All selected files complete without read or record-processing errors.
- The report correctly remains partial because 1,188 other corpus shards were not scanned.

## Scope and measurements

Pilot output size before this validation note: 65,704,182 bytes.
Peak RSS for the final no-op resume: 169.33 MiB (not a full-run guarantee).
The final scan_status timing is a no-op resume. Phase wall times below cover each complete CLI invocation:

| Phase | Wall seconds | Committed rows |
| --- | ---: | ---: |
| 1: --stop-after-groups 2 | 5.037 | 2000 |
| 2: --resume | 14.397 | 6040 |
| 3: --resume | 1.468 | 6040 |

Automatic classifications are provisional. This pilot is deliberately biased toward error/long-text cases.
It covers real VI/ZH content and metadata-only PubMed failures; EN and nonempty HTML paths use fixtures.
No model inference, source downloading, cleaning, or indexing was run.

## Selected shards

| Shard | Selection reason |
| --- | --- |
| `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714570674-056.parquet` | Historical Unicode replacement signal; Longest text in historical metrics; stress batch/length handling |
| `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714909666-041.parquet` | Historical HTML-in-text signal |
| `ViBioMIR/shards/A/vov.vn/part-1792716842933-706.parquet` | Historical suspected block-page signal |
| `ViBioMIR/shards/B/120ask.com/part-1792685726879-367.parquet` | Chinese Q&A content |
| `ViBioMIR/shards/B/vinmec.com/part-1792716882112-410.parquet` | Vietnamese articles with heading/paragraph blocks |
| `ViBioMIR/shards/C/cnkang.com/part-1792716526532-236.parquet` | Chinese medical articles |
| `ViBioMIR/shards/C/pubmed.ncbi.nlm.nih.gov/part-1792716468718-977.parquet` | Metadata-only failed acquisition; no English content assumed |

Machine-readable details: [PILOT_VALIDATION.json](PILOT_VALIDATION.json).
Audit output: [REPORT.md](REPORT.md), [scan_status.json](scan_status.json).
