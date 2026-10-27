# Stage 5 — human QA, full fixed baseline and comparisons

Delivery date: 2026-10-27. This delivery provides code, fixtures, configuration
and manual run instructions. **The new tests and pipelines have not been run
by the assistant.** Previously validated structural/semantic stores remain the
starting point; their measured results are in [the handoff](STAGE5_RESULTS_AND_HANDOFF.md).

## Current route to S06 — owner decision, 2026-10-27

**No additional stage-5 processing is required now for the unchanged validated
snapshot. Proceed to S06 implementation.** The owner has deferred comparisons,
measurement campaigns and extended human/clinical review until after the
S07–S09 retrieval implementations can produce candidate pools.

Reuse these existing artifacts without rebuilding them:

| Artifact | Local path | Existing evidence |
| --- | --- | --- |
| Selected document packets | `artifacts/chunk_inputs_stage5_full/` | Complete, 855,745 document rows |
| Structural chunks | `artifacts/chunks_stage5_full_structure/` | Full validation passed, 5,512,624 chunks |
| Semantic chunks | `artifacts/semantic_parallel_stage5_full/merged/` | Full validation passed, 9,036,279 chunks |
| Tokenizer | `artifacts/tokenizers/bge_m3_stage5/` | Pinned local registry |
| Parameters | `configs/chunking_stage5.json` | Preserve for reproducible downstream inputs |

Keep the matching stage-3/4 stores and existing validation reports. S06 labels
should be derived metadata over the selected documents, with source IDs and
versions preserved; they must not rewrite chunk evidence. Retain both chunk
strategies as experimental candidates. Neither is declared the quality winner.

**Skip sections 1–8 for the present handoff.** They are retained as the later
QA/experiment runbook, not an ordered prerequisite checklist for S06. In
particular, do not run the QA catalog/sample/export, full fixed build, intrinsic
scan, benchmark authoring/BM25 runs, or CPU/GPU parameter pilots now. There is
no new GPU job or Colab upload for this transition. Run the supplied fixture
tests when those tools are activated later; ordinary functional tests for new
S06/retrieval implementation still belong to their respective manual runbooks.

This is permission to continue engineering with a pinned experimental snapshot,
not final S05 acceptance. Preserve `human_qa=pending`,
`selection_is_final=false`, `ready_for_retrieval=false`. Existing technical
validation remains valid for unchanged data; restore/source/config changes
must be checked by the consuming pipeline rather than bypassed. No repeated
full validation is requested solely to move to the next implementation stage.

After retrieval is implemented, resume the relevant sections below in S10:
review source/chunk/retrieval failures, create pooled labels, compare chunkers
and output policies, and run parameter pilots if justified. A full fixed build
remains a separate optional experiment; a controlled pilot can precede deciding
whether it is useful. S06's 300-document ICD audit/calibration is also deferred
to that evaluation phase; preserve unknown/multilabel outputs and keep ICD
retrieval boosts disabled until dev evidence supports them. Select/tune on dev,
freeze settings, then evaluate holdout; final QA/release gates remain pending.

## Deferred execution order and resources

Run from the repository root. Each command below is owner-run. The global
`--config` option for `stage5_review` and `stage5_experiments` goes **before**
the subcommand. Defaults come from [stage5_workflow.json](../configs/stage5_workflow.json).
Inspect these paths first; edit a copy of the workflow for a new experiment.

| Step | Output | Compute |
| --- | --- | --- |
| Fixture/regression checks | `artifacts/stage5_review_tests/` | Local CPU; no model download |
| Catalog and frozen QA sample | `artifacts/stage5_qa_v1/` | Streaming local CPU and SQLite |
| Full fixed chunks and full validation | `artifacts/chunks_stage5_full_fixed/` | Local CPU; pinned tokenizer, no LLM |
| Source/chunk review and clinical decisions | QA HTML, CSV and report | Humans; relevant language and clinical competence |
| Intrinsic comparison | `artifacts/stage5_comparison_v1/` | Local CPU; optional disk-backed duplicate counting |
| Local benchmark and reference BM25 comparisons | Owner-selected `artifacts/local_eval_v1/` | Local CPU, explicitly bounded document subset |
| Fixed/structural parameter pilots | `artifacts/stage5_pilots_v1/` | Local CPU |
| New semantic threshold pilots | Same pilot root; separate outputs | CUDA GPU on Colab; existing notebook |

No new notebook is needed for CPU operations. New semantic pilots reuse
[stage5_full.ipynb](../colab/stage5_full.ipynb) with separate paths per variant.
Existing full semantic chunks can be compared without running the encoder again.

## 1. Deferred: run the checks before using these tools

```bash
source .venv-stage5/bin/activate
python -m pip install -r requirements-stage5-review.txt
python scripts/run_stage5_review_tests.py
```

Installation needs network access only if dependencies are missing. The runner
uses local fixtures and, when present, the existing registered tokenizer. It
disables CUDA integration tests and writes a new `run-NNNN/tests.log` and
`RESULTS.json`, plus `LATEST.json`. Stop and inspect failures before processing.
The fixture checks include Unicode/source offsets, negation and dosage
preservation, resume, sampling, independent review, exact LCS, macro F2,
benchmark leakage checks and controlled comparisons. A passed fixture suite
does not certify the corpus clinically.

## 2. Deferred: build a catalog and freeze the QA sample

```bash
python -m vibiomir.stage5_review catalog --resume --stop-after-batches 2
python -m vibiomir.stage5_review catalog --resume
python -m vibiomir.stage5_review sample
```

The catalog reads every prepared document and obtains its URL from the indexed
stage-3 version table. It records batch location, selected versions, language
hints, domain, format hints, availability and risk flags. It does not recrawl
or run an LLM. Batch transactions support interruption/resume. Resume verifies
input gzip checksums and skips catalog rows already committed; it still needs
to read those compressed files to verify them.

The frozen sample contains a uniform deterministic random cohort of up to
600 eligible content documents, supplemented by at least three per available
domain/language hint/type and up to 100 risk cases. Supplements can overlap
the random cohort. The total may exceed 600. At least 20% of selected cases
require a second reviewer. Unavailable documents remain in catalog/population
counts, but are outside this content-quality estimate. Language hints require
human confirmation. Exclusions, if needed, must be an integer-ID JSON array
passed to `sample --exclude-ids-file` before freezing.

`sample.json` and `selected_doc_ids.json` are frozen evidence. Do not hand-edit
membership, hashes or source manifests. Repeating the same command is safe;
changed source/code/selection settings require a new `qa_dir` and catalog.

## 3. Deferred, optional: complete the full fixed baseline

```bash
python -m vibiomir.stage5_experiments full-fixed --stop-after-batches 1
python -m vibiomir.stage5_experiments full-fixed
```

The first command is an intentional pause/resume check. The second reuses
committed batches, builds remaining fixed chunks, and automatically runs full
technical validation with resumable validation checkpoints. Repeat the second
command after interruption. It uses the same 855,745-document prepared snapshot,
tokenizer and 384/512/64/48 configuration as the existing structural/semantic
stores. It does not rebuild those stores. Default CPU workers: two; reduce to
one if RAM is constrained. Runtime and full fixed chunk counts are unmeasured.

Successful outputs include `VALIDATION.json` with `validation=passed`, checkpoints
under `artifacts/validation_stage5/fixed/` and `artifacts/stage5_fixed_v1/REPORT.json`.
The report's elapsed time covers that invocation, not accumulated earlier
resume attempts. The existing smoke fixed store is preserved separately.

## 4. Deferred: review original content and available chunkers

Export the available validated strategies. A missing fixed store is recorded
and does not block review of structure/semantic. If the later experiment needs
all three strategies in one frozen review package, build fixed before export:

```bash
python -m vibiomir.stage5_review export
cp artifacts/stage5_qa_v1/reviews_template.csv artifacts/stage5_qa_v1/reviews.csv
cp configs/reviewers_stage5.example.json artifacts/stage5_qa_v1/reviewers.json
```

Copy templates only once; never overwrite completed reviews. Open
`artifacts/stage5_qa_v1/index.html` in a browser. Each case includes stored
source text/blocks, available stored HTML, exact cleaned regions, cleaning
transformations and strategy chunks with retrieval context. HTML source is
escaped and inert. Export verifies selected stage-3/4 slices and raw source
shards when HTML is used, so it can take time even for a small sample.

Replace example reviewer identities and competences with actual people in
`reviewers.json`. A configured `clinical_qualified=true` is a human declaration,
not an automatically verified credential. Refer to
[the annotation guide](STAGE5_ANNOTATION_GUIDE.md) for fields and decisions.
If a strategy is missing, export records that omission. Adding it later changes
the frozen evidence: use a new QA directory rather than silently changing cases
already reviewed.

```bash
python -m vibiomir.stage5_review summarize \
  --reviews artifacts/stage5_qa_v1/reviews.csv \
  --reviewers artifacts/stage5_qa_v1/reviewers.json \
  --technical-report artifacts/stage5_review_tests/LATEST.json
```

Unfilled rows remain pending. Completed rows must use registered, competent
reviewers. The report separates random-cohort pass rate/Wilson 95% interval
from risk/coverage findings, reports disagreements, issue counts and clinical
confirmation, and writes `decisions.jsonl`. The proposed 98% target applies
to the reviewed random cohort's main-content pass rate, not all sampled cases
or a claim that a confidence bound exceeds 98%. Missing reviews bias estimates.

The strict sample review gate requires all selected decisions accepted and
clinically confirmed, required independent reviews resolved, and the proposed
rate target met. Technical fixture evidence is reported separately. Even a
passed sample gate does not approve unreviewed documents or freeze the corpus.
Failures become explicit stage-4/chunk-policy fixes followed by a new version,
validation and review; decisions do not mutate original stores.

## 5. Deferred: compare intrinsic properties

```bash
python -m vibiomir.stage5_experiments compare
```

This streams the same documents from all three validated stores, checks
document/version alignment, and writes `REPORT.json` and `SUMMARY.csv` with
chunk counts, empty-document counts, token histograms/quantiles, short chunks,
context/evidence characters and repeated source-offset coverage. It supports
automatic resume through a disk-backed per-batch cache. Overlap is measured
in source characters, not a clinical sentence-quality score. Payload size is
the whole store's compressed gzip size, even when using `--ids-file`.

Exact normalized duplicate counting is optional and adds disk/CPU work:
copy the workflow, change `comparison_dir`, then run
`python -m vibiomir.stage5_experiments --config YOUR_WORKFLOW.json compare --exact-duplicates`.
It counts identical evidence groups without deleting or merging official IDs.
Different options require a new comparison directory. Statistics alone cannot
select the best retrieval strategy.

## 6. Deferred: build a human-authored local benchmark

Start with 30 questions to debug annotation; expand to the spec's 180-question
target with deliberate VI/EN/ZH source coverage (60/60/60) and clinical intent,
domain and ICD diversity. The template generator does not enforce those quotas
or write questions. Keep related cases, translations and origin documents in
one split; adjust its provisional train/dev/holdout assignment before labeling.

```bash
python -m vibiomir.local_benchmark init \
  --qa-dir artifacts/stage5_qa_v1 \
  --output-dir artifacts/local_eval_v1 --questions 30
```

Humans fill `queries.json` in Vietnamese and confirm the source. Prepare the
same explicit corpus subset for every run, initially QA `selected_doc_ids.json`.
Optional unlabeled candidate-pool run, repeated for each strategy and split:

```bash
python -m vibiomir.reference_bm25 --strategy structure \
  --queries artifacts/local_eval_v1/queries.json --split dev \
  --ids-file artifacts/stage5_qa_v1/selected_doc_ids.json \
  --output-dir artifacts/local_eval_v1/pool_structure_dev

python -m vibiomir.local_benchmark pool \
  --queries artifacts/local_eval_v1/queries.json \
  --predictions artifacts/local_eval_v1/pool_structure_dev/predictions.json \
  --output-file artifacts/local_eval_v1/pooled_judgments.csv

python -m vibiomir.local_benchmark export-evidence \
  --ids-file artifacts/stage5_qa_v1/selected_doc_ids.json \
  --output-dir artifacts/local_eval_v1/evidence
```

Repeat `--predictions` for all available chunkers/retrievers/splits to form the
union pool. Pool before filling judgments, or manually merge a new pool into
the reviewed file without losing labels. Pooling never labels a candidate as
positive or negative. Without predictions, `pool` still creates origin-document
rows. If pool IDs extend beyond the QA subset, use its generated
`pooled_judgments.doc_ids.json` for an additional evidence export and choose
one common, explicitly recorded retrieval corpus containing all gold documents.

Fill pooled document judgments and independent gold spans according to
[the annotation guide](STAGE5_ANNOTATION_GUIDE.md), including at least 20%
independent second review across document/span labels. Then compile:

```bash
python -m vibiomir.local_benchmark compile \
  --queries artifacts/local_eval_v1/queries.json \
  --judgments artifacts/local_eval_v1/pooled_judgments.csv \
  --spans artifacts/local_eval_v1/spans.csv \
  --evidence-dir artifacts/local_eval_v1/evidence \
  --output-dir artifacts/local_eval_v1/compiled
```

Use a new compiled output directory when labels change. `--allow-incomplete-review`
only exports a draft; draft labels cannot be used by the comparison runner.
This benchmark is local, pool-relative evidence, not the official competition
test labels. Do not train or tune on the official 1,200 test queries.

## 7. Deferred: run controlled retrieval comparisons

The included BM25 is a bounded, in-memory CPU reference with Unicode/Han
analysis. It enables local comparisons before the production S07/S08 indexes;
it does not provide dense cross-language retrieval. Default cap: 25,000 chunks.
Exceeding the cap raises a clear error instead of truncating a strategy. Raise
`--max-chunks` consistently or choose a smaller shared document subset, retaining
all gold positives. Increasing the cap also increases Python memory usage.

Run once for each of `fixed`, `structure`, `semantic`, replacing both strategy
and output directory below. Keep corpus IDs, BM25 parameters and output caps
identical:

```bash
python -m vibiomir.reference_bm25 --strategy fixed \
  --benchmark artifacts/local_eval_v1/compiled/benchmark.json --split dev \
  --ids-file artifacts/stage5_qa_v1/selected_doc_ids.json \
  --output-dir artifacts/local_eval_v1/runs/fixed_dev

python -m vibiomir.retrieval_evaluation compare-runs \
  --benchmark artifacts/local_eval_v1/compiled/benchmark.json --split dev \
  --run fixed=artifacts/local_eval_v1/runs/fixed_dev \
  --run structure=artifacts/local_eval_v1/runs/structure_dev \
  --run semantic=artifacts/local_eval_v1/runs/semantic_dev \
  --baseline fixed --output-dir artifacts/local_eval_v1/comparison_dev
```

The scorer computes per-query document/chunk P/R/F2 and their macro means.
Chunks match only within the same query/doc using exact ordered token LCS,
with overlap at least 40% of the reference. Multiple predictions/reference
matches are accounted for separately. The report includes language/intent/ICD/
domain slices with support, lowest-F2 queries, unjudged results, paired-query
bootstrap intervals and measured run latency/cost metadata. Upstream candidate
recall is unknown (`null`); it is not inferred from final outputs.

`whitespace_proxy_v1` is the default **local** tokenization profile. Repeat in
a separate comparison directory with `--profile cjk_character_sensitivity_v1`
to check Chinese tokenization sensitivity. Neither profile is asserted to be
the organizer's tokenizer. Duplicate/zero-case conventions are explicit in
each report. Dev results with unjudged outputs remain provisional.

Choose configurations on dev and record the human choice:

```bash
python -m vibiomir.local_benchmark freeze-dev \
  --report-dir artifacts/local_eval_v1/comparison_dev \
  --run fixed --run structure --run semantic \
  --output-file artifacts/local_eval_v1/frozen_selection.json
```

Generate holdout runs with the identical parameters and shared corpus, using
`--split holdout` and new `*_holdout` directories. Repeat `compare-runs` with
those directories, `--split holdout` and
`--frozen-selection artifacts/local_eval_v1/frozen_selection.json`. All returned
holdout documents must be independently judged. If additional annotation is
needed, version the compiled benchmark and regenerate run metadata using that
benchmark; unchanged dev labels/configurations are required. Holdout results
must not drive retuning. Preserve both document and chunk F2, confidence
intervals, slice support, latency and storage costs when making a decision.

Parameter experiments use `--experiment-family chunk_parameters`, output-cap
experiments use `output_policy`, and later retriever experiments use `retriever`.
The runner checks the controlled configuration fields and rejects changing
multiple parameter families together. External retrievers must export the
same `run.json`/prediction contract; production dense/index support is later work.

## 8. Deferred: run bounded parameter pilots

```bash
python -m vibiomir.stage5_experiments prepare-pilots --documents 100
python -m vibiomir.stage5_experiments run-cpu-pilots
```

Preparation selects accepted QA cases deterministically and freezes the
portable input/plan/configuration. Fewer than 100 accepted cases requires
lowering the count or completing review. `--allow-unreviewed` is an explicit
debugging option; outputs retain that limitation. Default variants:

| Family | Variants | Compute |
| --- | --- | --- |
| Baseline | Fixed, structure, semantic at current configuration | CPU / CPU / CUDA |
| Fixed target size | 256, 512, with other fields held fixed | CPU |
| Structural overlap | 0, 128 | CPU |
| Semantic threshold | 0.55, 0.75 | CUDA |

Values are derived relative to the configured baseline and validated; inspect
generated `plan.json` for the exact experiment. The CPU runner builds and fully
validates six default CPU variants, reusing completed batches on rerun.

For each of the three semantic variants:

```bash
python -m vibiomir.stage5_experiments bundle-semantic-pilot --variant semantic_base
python -m vibiomir.stage5_experiments bundle-semantic-pilot --variant semantic_threshold_0_55
python -m vibiomir.stage5_experiments bundle-semantic-pilot --variant semantic_threshold_0_75
```

Each command creates `artifacts/stage5_pilots_v1/colab/NAME.zip`; an existing
ZIP is protected from overwrite. Upload those ZIPs to Drive and the existing
`colab/stage5_full.ipynb` to Colab. No raw corpus or source SQLite upload is
needed for chunk construction. Select a CUDA GPU (T4/L4/A100; no TPU support).
For each notebook session set:

```python
NAME = 'semantic_threshold_0_55'  # choose the matching variant
BUNDLE = Path('/content/drive/MyDrive/ViBioMIR/pilots') / (NAME + '.zip')
STRATEGY = 'semantic'
PROJECT = Path('/content') / ('vibiomir_pilot_' + NAME)
RUN_DIR = Path('/content') / ('chunks_pilot_' + NAME)
BACKUP_DIR = Path('/content/drive/MyDrive/ViBioMIR/pilots') / NAME
WORKERS = 2
ENCODER_BATCH_SIZE = 8  # conservative T4 start; try 32 on A100 after timing
STOP_AFTER_BATCHES = 2
```

Use the notebook's normal install, preflight, run and final validation/backup
cells. After timing, set `STOP_AFTER_BATCHES=None` and rerun the run cell;
committed batches resume. Distinct variant paths prevent checkpoint collision.
No structural seed is used: semantic boundaries are an independent experiment.
At most three legitimate Colab sessions/accounts can run variants concurrently.
If CUDA OOM occurs, reduce the encoder batch size and follow the notebook's
execution-identity/resume rules; never edit a manifest to bypass them.

Download each complete `BACKUP_DIR` to its exact `output_dir` in local
`plan.json`, with manifest, validation, model registry and all batches intact.
Then use generated `workflow.json` for intrinsic comparison of the three base
variants. For parameter retrieval, each `workflow_NAME.json` maps that variant
to the corresponding strategy. Build the pilot catalog with
`python -m vibiomir.stage5_review --config artifacts/stage5_pilots_v1/workflow.json catalog --resume`
before using reference BM25 on that pilot corpus. Use a common pilot ID list
from `plan.json`'s input manifest selection, author gold for that corpus and
compare one parameter family on dev. Do not compare a 100-document pilot
against a full-corpus run or reuse benchmark corpus signatures across them.
Use `run-cpu-pilots`, rather than `full-fixed`, with generated pilot workflows.

## Outputs, sharing and completion boundary

Source changes are listed in [this delivery's inventory](../sharing/stage5_review_source_files.txt).
Upload future generated data according to
[the planned Drive list](../sharing/drive_stage5_review_planned.txt) **after**
the owner has generated and checked them. Existing closeout inventories and
commit receipts remain historical. No new commit, push, pipeline execution or
clinical certification is part of this code-only delivery.

Acceptance evidence for experiments actually selected consists of the owner-run
test report; fixed validation when that baseline is built;
completed, resolved human/clinical review; intrinsic reports; source-backed local
labels; dev/frozen holdout comparisons and recorded selection rationale. Keep
`human_qa=pending`, `selection_is_final=false`, `ready_for_retrieval=false` in
original stores. These tools do not perform corpus freeze, ICD assignment,
production indexing, dense retrieval or official dashboard submission.
