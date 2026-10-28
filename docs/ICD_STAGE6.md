# S06 — provisional ICD-10 chapter metadata

Updated 2026-10-29: the full rules store contains 855,745 annotations across
1,712 batches. Its existing full evidence validation and current snapshot seals
have been verified; all 14 S06 tests passed during the authorized closeout.
Read the [baseline handoff](STAGES6_8_BASELINE_HANDOFF.md) for evidence and next
steps. Semantic/combined ICD and clinical acceptance remain deferred to S10.
No stage-5 fixed baseline, catalog, QA export or comparison is required here.
The commands below reproduce or resume a run; the current rules store is complete.

## Fast route now: local rules, then S07

Run from the repository root. CPU rules use only Python's standard library;
the existing stage-5 environment also supports packaging. The offline semantic
fixtures need NumPy, available through the small stage-6 requirement file.

```bash
source .venv-stage5/bin/activate
python -m pip install -r requirements-stage6.txt
python -m unittest discover -s tests -p 'test_stage6*.py' -v

python -m vibiomir.icd_pipeline build --stop-after-batches 1
python -m vibiomir.icd_pipeline build
python -m vibiomir.icd_pipeline validate
```

The first build is an optional one-batch smoke/resume check. The second finishes
remaining batches. Defaults:

| Setting | Path / behavior |
| --- | --- |
| Input | `artifacts/chunk_inputs_stage5_full/`, already complete |
| Output | `artifacts/icd_stage6_rules/` |
| Configuration | `configs/icd_stage6.json` |
| Taxonomy and seed phrases | `taxonomy/icd10_chapters.json` |
| Processing | One input gzip batch at a time; no raw corpus/SQLite rescan |
| Resume | Automatic when output has a matching manifest; no `--resume` needed |
| GPU / LLM | Neither required for rules |

Expect one ledger row per input document, including unavailable documents with
unknown labels. Counts derive from the input manifest rather than a URL quota.
For the current snapshot this is 855,745 rows / 1,712 batches, with 461,658
labeled documents and 394,087 unknown documents. This is topic coverage,
not clinical accuracy; no build duration is claimed. A completed build is followed by technical
validation; after that, the rules metadata is usable provisionally during S07
implementation. Semantic/combined full execution is optional for this fast
route and can be scheduled with the later GPU work. Full S06 acceptance remains
pending the deferred clinical audit/calibration.

If interrupted, repeat the same build command. Completed batch checksums and
geometry are checked and those batches are reused. An uncommitted batch is
recomputed. Changing mode, taxonomy, code, input snapshot or settings requires
a new output directory; never patch a manifest hash. Resume reads compressed
input/output bytes to verify checksums but does not reclassify committed rows.
`chunks=0` in the shared batch envelope means this store contains annotations,
not retrieval chunks; use `documents` and `annotation_records` for S06 totals.

## What the implementation does

The taxonomy pins the 22 chapter IDs/ranges in the mentor appendix and checks
their structure against the [WHO 2019 browser](https://icd.who.int/browse10/2019/en/)
and the [BfArM ICD-10-WHO 2019 chapter table](https://klassifikationen.bfarm.de/icd-10-who/kode-suche/htmlamtl2019/index.htm).
English/VI/ZH topic descriptions and seed phrases are project-authored; they
are not official translations or a clinically reviewed, exhaustive dictionary.
This is chapter grouping, not detailed code assignment. ICD-10-CM and later
country/COVID code extensions are not silently merged into this version.

| Mode | Compute | Output methods |
| --- | --- | --- |
| `rules` | Local CPU | Rules |
| `semantic` | CUDA GPU | Semantic |
| `combined` | CUDA GPU | Rules, semantic and combined in the same pass |

Rules match VI/EN/ZH seeds with word boundaries for non-Han phrases and preserve
original character spans through matching normalization. Title matches have
strength 1.0, body matches 0.8; the default threshold is 0.7. The current input
packets expose title and selected body regions, not separate abstract/category
fields. Those richer fields are not invented. Each field is scanned up to
200,000 source characters and the result records whether the scan was limited.
The dictionary is a conservative starter, not a trained biomedical classifier.

Local negation/uncertainty prefixes suppress a positive rule label and remain
recorded as evidence. This is a bounded heuristic, not complete clinical
assertion detection. The output states topic mention, never patient diagnosis.
The symptom-only guard keeps chest pain from becoming coronary disease via
semantic similarity. It also conservatively blocks new disease chapters when
only symptom seeds were recognized, which may reduce recall for unrecognized
disease terminology; the later audit must quantify that limitation.

Semantic mode reuses the pinned public [BGE-M3 revision](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181),
CLS pooling, L2 normalization and FP16. One model instance computes embeddings
for 66 chapter descriptions once per invocation. At most four source segments
per document, including title when present and spread body windows, are encoded.
Each starts from at most 1,600 source characters and retains at most 448 tokens
before special tokens; the hard encoder limit is 512. This is an explicitly
bounded sample, not exhaustive representation of long documents. Sampling does
not alter source evidence or rebuild stage-5 chunks.

For each chapter, its best segment/prototype cosine becomes the score. Defaults
0.65 similarity and 0.05 margin are **uncalibrated engineering settings**. A sole
above-threshold chapter with an insufficient runner-up margin abstains; multiple
above-threshold chapters can be retained, up to four. Excessive labels abstain
rather than silently forcing a top label. All 22 scores persist when encoding
produces evidence; only leading/selected candidates retain their source text.
Cosine and rule strength are not probabilities: `confidence` remains null.

Combined mode retains rule labels with optional semantic support. Disjoint
nonempty rule/semantic selections produce unknown and an explicit disagreement
reason. Missing evidence, low scores, uncertainty and symptom safeguards can
also yield unknown. Selected labels retain evidence and method provenance.
No unknown document is dropped and no official IDs are merged.

## Optional semantic/combined run on Colab

Only this branch needs a GPU. Use the existing local stage-5 environment to
verify/package its pinned tokenizer:

```bash
python -m vibiomir.icd_bundle
```

This creates `artifacts/stage6_icd_colab.zip` from current source, stage-6 tests,
configuration, taxonomy, existing prepared gzip batches and tokenizer. It does
not rerun preparation, include chunk stores/raw Data/SQLite, or include weights.
An existing ZIP is protected; choose a new `--output` filename when repackaging.
The canonical taxonomy path must be retained inside copied settings.

1. Upload that ZIP to Drive, e.g. `MyDrive/ViBioMIR/stage6_icd_colab.zip`.
2. Open [stage6_icd.ipynb](../colab/stage6_icd.ipynb) on Colab and select CUDA GPU.
3. Edit `BUNDLE`, `BACKUP_DIR` and `RUN_DIR`; default `MODE='combined'` emits all
   three methods without depending on an earlier CPU rules run.
4. Execute setup and fixture/preflight cells. The first model run downloads
   the pinned public weights; the registry records actual parameter count,
   revision, pooling, dtype and hardware. The code enforces the 15B cap.
5. Run the one-batch pilot, then set `STOP_AFTER_BATCHES=None` and rerun the run
   cell. Completed batches are reused. No threshold comparison is requested.
6. Once complete, run the validation/backup cell and download the entire output
   to `artifacts/icd_stage6_combined/` locally, preserving its directory layout.

| GPU | Starting encoder batch | Padded-token budget | Documents per wave |
| --- | ---: | ---: | ---: |
| T4 / L4 | 16 | 8,192 | 16 |
| A100 | 64 | 32,768 | 32 |

These are starting settings, not throughput claims or RAM usage targets.
CPU prepares bounded text windows; one GPU instance performs length-bucketed
microbatches and splits/retries on CUDA OOM. Vectors are temporary per wave,
not stored for the full corpus. A single session is sufficient; two notebooks
must never write the same backup directory. TPU and multi-Colab sharding are
not implemented for this stage. The project permits up to three accounts but
does not require using all of them for this workload.

Keep computation/checkpoints on Colab local disk; Drive holds copied immutable
batches, manifest and registry. Each completed batch is backed up. After a
runtime reset, remount/re-extract the same bundle and run the same configuration;
the builder restores the backup automatically, including the registry. A batch
interrupted before commit is recalculated. Hardware/microbatch settings can
change on resume; execution history records them, and floating-point boundary
decisions may vary for newly computed rows. Clinical calibration remains pending.

Optional local revalidation after download uses CPU, with no model load:

```bash
python -m vibiomir.icd_pipeline validate \
  --output-dir artifacts/icd_stage6_combined
```

This is a new S06 technical check, not a repetition of full S05 validation.
It verifies checksums, complete document/version accounting, exact evidence
spans, taxonomy labels, selection policy and provisional flags. Rules are
recomputed; semantic vectors are not regenerated and clinical accuracy is
not established. Keep the exact shipped configuration/code for validation.

## Output contract and downstream integration

Each store contains `manifest.json`, `VALIDATION.json` after validation,
`batch-NNNNNN/batch.json` and `documents.jsonl.gz`. GPU runs additionally retain
`MODEL_REGISTRY.json` and manifest execution history. One gzip JSONL row contains:

```json
{
  "doc_id": 123,
  "derived_version_id": "selected-cleaned-version",
  "parent_version_id": "raw-version",
  "availability": "content_candidate",
  "scope": "document_topic_not_patient_diagnosis",
  "annotations": {
    "rules": {
      "method": "rules",
      "labels": ["IV"],
      "unknown": false,
      "confidence": null,
      "calibration": "pending",
      "human_reviewed": false,
      "retrieval_boost_enabled": false,
      "hard_filter_enabled": false
    }
  }
}
```

The example omits annotation IDs, scores/evidence and scan metadata for brevity.
Actual records include exact field/region offsets, source locators, text hashes
and candidate reasons. Join labels to chunks through **both** `doc_id` and
`derived_version_id`. Document-level labels inherited by a chunk are not new
chunk-level predictions. S07/S08 must not modify evidence or filter unlabeled
documents based on these labels. Use the labels initially for EDA/slices only.

For a supplied query/chunk text file, the same methods/schema can be used through
`tag-text` (rules default; semantic/combined additionally needs CUDA/tokenizer):

```bash
python -m vibiomir.icd_pipeline tag-text \
  --text-file YOUR_QUERY.txt --entity-kind query --entity-id 42 \
  --output-file artifacts/icd_query_42.json
```

Text annotations bind to the supplied file hash and declared entity ID. They
do not claim original-document provenance unless the caller supplies/retains
that mapping. Batch annotation of the official query set and retrieval boosts
belong to later retrieval integration; no automatic query-set processing occurs.

## Deferred work and sharing

The 300-document human ICD audit, calibration/test split, threshold selection,
clinical review, multilingual P/R and E09 boost ablation remain deferred to S10.
Descriptions/seeds must be reviewed then. No precision/recall, calibrated
confidence or complete A10 acceptance is claimed. Preserve source-store
`human_qa=pending`, `selection_is_final=false`, `ready_for_retrieval=false`.

GitHub: source, taxonomy, configuration, tests, notebook and this guide, listed
in [stage6_source_files.txt](../sharing/stage6_source_files.txt). Drive: generated
label stores with all batches/validation/registry; see
[drive_stage6_planned.txt](../sharing/drive_stage6_planned.txt). The full rules
store is present and verified; no semantic/combined GPU run is claimed here.
The authorized closeout creates local commits only; the owner pushes.
