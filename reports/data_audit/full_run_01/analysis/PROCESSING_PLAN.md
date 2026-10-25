# Processing plan after the full local audit

**Status:** ready to implement the versioned document store and metadata contracts. Bulk cleaning rules remain disabled until domain-specific regression tests pass.

## Evidence and scope

All 1,195 local corpus shards and 1,105,160 readable records were inspected. They represent 855,745 distinct document IDs.
The announced URL-list size is not an acquisition quota. Missing sources do not block local processing; acquisition remains deferred.
There are 852,995 local IDs with at least one nonempty text version and 2,750 without one. Nonempty does not mean correct or relevant.

## Decisions for the next implementation delivery

| Issue | Priority | Affected count | Count means | Action |
| --- | --- | --- | --- | --- |
| D3-01 | P0 | 855,745 | distinct local document IDs | Keep source hashes/locators and every content version; do not replace IDs or select by longest/newest alone. |
| D3-02 | P0 | 28,480 | distinct IDs with multiple normalized texts | Compare article structure and main content; retain rejected versions and reasons. Final selection follows cleaning QA. |
| D3-03 | P0 | 1,105,160 | collected records | Use stored title/desc and available HTML/body bylines; keep publication/date/author roles separate and null when unknown. |
| D4-01 | P0 | 2,750 | distinct IDs with no nonempty text version | Separate offline-recovery candidates from empty-only records. Exclude irreparable versions from content indexing, without recrawling. |
| D4-02 | P0 | 84 | flagged records, not all confirmed binary | Quarantine confirmed image/binary redirects; never guess lost characters or call it ordinary HTML cleanup. |
| D4-03 | P1 | 257,117 | records with a lexical UI marker | Validate block positions and counterexamples; retain substantive article and medical caution paragraphs. |
| D4-04 | P1 | 212,401 | records with repeated normalized lines | Keep clinical repetitions/lists; trim only proven contents/navigation/related-story spans. |
| D4-05 | P1 | 841 | records with heuristic block-page signal | Distinguish real gates from reCAPTCHA footer in an otherwise valid article. Do not reject by marker alone. |
| D4-06 | P1 | 7,845 | records flagged by current word-overlap heuristic | Use CJK-aware diagnostics and review; shared topic substrings can make the current flag a false positive. |
| D5-01 | P1 | 852,995 | distinct IDs with normalized text/block agreement | Map exact original spans first; support h/p/tr blocks and numbered headings inside p; preserve Q&A, conditions and numbers. |

Counts of heuristic flags identify investigation candidates. They are not counts of confirmed extraction errors. The groups overlap.

### 1. Import first; preserve all versions

Build immutable input_record/document_version/document_selection tables with record/doc/version links and rejected-version reasons. 
Distinguish same-ID retries from cross-ID aliases. Store all versions before selecting one. Prefer structurally complete article content over longer navigation-heavy text; do not choose newest crawl time blindly.
Use normalized hashes for comparison only; an alias must retain its own verbatim source spans. Do not merge translated documents because they discuss the same topic.

### 2. Metadata requires roles and provenance

| Field | Present records | Missing-column records | Other states |
| --- | --- | --- | --- |
| abstract | 0 | 1105160 | 0 |
| authors | 0 | 1105160 | 0 |
| canonical | 920581 | 853 | 183726 |
| category | 0 | 1105160 | 0 |
| desc | 987249 | 853 | 117058 |
| method | 1101858 | 853 | 2449 |
| published_date | 0 | 1105160 | 0 |
| source | 1105160 | 0 | 0 |
| title | 1101648 | 853 | 2659 |
| ts | 1105160 | 0 | 0 |
| updated_date | 0 | 1105160 | 0 |

Retain raw and normalized title/description separately. A source description is not automatically an abstract. Inspect available stored HTML for meta/JSON-LD candidates, and body bylines only where the domain format supports them.
Do not infer publication date from crawl ts, the latest date in a related feed, or a patient admission date. Do not infer authorship from a cited doctor or a webcast featured expert. Store candidates and confidence rather than inventing values.
The reviewed familydoctor example has an old article date and newer feed dates; the reviewed clinical-case example has a patient timeline.

### 3. Repair offline and remove noise by region

Keep source text/blocks/HTML unchanged. Derive a new evidence version with kept/removed spans and rule revisions. Use available blocks/HTML for repair; when absent, mark partial/unavailable instead of generating missing paragraphs.
For VOV, distinguish article content from reCAPTCHA/comment/related-feed tails. For cnkang/familydoctor, inspect related/latest/hot-article boundaries. For 120ask, preserve question and answers while removing only validated standalone controls.
For news sites, preserve health-system/news content and numerical information; sponsored or lifestyle content is metadata, not proof that acquisition failed. For Vinmec, review booking-service footers without removing citations or clinically meaningful instructions.
Some unrelated teasers occur inside an article rather than only at its tail. A cleaned chunk must not fabricate a continuous original span across a removed teaser.

### 4. Preserve medical meaning and source fidelity

Before/after fixtures must preserve negation, dose/units, ranges, dates with clinical roles, laboratory notation, lists/tables and Q&A links. Clinical cautions are not generic boilerplate.
Do not correct questionable source claims, infer omitted bullet lists, reconstruct lost image bytes or silently change numerical notation. Retrieval evidence must remain attributable to the source.
Normalized text/block equality does not establish exact source offsets. Validate exact slices for each output version before releasing chunks.

### 5. Select chunk policy from actual structure

Observed total blocks: 34,658,787; kinds: `{"h": 5581196, "p": 28782198, "tr": 242270, "dt": 53089, "pre": 34}`.
Use structural chunks when headings are reliable. All-p clinical cases and some Chinese articles need reviewed numbered-heading detection or a sentence/window fallback. Preserve table-row text without inventing unavailable column structure.
Keep short Q&A together; for long answers use mapped windows and context-only question prefixes. Use fixed windows as a baseline and semantic chunking as a later controlled experiment.
The spec defaults (target 384, max 512, overlap 64 model tokens) remain starting experiments. Measure actual model-token lengths after cleaning; whitespace units and characters are not equivalent to embedding tokens.

## QA gates and remaining review

- Automated full-scan accounting and source-checksum checks must pass before release of this audit.
- 600 domain-stratified random records and separate targeted examples are prepared. Their probabilities/weights apply only to the random subset.
- 26 source-excerpt cases across 20 domains were reviewed by the assistant for structural issues. This is not independent human or clinical validation.
- Qualified human/independent review of the sample packet remains pending; do not claim the spec's 98% main-content QA gate has passed.
- Rules need domain-specific positive/negative fixtures and held-back examples, with zero source-offset or meaning-preservation errors.
- Document-store/schema implementation can proceed. Final cleaning policy and working-corpus freeze require the corresponding regression/review evidence.
- No recrawl, embeddings, indexing or submission generation is part of this delivery.

## Supporting artifacts

- [Source review findings](REVIEW_FINDINGS.md), [assistant case data](assistant_source_reviews.jsonl).
- [Document quality by domain](document_quality_by_domain.csv), [issues by domain](issues_by_domain.csv).
- [Metadata availability](metadata_availability.csv), [content structure](content_structure_by_domain.csv).
- [Implementation backlog](IMPLEMENTATION_BACKLOG.csv), [version-selection priority](version_selection_priority.csv).

## Additional counterexamples for processing fixtures

- Preserve English or mixed passages found outside PubMed: MEDLATEC has an English article, while a cnkang case has a Chinese heading and English body. Latin-script medical identifiers or references alone do not establish English narrative.
- Reject confirmed short error placeholders even when HTTP status is 200; the reviewed Baonghean case contains only `404 Not Found` and `nginx`. Preserve the distinct counterexample of a valid short Q&A.
- Source consistency is verified for all 600 random packet samples plus targeted records. These checks do not replace independent main-content/clinical review.
