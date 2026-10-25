"""Manual preparation commands. Usage: python -m vibiomir --help."""
import argparse
import json
from pathlib import Path
import sqlite3

from .documents import build_documents
from .validate_documents import validate_documents


def main(argv=None):
    parser = argparse.ArgumentParser(description="ViBioMIR manual CPU preparation")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare-documents", help="Stage 3: versioned verbatim import and metadata")
    build.add_argument("--data-dir", type=Path, required=True)
    build.add_argument("--audit-dir", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--config", type=Path)
    build.add_argument("--batch-size", type=int, default=16)
    build.add_argument("--max-files", type=int)
    build.add_argument("--shard-list", type=Path)
    build.add_argument("--stop-after-groups", type=int)
    build.add_argument("--resume", action="store_true")
    build.add_argument("--dry-run", action="store_true")
    validate = commands.add_parser("validate-documents", help="Validate exported contracts; not clinical QA")
    validate.add_argument("--store-dir", type=Path, required=True)
    validate.add_argument("--data-dir", type=Path, help="Also recheck original source-file hashes")
    clean = commands.add_parser("clean-documents", help="Stage 4: local offline recovery and source-mapped cleaning")
    clean.add_argument("--store-dir", type=Path, required=True, help="Completed, validated stage-3 store")
    clean.add_argument("--data-dir", type=Path, required=True)
    clean.add_argument("--output-dir", type=Path, required=True)
    clean.add_argument("--config", type=Path, required=True)
    clean.add_argument("--mode", choices=["dry-run", "apply"], default="dry-run")
    subset = clean.add_mutually_exclusive_group()
    subset.add_argument("--record-ids-file", type=Path)
    subset.add_argument("--max-documents", type=int)
    clean.add_argument("--stop-after-documents", type=int)
    clean.add_argument("--resume", action="store_true")
    clean.add_argument("--preflight-only", action="store_true", help="Check inputs without creating an output store")
    validate_clean = commands.add_parser("validate-cleaned", help="Stage-4 reproducibility, source slices and accounting")
    validate_clean.add_argument("--output-dir", type=Path, required=True)
    validate_clean.add_argument("--store-dir", type=Path, required=True)
    validate_clean.add_argument("--data-dir", type=Path, required=True)
    tokenizer = commands.add_parser("download-chunk-tokenizer", help="Download only the pinned BGE-M3 tokenizer (CPU, network)")
    tokenizer.add_argument("--output-dir", type=Path, required=True)
    inputs = commands.add_parser("prepare-chunk-inputs", help="Stage 5: verify selected sources and prepare portable document batches (CPU)")
    inputs.add_argument("--cleaned-dir", type=Path, required=True)
    inputs.add_argument("--store-dir", type=Path, required=True)
    inputs.add_argument("--data-dir", type=Path, help="Only needed if stage 4 recovered HTML")
    inputs.add_argument("--output-dir", type=Path, required=True)
    inputs.add_argument("--batch-documents", type=int, default=500)
    inputs.add_argument("--max-documents", type=int)
    inputs.add_argument("--stop-after-batches", type=int)
    inputs.add_argument("--resume", action="store_true")
    chunks = commands.add_parser("build-chunks", help="Stage 5: fixed/structure on CPU, semantic on CUDA")
    chunks.add_argument("--input-dir", type=Path, required=True)
    chunks.add_argument("--output-dir", type=Path, required=True)
    chunks.add_argument("--tokenizer-dir", type=Path, required=True)
    chunks.add_argument("--strategy", choices=["fixed", "structure", "semantic"], default="structure")
    chunks.add_argument("--config", type=Path, default=Path("configs/chunking_stage5.json"))
    chunks.add_argument("--encoder-batch-size", type=int, default=16)
    chunks.add_argument("--stop-after-batches", type=int)
    chunks.add_argument("--resume", action="store_true")
    check_chunks = commands.add_parser("validate-chunks", help="Stage 5: source offsets, accounting and actual tokenizer budgets (CPU)")
    check_chunks.add_argument("--input-dir", type=Path, required=True)
    check_chunks.add_argument("--output-dir", type=Path, required=True)
    check_chunks.add_argument("--tokenizer-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-documents":
            paths = json.loads(args.shard_list.read_text(encoding="utf-8")) if args.shard_list else None
            result = build_documents(args.data_dir, args.audit_dir, args.output_dir, config_path=args.config,
                batch_size=args.batch_size, max_files=args.max_files, shard_paths=paths,
                resume=args.resume, stop_after_groups=args.stop_after_groups, dry_run=args.dry_run)
            if args.dry_run:
                print(json.dumps({k: result[k] for k in ("scope", "expected_input_records", "gpu_required")}, indent=2))
            else:
                print(json.dumps({"state": result["state"], "counts": {k:v for k,v in result["stats"].items() if k not in ("files","row_groups")},
                                  "gpu_required": False, "ready_for_retrieval": False}, indent=2))
                if result["state"] in {"failed", "partial", "interrupted"}:
                    return 1
        elif args.command == "validate-documents":
            print(json.dumps(validate_documents(args.store_dir, data_dir=args.data_dir), indent=2))
        elif args.command == "clean-documents":
            from .cleaning import build_cleaned_documents
            record_ids = json.loads(args.record_ids_file.read_text(encoding="utf-8")) if args.record_ids_file else None
            result = build_cleaned_documents(args.store_dir, args.data_dir, args.output_dir, config_path=args.config,
                mode=args.mode, record_ids=record_ids, max_documents=args.max_documents,
                stop_after_documents=args.stop_after_documents, resume=args.resume, dry_run=args.preflight_only)
            print(json.dumps({key: result[key] for key in ("state", "mode", "scope", "expected", "gpu_required", "ready_for_retrieval")}, indent=2))
            if result["state"] in {"failed", "partial", "interrupted"}:
                return 1
        elif args.command == "validate-cleaned":
            from .validate_cleaning import validate_cleaned_documents
            print(json.dumps(validate_cleaned_documents(args.output_dir, args.store_dir, args.data_dir), indent=2))
        elif args.command == "download-chunk-tokenizer":
            from .chunk_models import download_tokenizer
            print(json.dumps(download_tokenizer(args.output_dir), indent=2))
        elif args.command == "prepare-chunk-inputs":
            from .chunk_pipeline import prepare_inputs
            result = prepare_inputs(args.cleaned_dir, args.store_dir, args.output_dir,
                batch_documents=args.batch_documents, max_documents=args.max_documents,
                stop_after_batches=args.stop_after_batches, resume=args.resume, data_dir=args.data_dir)
            print(json.dumps(result, indent=2))
        else:
            from .chunk_models import load_tokenizer, SentenceEncoder
            from .chunk_pipeline import build_chunks, validate_chunks
            # Check cheap paths before loading a tokenizer or downloading weights.
            if not (args.input_dir / "manifest.json").is_file():
                raise ValueError("Missing chunk inputs: run prepare-chunk-inputs first")
            input_manifest = json.loads((args.input_dir / "manifest.json").read_text(encoding="utf-8"))
            if input_manifest.get("state") != "complete":
                raise ValueError("Chunk inputs are not complete: resume prepare-chunk-inputs first")
            if args.command == "validate-chunks" and not (args.output_dir / "manifest.json").is_file():
                raise ValueError("Missing chunk output: run build-chunks successfully first")
            tokenizer, signature = load_tokenizer(args.tokenizer_dir)
            if args.command == "validate-chunks":
                result = validate_chunks(args.input_dir, args.output_dir, tokenizer, signature)
            else:
                from .chunking import settings
                config = settings(json.loads(args.config.read_text(encoding="utf-8")))
                if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
                    raise ValueError("Output exists. Use --resume with identical settings or choose a new directory.")
                encoder = SentenceEncoder(tokenizer, args.encoder_batch_size) if args.strategy == "semantic" else None
                result = build_chunks(args.input_dir, args.output_dir, tokenizer, signature,
                    strategy=args.strategy, config=config, resume=args.resume,
                    stop_after_batches=args.stop_after_batches, encoder=encoder,
                    encoder_registry=encoder.registry if encoder else None)
            print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted. Committed batches are preserved; repeat the same stage-5 build with --resume.\n")
    except (ValueError, OSError, json.JSONDecodeError, sqlite3.Error, ImportError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
