"""Read completed stage-3 stores and local HTML without mutating source artifacts."""
from collections import OrderedDict
from contextlib import contextmanager
import fcntl
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from .io import file_hash, text_hash


@contextmanager
def stage3_source(directory):
    root = Path(directory).resolve()
    lock = (root / ".store.lock").open("rb")
    db = None
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Stage-3 store is busy; use an idle completed store") from exc
        manifest_path, validation_path = root / "run_manifest.json", root / "VALIDATION.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("state") != "complete_provisional" or not manifest.get("exports_ready") or not validation_path.is_file():
            raise ValueError("Stage 4 requires a completed and manually validated stage-3 store")
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if validation.get("validation") != "passed":
            raise ValueError("Run the stage-3 validator successfully before starting stage 4")
        database = root / "document_store.sqlite3"
        if any(Path(str(database) + suffix).exists() for suffix in ("-journal", "-wal")):
            raise ValueError("Stage-3 store has a journal/WAL; close/recover it before use")
        # Pin the actual checkpoint consumed here, without copying its ~17 GB contents.
        signature = {"manifest_sha256": file_hash(manifest_path), "validation_sha256": file_hash(validation_path),
                     "database_sha256": file_hash(database), "parent_fingerprint": manifest["fingerprint"]}
        db = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA cache_size=-32768")
        db.execute("PRAGMA temp_store=FILE")
        versions = db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        docs = db.execute("SELECT COUNT(DISTINCT doc_id) FROM versions").fetchone()[0]
        if (versions != manifest["tables"]["document_versions"]["rows"] or
                docs != manifest["tables"]["document_selection"]["rows"] or
                versions != validation.get("tables", {}).get("document_versions")):
            raise ValueError("Stage-3 checkpoint counts disagree with its validated export")
        yield root, db, manifest, signature
    finally:
        if db is not None:
            db.close()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def verify_stage3_signature(root, signature):
    for filename, key in (("document_store.sqlite3", "database_sha256"),
                          ("run_manifest.json", "manifest_sha256"), ("VALIDATION.json", "validation_sha256")):
        if file_hash(root / filename) != signature[key]:
            raise ValueError("Stage-3 inputs changed while reading; do not use this run")


def parent_version(row):
    value = json.loads(row["payload"])
    value["representative_record_id"] = row["representative_record_id"]
    return value


class RawHTMLLoader:
    """Bounded row reads and a bounded cache; validate both shard and HTML hashes."""
    def __init__(self, db, manifest, data_dir):
        self.db, self.root = db, Path(data_dir).resolve()
        self.files = {f["path"]: f for f in manifest["source_files"]}
        self.checked = {}
        self.cache, self.cache_chars = OrderedDict(), 0

    def __call__(self, version):
        key = version["representative_record_id"]
        if key in self.cache:
            value = self.cache.pop(key)
            self.cache[key] = value
            return value
        entry = self.db.execute("SELECT payload FROM inputs WHERE record_id=?", (key,)).fetchone()
        if entry is None:
            raise ValueError("Missing stage-3 representative input")
        source = json.loads(entry[0])
        if source["version_id"] != version["version_id"] or source["doc_id"] != version["doc_id"]:
            raise ValueError("Representative input/version mismatch")
        shard = source["source_shard"]
        item = self.files.get(shard)
        if item is None or source["source_sha256"] != item["sha256"]:
            raise ValueError("HTML source does not belong to the pinned parent manifest")
        rid = text_hash(f"{source['source_sha256']}\0{shard}\0{source['row_group']}\0{source['row_offset']}")
        if rid != key:
            raise ValueError("Invalid HTML source record locator")
        path = (self.root / shard).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("Missing/unsafe raw HTML source path: " + shard)
        if shard not in self.checked:
            if file_hash(path) != source["source_sha256"]:
                raise ValueError("Raw source checksum mismatch: " + shard)
            self.checked[shard] = source["source_sha256"]
        row, offset = None, 0
        with pq.ParquetFile(path) as pf:
            if not {"id", "html_trim"}.issubset(pf.schema_arrow.names):
                raise ValueError("Expected raw HTML columns are absent")
            for batch in pf.iter_batches(batch_size=16, row_groups=[source["row_group"]], columns=["id", "html_trim"]):
                if offset <= source["row_offset"] < offset + batch.num_rows:
                    row = batch.slice(source["row_offset"] - offset, 1).to_pylist()[0]
                    break
                offset += batch.num_rows
        if row is None or row["id"] != version["doc_id"] or not isinstance(row["html_trim"], str):
            raise ValueError("HTML row/identity is inconsistent")
        if text_hash(row["html_trim"]) != version["html_sha256"]:
            raise ValueError("Retained HTML differs from the parent version")
        value = {"html": row["html_trim"], "locator": {"record_id": key, "source_shard": shard,
                 "source_sha256": source["source_sha256"], "row_group": source["row_group"],
                 "row_offset": source["row_offset"], "column": "html_trim", "html_sha256": version["html_sha256"]}}
        length = len(value["html"])
        if length <= 5000000:
            while self.cache and self.cache_chars + length > 5000000:
                _, old = self.cache.popitem(last=False)
                self.cache_chars -= len(old["html"])
            self.cache[key] = value
            self.cache_chars += length
        return value

    def verify_files(self, files=None):
        for shard, expected in (files or self.checked).items():
            path = (self.root / shard).resolve()
            if self.root not in path.parents or file_hash(path) != expected:
                raise ValueError("Raw HTML source changed: " + shard)


class ArticleHTML(HTMLParser):
    """Exact raw HTML text-node spans inside a single explicit article/main root."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    SKIP = {"script", "style", "nav", "aside", "footer", "form", "button", "select", "svg", "iframe", "noscript"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "dt", "dd", "pre"}

    def __init__(self, markup):
        super().__init__(convert_charrefs=False)
        self.markup = markup
        self.line_starts = [0]
        for index, char in enumerate(markup):
            if char == "\n":
                self.line_starts.append(index + 1)
        self.stack, self.roots, self.nodes = [], [], []
        self.serial = 0

    def _source_offset(self):
        # HTMLParser's base class owns an integer `offset` for its current column.
        # Keep our absolute-source-position helper separate from that attribute.
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        self.serial += 1
        entry = {"tag": tag, "serial": self.serial, "start": self._source_offset()}
        attributes = dict(attrs)
        entry["skip"] = tag in self.SKIP or "hidden" in attributes or attributes.get("aria-hidden") == "true"
        if tag in {"article", "main"}:
            self.roots.append(entry)
        if tag not in self.VOID:
            self.stack.append(entry)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def append_node(self, length):
        if not self.stack or any(node["skip"] for node in self.stack):
            return
        roots = [node["serial"] for node in self.stack if node["tag"] in {"article", "main"}]
        if not roots:
            return
        start = self._source_offset()
        kind = next((n["tag"] for n in reversed(self.stack) if n["tag"] in self.BLOCK), "text")
        block = next((n["serial"] for n in reversed(self.stack) if n["tag"] in self.BLOCK), None)
        if self.nodes and self.nodes[-1]["end"] == start and self.nodes[-1]["roots"] == roots and self.nodes[-1]["block"] == block:
            self.nodes[-1]["end"] += length
        else:
            self.nodes.append({"start": start, "end": start + length, "roots": roots, "kind": kind, "block": block})

    def handle_data(self, data):
        self.append_node(len(data))

    def handle_entityref(self, name):
        start = self._source_offset()
        length = len(name) + 1 + int(self.markup[start + len(name) + 1:start + len(name) + 2] == ";")
        self.append_node(length)

    def handle_charref(self, name):
        start = self._source_offset()
        length = len(name) + 2 + int(self.markup[start + len(name) + 2:start + len(name) + 3] == ";")
        self.append_node(length)


def html_units(payload, parent_id, max_chars):
    markup = payload["html"]
    if len(markup) > max_chars:
        return [], "html_size_limit_review"
    parser = ArticleHTML(markup)
    try:
        parser.feed(markup)
        parser.close()
    except (ValueError, RecursionError, AssertionError):
        return [], "html_parse_error_review"
    articles = [r for r in parser.roots if r["tag"] == "article"]
    mains = [r for r in parser.roots if r["tag"] == "main"]
    chosen = articles[0] if len(articles) == 1 else mains[0] if not articles and len(mains) == 1 else None
    if chosen is None:
        return [], "ambiguous_or_missing_article_root_review"
    units = []
    for node in parser.nodes:
        if chosen["serial"] not in node["roots"]:
            continue
        text = markup[node["start"]:node["end"]]
        if not text.strip():
            continue
        units.append({"text": text, "source_kind": "html_text_node", "source_start": node["start"],
            "source_end": node["end"], "kind": node["kind"],
            "locator": {**payload["locator"], "parent_version_id": parent_id,
                        "root_tag": chosen["tag"], "root_start": chosen["start"], "node_start": node["start"]}})
    return units, None if units else "empty_article_root_review"
