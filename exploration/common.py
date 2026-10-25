"""Shared IO and lightweight text diagnostics; no model/API calls."""
import csv
from contextlib import contextmanager
import html
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow.parquet as pq

SPACE = re.compile(r"\s+")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")
VI = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]")
HTML_TAG = re.compile(r"</?(?:html|body|div|script|style|p|span|a)\b[^>]*>", re.I)
BLOCK_PAGE = re.compile(
    r"captcha|access denied|verify you are human|cloudflare ray id|"
    r"checking your browser|访问验证|安全验证|xác minh bạn là con người", re.I
)


def normalize(text):
    """NFKC → HTML unescape → lowercase → whitespace, as provided in the task."""
    return SPACE.sub(" ", html.unescape(unicodedata.normalize("NFKC", text)).lower()).strip()


def language_hint(text):
    """Script heuristic on a prefix; Latin does NOT mean English."""
    sample = text[:4000]
    letters = sum(c.isalpha() for c in sample)
    if not letters:
        return "unknown"
    if len(CJK.findall(sample)) / letters >= 0.2:
        return "cjk_likely_zh"
    if VI.search(sample):
        return "vi_diacritics"
    if sum(c.isascii() and c.isalpha() for c in sample) / letters >= 0.7:
        return "latin_undetermined"
    return "other_or_mixed"


def domain_of(url):
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


@contextmanager
def read_only_db(path):
    # immutable avoids creating journal/WAL/SHM sidecars. Source must be idle.
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        yield db
    finally:
        db.close()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path, columns, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)


def parquet_batches(path, columns=None, batch_size=512, row_groups=None):
    with pq.ParquetFile(path) as pf:
        selected = None if columns is None else [c for c in columns if c in pf.schema_arrow.names]
        yield from pf.iter_batches(batch_size=batch_size, columns=selected, row_groups=row_groups)
