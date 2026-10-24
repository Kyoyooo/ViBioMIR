"""Inspect file inventory, Parquet schemas and crawler state read-only."""
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from .common import read_only_db, write_csv, write_json


def inventory(data_dir, output_dir):
    root, out = Path(data_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files, schemas, errors, runs, states = [], Counter(), [], [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        item = {"path": str(path.relative_to(root)), "bytes": path.stat().st_size,
                "kind": path.suffix, "rows": None, "schema": ""}
        if path.suffix == ".parquet":
            try:
                with pq.ParquetFile(path) as pf:
                    item["rows"] = pf.metadata.num_rows
                    item["schema"] = "; ".join(f"{f.name}: {f.type}" for f in pf.schema_arrow)
                    schemas[item["schema"]] += 1
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        files.append(item)
        if path.name.startswith("last_run_") and path.suffix == ".json":
            import json
            try:
                runs.append({"path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))})
            except (ValueError, OSError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
        if path.suffix == ".sqlite3":
            try:
                with read_only_db(path) as db:
                    tables = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
                    state = {"path": str(path), "tables": tables, "by_domain_status": []}
                    if "crawl_state" in tables:
                        state["by_domain_status"] = [dict(zip(("domain", "status", "rows"), row))
                            for row in db.execute("SELECT domain,status,COUNT(*) FROM crawl_state GROUP BY domain,status")]
                    states.append(state)
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
    write_csv(out / "files.csv", ["path", "bytes", "kind", "rows", "schema"],
              ([x[c] for c in ["path", "bytes", "kind", "rows", "schema"]] for x in files))
    result = {"data_dir": str(root.resolve()), "files": len(files),
              "parquet_files": sum(f["kind"] == ".parquet" for f in files),
              "parquet_rows": sum(f["rows"] or 0 for f in files),
              "parquet_bytes": sum(f["bytes"] for f in files if f["kind"] == ".parquet"),
              "schemas": dict(schemas), "crawler_states": states, "last_runs": runs, "errors": errors}
    write_json(out / "inventory.json", result)
    write_csv(out / "crawl_status.csv", ["database", "domain", "status", "rows"],
              ((s["path"], r["domain"], r["status"], r["rows"])
               for s in states for r in s["by_domain_status"]))
    return result
