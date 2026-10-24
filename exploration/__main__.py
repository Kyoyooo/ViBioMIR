"""Usage: python -m exploration --data-dir Data --output-dir reports/eda"""
import argparse
import json
from pathlib import Path

from .corpus import profile_corpus
from .inventory import inventory
from .references import profile_references
from .report import build_report


def main():
    parser = argparse.ArgumentParser(description="Offline, streaming ViBioMIR EDA")
    parser.add_argument("--data-dir", type=Path, default=Path("Data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/eda"))
    parser.add_argument("--stage", choices=["all", "inventory", "corpus", "references", "report"], default="all")
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--max-files", type=int, help="Smoke test only: scan first N sorted corpus shards")
    parser.add_argument("--short-chars", type=int, default=200)
    parser.add_argument("--inspect-blocks", action="store_true", help="Also parse blocks JSON; adds CPU and IO")
    parser.add_argument("--query-file", type=Path)
    parser.add_argument("--links-file", type=Path)
    args = parser.parse_args()
    if args.batch_size < 1 or args.short_chars < 1 or (args.max_files is not None and args.max_files < 1):
        parser.error("batch-size, short-chars and max-files must be positive")
    if not args.data_dir.is_dir():
        parser.error(f"Data directory does not exist: {args.data_dir}")
    root, out = args.data_dir.resolve(), args.output_dir.resolve()
    if root == out or root in out.parents:
        parser.error("Output must be outside the source data directory")
    if args.stage in ["all", "corpus"] and (out / "metrics.sqlite3").exists():
        parser.error("Output already contains metrics.sqlite3; choose a new --output-dir")
    out.mkdir(parents=True, exist_ok=True)
    if args.stage == "all":
        (out / "run_config.json").write_text(json.dumps(vars(args), default=str, indent=2), encoding="utf-8")
    if args.stage in ["all", "inventory"]:
        print("Reading inventory and crawler state…", flush=True)
        inventory(root, out)
    if args.stage in ["all", "corpus"]:
        profile_corpus(root, out, args.batch_size, args.max_files, args.short_chars, args.inspect_blocks)
    if args.stage in ["all", "references"]:
        profile_references(root, out, args.query_file, args.links_file)
    if args.stage in ["all", "report"]:
        build_report(out)
    print(f"Done: {out}", flush=True)


if __name__ == "__main__":
    main()
