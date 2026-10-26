# Development and language conventions

These conventions apply to project-owned code, technical documentation, and
human-readable generated outputs. They document the English-language migration
agreed with the user on 2026-10-24.

## Language by artifact

| Artifact | Convention |
| --- | --- |
| Variables, functions, classes, modules, and new schema fields | English identifiers; follow the existing Python naming style |
| Comments, docstrings, CLI help, logs, exceptions, and test descriptions | English |
| README, task specification, development specification, architecture, and runbooks | English |
| Report headings, explanatory prose, chart titles, and generated technical labels | English |
| Conversation with the project owner | Vietnamese unless requested otherwise |
| Corpus text, queries, evidence, and multilingual fixture payloads | Preserve the original VI/EN/ZH text |
| Original Mentor Day summary, upstream dataset cards, downloaded source files, and quotations | Preserve the original source and its provenance |

English documentation does not authorize translating evidence or query text.
A report may contain multilingual source snippets or source-defined labels.
Language-detection patterns and medical examples must retain the characters
needed to test and process the actual languages.

## Compatibility and traceability

- Preserve existing file paths and public field names during language-only
  changes. `docs/BAI_TOAN_VIBIOMIR.md` retains its path for existing references.
- Preserve R/S/A/E identifiers, formulas, numerical thresholds, model IDs,
  revisions, and schemas. Update links and heading anchors when headings change.
- Use UTF-8. New project identifiers use descriptive English names; data values
  and official IDs are not renamed or translated.
- Label measured results, engineering defaults, user-supplied announcements,
  historical snapshots, and unverified assumptions explicitly.
- Keep original source files separate from project-authored interpretations.
  A translation of a source, if later requested, must be a separate derived file.
- Generate report prose in English from the report code. Regenerating a report
  from saved metrics is not a new corpus scan; disclose that distinction.

## Implementation workflow

Follow the development specification and its acceptance criteria. Start with
S00 audit tooling and representative-shard validation; a full audit and the
resulting processing plan precede bulk cleaning. Acquisition remains deferred.
Local processing and indexing do not depend on collecting every published URL.

For each delivery, record the implemented scope, relevant checks, actual run
scope, output locations, and remaining limitations. Preserve source data and
keep changes to project text separate from changes to processing behavior.
Do not infer implementation completion from a translated specification.

## Manual execution from delivery 3 onward

The project owner's instruction on 2026-10-25 changes the delivery workflow:
the assistant writes implementation code, necessary tests, configuration and
run instructions; **the owner executes all processing and tests manually**.
Do not launch pipelines, tests, model jobs or packaging scripts as part of
assistant implementation unless the owner explicitly changes this instruction.
Static source inspection and edits are permitted.

Every delivery must state whether GPU is required. **When GPU is not required,
provide local code and local run instructions only; do not create Colab
notebooks or Colab packaging.** Only deliveries that need GPU should include
Colab-ready files, upload layout, hardware/resource assumptions, manual execution,
checkpoint/resume and result-export instructions.

On 2026-10-26 the owner explicitly requested Colab execution for the full
stage-5 workload after local structural chunking proved too slow. That request
authorizes a full Colab notebook for this workload, including its CPU-bound
structural continuation. Document actual CPU/GPU usage: GPU runtime selection
does not make the structural tokenizer run on CUDA. Manual execution remains
required; semantic CUDA chunks are a separate experiment.

Later on 2026-10-26 the owner explicitly authorized the assistant to run full
stage-5 validation, prepare the completion/sharing documentation, clean up
superseded failed/incomplete versions, and create local Git commits. This is a
scoped execution exception for that closeout; it does not authorize new GPU
jobs, a new full chunk strategy, or automatic processing in later deliveries.
The owner will push the commits. Preserve the standing one-file-per-commit
publication convention and exclude runtime/Drive data from Git.

State which checks were only written or statically reviewed and which were
actually executed by the owner. Do not label unexecuted tests as passing,
generated artifacts as existing, or full-data processing as complete because
code exists. Any supplied GPU notebook must have null execution counts and
empty outputs on delivery.
