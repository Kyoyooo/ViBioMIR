"""Execute the existing stage-4 engine with local SQLite WAL/NORMAL checkpoints.

This changes checkpoint IO, not extraction/rule/selection semantics. Committed
WAL transactions remain atomic. An OS/power failure can lose recent commits;
resume reads the recovered database and reprocesses missing documents.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path

from vibiomir import cleaning
from vibiomir.io import file_hash, write_json

BASE_DATABASE = cleaning.database


def wal_database(path):
    db = BASE_DATABASE(path)
    try:
        mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            raise ValueError("Unable to enable WAL on the local checkpoint")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA wal_autocheckpoint=1000")
        return db
    except BaseException:
        db.close()
        raise


@contextmanager
def wal_checkpoints():
    previous = cleaning.database
    cleaning.database = wal_database
    try:
        yield
    finally:
        cleaning.database = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    with wal_checkpoints():
        result = cleaning.build_cleaned_documents(args.store_dir, args.data_dir, args.output_dir,
            config_path=args.config, mode="apply", resume=args.resume)
    settings = {"engine": "vibiomir.cleaning.build_cleaned_documents", "journal_mode": "WAL",
        "synchronous": "NORMAL", "wal_autocheckpoint_pages": 1000,
        "script_sha256": file_hash(Path(__file__)), "mode": "apply", "state": result["state"],
        "note": "IO profile only; extraction, evidence, rule guards and validation remain the existing engine."}
    write_json(args.output_dir / "EXECUTION_SETTINGS.json", settings)
    print(json.dumps({"state": result["state"], "expected": result["expected"], "stats": result["stats"]}, indent=2))
    return 0 if result["state"] == "complete_provisional" else 1


if __name__ == "__main__":
    raise SystemExit(main())
