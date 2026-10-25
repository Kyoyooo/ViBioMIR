"""Offline, non-destructive record diagnostics for S00; flags are review signals."""
from collections import Counter
import hashlib
import html
from html.parser import HTMLParser
import json
import re

from .common import BLOCK_PAGE, CJK, VI, domain_of, normalize
from .corpus import FLAGS, METRIC_COLUMNS, metrics, valid_id

AUDIT_VERSION = "1.0"
METADATA_FIELDS = ("title", "desc", "source", "method", "ts", "canonical",
                   "authors", "published_date", "updated_date", "category", "abstract")
ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);", re.I)
TAG = re.compile(r"</?[a-z][^>]*>", re.I)
NUMBER_UNIT = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mcg|μg|µg|mg|kg|ml|mmol|mmHg|g|l|%)(?!\w)", re.I)
NEGATION = re.compile(r"\b(?:không|chưa|not|no|without)\b|无|没有|不宜", re.I)
UI = re.compile(r"cookie|privacy policy|đăng nhập|quảng cáo|投诉|登录|版权所有", re.I)
GATE = re.compile(r"not available in (?:english|this language)|content unavailable|nội dung không khả dụng", re.I)
KIND = re.compile(r"^(?:h[1-6]?|heading)$", re.I)
QNA = re.compile(r"(?:câu hỏi|trả lời|bệnh tình|bệnh lý|question|answer|咨询描述|病情分析|指导意见)", re.I)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class HTMLDiagnostics(HTMLParser):
    """Tolerant HTML inspection, not a guarantee of valid or complete markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = Counter()
        self.meta = {}
        self.titles = []
        self.visible_chars = 0
        self.hidden = []
        self.in_title = False
        self.json_ld = None
        self.json_ld_count = 0
        self.json_ld_errors = 0
        self.structured_fields = set()
        self.text_preview = []
        self.preview_chars = 0

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.hidden.append(tag)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"][:1000]
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.json_ld = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self.json_ld is not None:
            try:
                payload = json.loads("".join(self.json_ld))
                pending = [payload]
                while pending:
                    item = pending.pop()
                    if isinstance(item, dict):
                        self.structured_fields.update(k for k in item if k in
                            {"headline", "author", "datePublished", "dateModified", "articleBody", "articleSection"})
                        pending.extend(item.values())
                    elif isinstance(item, list):
                        pending.extend(item)
                self.json_ld_count += 1
            except (ValueError, RecursionError):
                self.json_ld_errors += 1
            self.json_ld = None
        if tag in self.hidden:
            self.hidden.remove(tag)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.json_ld is not None:
            self.json_ld.append(data)
        if self.in_title:
            self.titles.append(data)
        if not self.hidden:
            self.visible_chars += len(data.strip())
            if self.preview_chars < 600:
                part = data[:600 - self.preview_chars]
                self.text_preview.append(part)
                self.preview_chars += len(part)


def inspect_html(value, present):
    if not present:
        return {"state": "missing_column", "chars": None, "visible_chars": 0}
    if value is None:
        return {"state": "null", "chars": 0, "visible_chars": 0}
    if not isinstance(value, str):
        return {"state": "invalid_type", "chars": None, "visible_chars": 0}
    if not value.strip():
        return {"state": "empty", "chars": len(value), "visible_chars": 0}
    parser = HTMLDiagnostics()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError, AssertionError) as exc:
        return {"state": "parse_error", "chars": len(value), "visible_chars": 0,
                "error": str(exc)[:300]}
    return {"state": "parsed" if parser.tags else "unstructured", "chars": len(value),
            "visible_chars": parser.visible_chars, "tags": dict(parser.tags),
            "title": " ".join(parser.titles)[:500], "meta": parser.meta,
            "json_ld_count": parser.json_ld_count, "json_ld_errors": parser.json_ld_errors,
            "structured_fields": sorted(parser.structured_fields),
            "possibly_truncated": not bool(re.search(r"</(?:html|body)>\s*$", value, re.I)),
            "preview": " ".join(parser.text_preview)[:600]}


def field_state(row, key):
    if key not in row:
        return "missing_column"
    value = row[key]
    if value is None:
        return "null"
    if key == "ts":
        return "present" if isinstance(value, (int, float, str)) and not isinstance(value, bool) else "invalid_type"
    if not isinstance(value, (str, list)) or (key != "authors" and not isinstance(value, str)):
        return "invalid_type"
    return "present" if value and (not isinstance(value, str) or value.strip()) else "empty"


def inspect_record(row, shard, source_sha256, row_group, row_offset, short_chars=200):
    """Return metrics plus candidate template signatures; never alter source values."""
    raw_text = row.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    text_state = ("missing_column" if "text" not in row else "null" if raw_text is None
                  else "invalid_type" if not isinstance(raw_text, str)
                  else "empty" if not text.strip() else "present")
    raw_url = row.get("url")
    url = raw_url if isinstance(raw_url, str) else ""
    # Reuse established EDA metrics on a type-safe view, while reporting invalid types explicitly.
    legacy_row = dict(row, url=url)
    if "text" in row:
        legacy_row["text"] = text
    legacy = dict(zip(METRIC_COLUMNS, metrics(legacy_row, "audit", shard, short_chars, False)))
    flags = {name for name, bit in FLAGS.items() if legacy["flags"] & bit}
    if valid_id(row.get("id")) and not -(2**63) <= row["id"] < 2**63:
        flags.add("id_out_of_int64_range")
    if text_state == "invalid_type":
        flags.add("invalid_text_type")
    if raw_url is not None and not isinstance(raw_url, str):
        flags.add("invalid_url_type")
    source_domain = domain_of(url)
    declared = row.get("domain")
    declared_domain = domain_of("https://" + declared) if isinstance(declared, str) else ""
    domain = source_domain or declared_domain or "unknown"
    if declared_domain and source_domain and declared_domain != source_domain:
        flags.add("domain_url_conflict")
    for key in ("final_url", "canonical"):
        value = row.get(key)
        if isinstance(value, str) and value:
            other = domain_of(value)
            if not other:
                flags.add(key + "_invalid")
            elif source_domain and source_domain != other:
                flags.add(key + "_domain_changed")
    letters = sum(c.isalpha() for c in text)
    cjk = len(CJK.findall(text))
    ascii_letters = sum(c.isascii() and c.isalpha() for c in text)
    vi_chars = len(VI.findall(text))
    hint = ("unknown" if not letters else "mixed_script" if cjk / letters >= .2 and (vi_chars or ascii_letters / letters >= .2)
            else "cjk_likely_zh" if cjk / letters >= .2 else "vi_diacritics" if vi_chars
            else "latin_undetermined" if ascii_letters / letters >= .7 else "other_or_mixed")
    lines = [normalize(line) for line in text.splitlines() if line.strip()]
    repeated_lines = sum(n - 1 for line, n in Counter(lines).items() if len(line) >= 15 and n > 1)
    if repeated_lines:
        flags.add("repeated_lines")
    if ENTITY.search(text):
        flags.add("html_entities")
    if text and text != text.strip():
        flags.add("outer_whitespace")
    if re.search(r"(.)\1{11,}", text):
        flags.add("repeated_characters")
    if GATE.search(text):
        flags.add("possible_language_gate")
    if UI.search(text):
        flags.add("possible_ui_text")
    title = row.get("title")
    if isinstance(title, str) and normalize(title) in {normalize(domain), normalize(domain.split('.')[0])}:
        flags.add("possible_website_title")
    title_terms = set(re.findall(r"\w{3,}", normalize(title))) if isinstance(title, str) else set()
    body_terms = set(re.findall(r"\w{3,}", normalize(text))) if text else set()
    title_body_overlap = len(title_terms & body_terms) / len(title_terms) if title_terms else None
    if len(title_terms) >= 3 and body_terms and title_body_overlap == 0:
        flags.add("possible_title_body_mismatch")
    block_state = "missing_column" if "blocks" not in row else "empty"
    blocks = []
    if row.get("blocks") is not None and row.get("blocks") != "":
        try:
            blocks = json.loads(row["blocks"]) if isinstance(row["blocks"], str) else row["blocks"]
            if not isinstance(blocks, list) or any(not isinstance(b, list) or len(b) != 2
                    or not all(isinstance(x, str) for x in b) for b in blocks):
                raise ValueError("Expected [kind, text] string pairs")
            block_state = "valid"
        except (ValueError, TypeError, RecursionError):
            blocks = []
            block_state = "invalid"
            flags.add("invalid_blocks")
    kinds = Counter(kind for kind, _ in blocks)
    joined = "\n".join(value for _, value in blocks)
    block_match = normalize(joined) == normalize(text) if joined.strip() and text.strip() else None
    if block_match is False:
        flags.add("blocks_text_mismatch")
    if blocks and all(KIND.match(kind) for kind, _ in blocks):
        flags.add("headings_without_body")
    html_info = inspect_html(row.get("html_trim"), "html_trim" in row)
    if html_info["state"] in ("invalid_type", "parse_error"):
        flags.add("invalid_html")
    if html_info.get("json_ld_errors"):
        flags.add("malformed_json_ld")
    recovery = bool(joined.strip() or html_info["visible_chars"] > 0)
    if text_state != "present":
        disposition = "needs_reextraction" if recovery else "unusable_currently"
        reasons = ["text_" + text_state, "offline_material_available" if recovery else "no_recoverable_content_detected"]
    elif flags & {"missing_or_invalid_id", "id_out_of_int64_range", "invalid_url", "invalid_url_type", "domain_url_conflict",
                  "non_2xx_status", "possible_block_page", "possible_language_gate", "invalid_blocks",
                  "blocks_text_mismatch", "replacement_character", "headings_without_body", "invalid_html",
                  "possible_website_title", "possible_title_body_mismatch", "malformed_json_ld",
                  "final_url_domain_changed", "canonical_domain_changed"}:
        disposition, reasons = "needs_review", sorted(flags)
    elif flags & {"html_in_text", "html_entities", "repeated_lines", "outer_whitespace", "possible_ui_text"}:
        disposition, reasons = "needs_cleaning", sorted(flags)
    elif flags or hint in ("mixed_script", "other_or_mixed", "unknown"):
        disposition, reasons = "needs_review", sorted(flags) or ["uncertain_language"]
    else:
        disposition, reasons = "usable_as_is", ["no_severe_automatic_signal"]
    signals = {}
    for name, pattern in [("number_unit", NUMBER_UNIT), ("negation", NEGATION), ("ui", UI), ("gate", GATE)]:
        hits = []
        count = 0
        for match in pattern.finditer(text):
            count += 1
            if len(hits) < 8:
                hits.append({"start": match.start(), "end": match.end(), "text": match.group()})
        signals[name] = {"count": count, "examples": hits}
    metadata = {key: {"state": field_state(row, key), "origin": "existing_column" if key in row else "unknown",
                      "preview": str(row[key])[:300] if field_state(row, key) == "present" else None}
                for key in METADATA_FIELDS}
    metadata["html_candidates"] = {"origin": "html_meta_or_jsonld", "meta": html_info.get("meta", {}),
                                    "structured_fields": html_info.get("structured_fields", [])}
    type_hint = ("qa_candidate" if QNA.search(text) else "structured_article_candidate"
                 if any(KIND.match(k) for k in kinds) else "unknown")
    record = {"record_id": digest(f"{source_sha256}\0{shard}\0{row_group}\0{row_offset}"),
        "shard": shard, "source_sha256": source_sha256, "row_group": row_group, "row_offset": row_offset,
        "doc_id": row["id"] if valid_id(row.get("id")) and -(2**63) <= row["id"] < 2**63 else None,
        "doc_id_raw": str(row.get("id")), "domain": domain, "url": url, "status": legacy["status"],
        "text_state": text_state, "n_chars": len(text), "n_ws_tokens": legacy["n_ws_tokens"],
        "n_lines": legacy["n_lines"], "verbatim_sha256": digest(text) if text.strip() else None,
        "normalized_sha256": legacy["text_hash"].hex() if legacy["text_hash"] else None,
        "language_hint": hint, "script_ratios": {"letters": letters, "cjk": cjk, "ascii_latin": ascii_letters,
            "vi_diacritic_chars": vi_chars, "scope": "entire_text", "confidence": "heuristic_only"},
        "doc_type_hint": type_hint, "n_blocks": len(blocks) if block_state == "valid" else None,
        "block_state": block_state, "blocks_text_match": block_match,
        "html_state": html_info["state"], "html_chars": html_info["chars"],
        "html_visible_chars": html_info["visible_chars"], "has_recovery_material": recovery,
        "disposition": disposition, "reasons": reasons, "flags": sorted(flags), "metadata": metadata,
        "structure": {"block_kinds": dict(kinds), "repeated_lines": repeated_lines,
                      "title_body_word_overlap": title_body_overlap,
                      "html": {k: v for k, v in html_info.items() if k != "preview"}},
        "signals": signals, "snippet": text[:600] if text else joined[:600] or html_info.get("preview", ""),
        "snippet_origin": "text" if text else "joined_blocks" if joined else "html_visible_preview",
        "review_status": "unreviewed"}
    # Candidate templates only: short blocks/lines may also contain legitimate medical content.
    templates = {}
    for kind, value in blocks or [("line", line) for line in text.splitlines()]:
        value = normalize(value)
        if 15 <= len(value) <= 160:
            templates.setdefault(digest(value), (domain, digest(value), kind, value))
    return record, list(templates.values())
