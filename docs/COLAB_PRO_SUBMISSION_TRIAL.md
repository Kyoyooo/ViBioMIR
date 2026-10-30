# Colab Pro submission trial

Open `colab/stage9_colab_pro_submission.ipynb` in a fresh Google Colab runtime.
This is a bounded output-selection experiment: 40 documents, 80 chunks and four
chunks per document, using the existing S09 reranked pool. It does not implement
NER/ICD boosts, enlarge the candidate pool or promise a score improvement.

## Inputs

The default Drive root is `/content/drive/MyDrive/AIGuru`. Edit the first code
cell if your layout differs:

```text
AIGuru/
  stage9_rerank_colab.zip
  stage9_rerank_workers/
    stage9_worker_0.zip
    stage9_worker_1.zip
  Data.zip
```

Extracted `stage9_rerank_workers/worker_0/` and `worker_1/` directories work too.
Worker ZIPs can alternatively be directly under `AIGuru/`. Only the configured
number of workers in the frozen bundle is required. The notebook can recover
the S09 bundle and matching worker files from `artifacts.zip` if they are inside
that archive; older archives may not contain them. Stage-5 bundles are not
substitutes. It never extracts the entire corpus archive.

Official query and links files can be provided directly under
`Data/official/ViBioMIR/`, or selectively extracted from `Data.zip`. The query
file must match the prepared snapshot, and the links checksum is pinned to the
verified baseline. Missing or mismatched inputs stop execution rather than
silently substituting another run.

## Execution

1. Keep `RUN_MISSING_RERANK=False` when matching workers have completed. This
   path requires CPU only and does not download model weights.
2. If worker outputs are missing or incomplete, select a CUDA GPU and set
   `RUN_MISSING_RERANK=True`. The notebook finishes the frozen original S09
   workers sequentially on one GPU and backs up committed queries under
   `AIGuru/stage9_submission_resume/`. Do not run concurrent writers there.
   This mode scores the existing pool, not a new top-400 pool.
3. Run cells in order. Inspect the baseline/trial document and chunk counts.
4. The final cell saves and downloads `submission.zip`. Upload it manually to
   the competition. The notebook does not access the competition account.

After disconnection, restart from the first cell using the same bundle, inputs
and backup path. Completed query scores are checked and reused. If installation
requires a runtime restart, restart before importing project modules. The setup
cell deliberately rejects an already imported `vibiomir` package to prevent
mixing frozen source versions.

## Outputs and validation scope

```text
AIGuru/submission_trials/recall_40_80_<timestamp>/
  submission.json
  submission.zip
  baseline_reconstructed.json
  REPORT.json
```

Only `submission.zip` is submitted; it contains exactly one root-level
`submission.json`. The report records input hashes, selection settings, changed
queries, rejection reasons and documents without selected evidence. A fresh
timestamped directory is used for each export.

Validation checks the signed bundle/packets, matching worker/model identities,
finite scores, official IDs and query coverage, original packet text hashes and
offset lengths, selection limits, and ZIP roundtrip. It does not re-scan the
original Stage-5 store or measure relevance. Prepared-packet provenance is the
source integrity boundary for this portable workflow.

Six local offline tests cover Python syntax, selection and ZIP creation, and
rejection of corrupt text, ineligible IDs, mismatched queries and unchanged
output. They use fixture packets and stub worker I/O; they do not establish
Drive/GPU execution or full-data validity. Run them with:

```bash
python -m unittest discover -s tests -p test_colab_submission_notebook.py -v
```
