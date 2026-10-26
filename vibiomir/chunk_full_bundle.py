"""Portable full-stage-5 bundle with an optional stopped local checkpoint."""
from contextlib import ExitStack
import fcntl
from pathlib import Path
import zipfile

from .chunk_batches import batches, read_json
from .chunk_models import load_tokenizer
from .chunk_pipeline import code_signature
from .chunking import settings


def make_full_bundle(input_dir, tokenizer_dir, output, *, checkpoint_dir=None,
                     config_path=None, project_root=None):
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
    input_dir, tokenizer_dir, output = Path(input_dir), Path(tokenizer_dir), Path(output)
    config_path = Path(config_path) if config_path else root / "configs/chunking_stage5.json"
    manifest = read_json(input_dir / "manifest.json")
    if manifest.get("state") != "complete" or manifest["identity"]["operation"] != "prepare-chunk-inputs":
        raise ValueError("Use the completed chunk_inputs_stage5_full directory; do not rerun preparation")
    if output.exists():
        raise ValueError("Bundle exists; choose a new --output filename")
    _, tokenizer_signature = load_tokenizer(tokenizer_dir)
    config = settings(read_json(config_path))
    with ExitStack() as stack:
        entries = []
        for folder in ("vibiomir", "exploration"):
            entries.extend((p, p.relative_to(root).as_posix()) for p in sorted((root / folder).glob("*.py")))
        for p in sorted((root / "tests").glob("test_stage5*.py")):
            entries.append((p, p.relative_to(root).as_posix()))
        for p in sorted((root / "colab").glob("stage5*.ipynb")):
            entries.append((p, p.relative_to(root).as_posix()))
        guide = root / "docs/CHUNKING_STAGE5_COLAB_FULL.md"
        if guide.is_file():
            entries.append((guide, "docs/CHUNKING_STAGE5_COLAB_FULL.md"))
        guide = root / "docs/SEMANTIC_STAGE5_TWO_COLABS.md"
        if guide.is_file():
            entries.append((guide, "docs/SEMANTIC_STAGE5_TWO_COLABS.md"))
        for name in ("requirements-stage3.txt", "requirements-stage4.txt", "requirements-stage5.txt", "requirements-stage5-colab.txt"):
            entries.append((root / name, name))
        if (root / "requirements-stage5-semantic.txt").is_file():
            entries.append((root / "requirements-stage5-semantic.txt", "requirements-stage5-semantic.txt"))
        entries.append((config_path, "configs/chunking_stage5.json"))
        entries.append((input_dir / "manifest.json", "inputs/manifest.json"))
        inputs = list(batches(input_dir, manifest["signature"]))
        for path, _ in inputs:
            for name in ("batch.json", "documents.jsonl.gz"):
                entries.append((path / name, f"inputs/{path.name}/{name}"))
        for path in sorted(tokenizer_dir.iterdir()):
            if path.is_file():
                entries.append((path, f"tokenizer/{path.name}"))
        checkpoints = []
        if checkpoint_dir:
            checkpoint_dir = Path(checkpoint_dir)
            lock = stack.enter_context((checkpoint_dir / ".store.lock").open("rb"))
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Local build is still running. Stop it with Ctrl+C, wait for exit, then package its committed batches.") from exc
            old = read_json(checkpoint_dir / "manifest.json")
            identity = old["identity"]
            if (identity["operation"] != "build-chunks" or identity["parent_signature"] != manifest["signature"] or
                    identity["tokenizer"] != tokenizer_signature or identity["config"] != config or
                    identity["code"] != code_signature()):
                raise ValueError("Checkpoint input, tokenizer, configuration or original chunk code differs; do not edit its manifest")
            checkpoints = list(batches(checkpoint_dir, old["signature"]))
            if len(checkpoints) > len(inputs):
                raise ValueError("Checkpoint has more batches than prepared inputs")
            for (_, completed), (_, source) in zip(checkpoints, inputs):
                if any(completed[key] != source[key] for key in ("index", "documents", "first_doc_id", "last_doc_id")):
                    raise ValueError("Checkpoint batch does not correspond to prepared input")
            entries.append((checkpoint_dir / "manifest.json", "checkpoint/manifest.json"))
            for path, _ in checkpoints:
                for name in ("batch.json", "documents.jsonl.gz"):
                    entries.append((path / name, f"checkpoint/{path.name}/{name}"))
            if (checkpoint_dir / "VALIDATION.json").is_file():
                entries.append((checkpoint_dir / "VALIDATION.json", "checkpoint/VALIDATION.json"))
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".partial")
        # Inputs/checkpoints are already gzip-compressed; avoid compressing them
        # again. ZIP64 handles full-corpus bundles above 4 GiB.
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path, name in entries:
                archive.write(path, name)
        temporary.replace(output)
    return {"output": str(output), "documents": manifest["documents"], "input_batches": len(inputs),
            "reused_checkpoint_batches": len(checkpoints), "files": len(entries),
            "weights_included": False, "source_databases_included": False}
