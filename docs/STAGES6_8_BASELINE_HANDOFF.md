# S06–S08 baseline results and handoff

Observed on **2026-10-29**, following the owner's authorization to finish the
rules/lexical/BGE-M3 baseline. The owner subsequently deferred expensive full
S07 query generation. The existing lexical index is retained and verified.
No full-corpus rebuild or new GPU inference was needed for this closeout.

## Verified outputs

| Stage | Observed result | Canonical local directory |
| --- | --- | --- |
| S06 rules | 855,745 document annotations / 1,712 batches; sealed snapshot verified | `artifacts/icd_stage6_rules/` |
| S07 BM25/TF-IDF index | 5,512,624 chunks / 737,967 terms / 1,712 batches; sealed index verified | `artifacts/lexical_stage7_structure/` |
| S08 merged BGE-M3 vectors | 5,512,624 × 1,024 float16 vectors; complete source/mapping coverage | `artifacts/dense_stage8_bge_structure/merged/` |
| S08 query vectors | All 1,200 official queries; finite, normalized vectors and exact query identity checked | `artifacts/dense_stage8_bge_structure/query_vectors/` |
| S08 FAISS IVF-PQ | All 5,512,624 unique global row IDs indexed; file checksum and geometry checked | `artifacts/dense_stage8_bge_structure/index_ivfpq/` |
| S08 candidates | 1,200 query files × 500 candidates = 600,000 mapped candidates; no empty query | `artifacts/candidates_stage8_bge/` |

The shared retrieval corpus is the validated structural snapshot at
`artifacts/chunks_stage5_full_structure/`. S06 annotates the corresponding
prepared document inputs, including unavailable/unknown records; an annotation
row does not imply that the document contains usable retrieval chunks.

S06 has 461,658 documents with at least one chapter and 394,087 unknown
documents. Chapter labels are multilabel topic mentions, not diagnoses or
calibrated probabilities. These counts describe coverage, not accuracy.
ICD text is not inserted into `retrieval_text`; filters and boosts remain off.

The S06/S07 checks reuse their existing full technical validation and verify
the immutable snapshot seals, matching code/configuration, source identity and
batch/database accounting. S06 additionally checks input/output payload hashes;
S07 checks the database hash, analyzer registry, statistics and norm ledger.
They do not repeat unchanged corpus classification or source-mapping scans.
S08 checks cover the merged payload seals, query windows/model registry, FAISS
ID coverage and every candidate's source mapping and score order.

## What is ready next

S09 can consume the **dense candidate branch** now. Each `query-ID.json` is a
signed intermediate artifact with source chunk/version identity and scores;
it is not competition submission JSON. Preserve the matching merged mapping,
structural chunk store and frozen `plan.json` for resolving original evidence.

Full S07 BM25/TF-IDF candidate generation is **deferred by the owner** because
of local query cost. Neither `artifacts/candidates_stage7_bm25/` nor
`artifacts/candidates_stage7_tfidf/` is a completed output. Full hybrid/RRF
integration requires those candidates later. Dense-only S09 work does not.

Semantic/combined ICD, mBERT, full fixed-chunker comparisons, clinical QA,
ICD calibration, relevance evaluation and ANN recall@200 measurement remain
at S10. No F2, clinical accuracy or ANN recall is claimed here. The FAISS
training sample uses language **hints**, not verified language/domain strata.
Formal clinical/quality acceptance gates, including A10/A12, are still open;
pending human-QA, final-selection and readiness flags remain unchanged.

## Measured CPU work and tests

FAISS uses inner product, `nlist=4096`, `m=64`, `nbits=8`, `nprobe=32`, and
200,000 seeded corpus training vectors. Query vectors and relevance labels
are excluded from training. The final file is `checkpoint-001712.faiss`,
414,767,668 bytes (about 395.6 MiB).

| Executed job | Wall time | Peak process RSS |
| --- | ---: | ---: |
| Full IVF-PQ build | 14 min 22.66 s | 1,420,040 KiB |
| Dense candidate generation | 1 min 21.01 s | 461,184 KiB |
| Full candidate validation | 17.09 s | 224,620 KiB |

These are observations on this local CPU run, not performance guarantees.
Encoding was completed previously on Colab CUDA. Baseline closeout used CPU
only and reused those vectors. No GPU is required to read/query this index.

The executed S06/S07/S08 test suites ran **40 tests: 39 passed, 1 skipped,
zero failures/errors**. The skip is an optional mBERT CPU tensor-pooling
fixture because PyTorch is absent from the local CPU environment. Fast-tokenizer
regressions, the registered local BGE tokenizer fixture and new corruption
checks passed. See [execution evidence](../reports/stage678_baseline_20261029/README.md).

## Reproducing checks when inputs change

The above jobs are already complete; no rerun is needed for unchanged inputs.
The exact tested Linux/Python 3.13 CPU packages are pinned in
[requirements-baseline-cpu.txt](../requirements-baseline-cpu.txt). A separate
environment avoids changing the existing analyzer dependencies:

```bash
python3.13 -m venv .venv-baseline
source .venv-baseline/bin/activate
python -m pip install -r requirements-baseline-cpu.txt

python -m unittest discover -s tests -p 'test_stage6*.py' -v
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  python -m unittest discover -s tests -p 'test_stage78*.py' -v

python -m vibiomir.stage678_validation icd \
  --report artifacts/baseline_recheck/icd.json
python -m vibiomir.stage678_validation lexical-index \
  --report artifacts/baseline_recheck/lexical.json
python -m vibiomir.stage678_validation queries \
  --report artifacts/baseline_recheck/queries.json
python -m vibiomir.stage678_validation dense-index \
  --report artifacts/baseline_recheck/index.json
python -m vibiomir.stage678_validation candidates \
  --candidate-dir artifacts/candidates_stage8_bge \
  --report artifacts/baseline_recheck/candidates.json
```

These commands read complete stores and verify checksums; disk scans can take
time. They do not launch encoding or clinical evaluation. New build/search
instructions remain in [S06](ICD_STAGE6.md) and [S07/S08](RETRIEVAL_STAGES7_8.md).
For a new GPU encoding run use `requirements-stage8-colab.txt` and the supplied
notebook, rather than the CPU lockfile. Do not re-sign the existing frozen plan.

## Publication and cleanup

Small results, logs and dated manifest/validation copies belong on GitHub.
Full stores, mapping databases, vector payloads and source data stay outside Git.
The [run registry](../reports/run_registry/README.md) contains review copies,
not runnable checkpoints. Existing sharing inventories list the canonical
directories; no additional upload procedure is required for this handoff.

Incomplete optional S07 pilot outputs and unused pilot code were removed.
Successful stores, original archives, frozen bundles and owner-managed upload
snapshots were preserved. Transient project bytecode was cleaned separately.
No source dataset was deleted. Changes are committed locally, one file per
Conventional Commit; the owner performs `git push origin main`.
