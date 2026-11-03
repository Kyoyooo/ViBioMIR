# Dense candidates to a new S09 submission

Open `colab/stage9_dense_recovery_colab.ipynb` directly in a fresh Colab runtime.
Do not paste notebook JSON or mix cells with the previous expand-pool notebook.
All support code is embedded; no separate Python upload is required.

Default inputs under `/content/drive/MyDrive/ViBioMIR`:

- `stage9_rerank_colab.zip`: verified frozen S09 code, policy and source snapshot.
- `dense_stage8_bge_structure/bundles/worker_*.zip`: original structural text,
  batch metadata and Stage-8 plan. Only `chunks/` payloads are restored locally.
- `dense_stage8_bge_structure/query_results_v1/results`: signed dense candidates.
- `Data.zip` or extracted official query/links Parquet files.

This is a dense-only experiment, not the original hybrid pool expansion. BM25,
semantic chunks, `artifacts.zip`, and completed old rerank workers are not required.
The existing embeddings are not regenerated. The pinned reranker is downloaded
by the frozen worker and scores the new candidate pool; old hybrid scores are
not imported. Top 400 candidates per query are reranked with output limits
120 documents / 120 chunks / 6 per document / IoU 0.8.

## Execution

1. Run setup, frozen-code extraction and official-file verification.
2. Inspect source bundle metadata and candidate file presence before large copies.
3. Restore source payloads; per-batch checksums are checked and existing matching
   files are reused. Source metadata is written only after restoration completes.
4. Adapt dense results into the S09 candidate contract. Every input record is
   signature-checked, query-bound and checked against the source text. The new
   adapter report describes integrity and mapping validation, not relevance.
   Original dense reports are not modified or marked passed.
5. Prepare S09 packets, save `prepared.zip` to the new Drive run directory.
6. Install GPU requirements, run the five-query worker pilot. Examine elapsed
   time before changing `STOP_AFTER_QUERIES=None` in a separate cell and rerunning
   only the worker cell. Timing is not estimated from the GPU subscription tier.
7. After the worker completes all queries, run original-source validation and
   packaging. The notebook downloads a ZIP containing only `submission.json`.
   Website submission remains manual.

Keep the same run configuration when resuming. Interrupted source files are
rechecked; corrupted/partial files are recopied. A runtime reset requires local
source restoration and candidate validation again. Prepared packets restore from
`prepared.zip` with their exact gzip bytes. Worker directories are keyed by the
packet hash list so scores cannot be mixed across packet rebuilds. Gzip files
rebuilt at another time may have different hashes even when their text is equal.
Completed query scores restore from the matching worker backup on Drive.
Do not run concurrent writers into the same output directories. If changing
candidate version, limits or policy, use a different `RUN_NAME`.

The corpus and temporary preparation need substantial local disk. Restoration
checks missing compressed source payload size plus an 8 GiB reserve; this reserve
is not a guarantee that every later model/preparation artifact will fit.
Progress messages run every 30 seconds during long phases, and batch counters
show completed work. A heartbeat indicates activity, not a completion estimate.

## Evaluation boundary

Technical checks do not measure F2 or the official LCS metric. Original chunk
text is preserved, with no automatic shortening. No official new-LCS evaluator
or gold answers have been supplied. Compare submissions under the same scoring
phase and retain the existing best ZIP. No improvement over 0.1626 is established.

Offline checks: `test_dense_recovery.py` exercises source restoration/resume,
missing/wrong bundles, corrupted payloads, wrong candidate mappings and notebook
syntax. `test_dense_recovery_pipeline.py` runs the real S09 preparation, worker
resume, validation and ZIP packaging on a small fixture with a synthetic scorer.
These checks do not run a real Colab GPU or the owner's complete corpus.
