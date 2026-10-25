"""Conservative offline metadata extraction; descriptions and dates retain roles."""
from datetime import date, datetime
import html
from html.parser import HTMLParser
import json
import re
import unicodedata

SPACE = re.compile(r"\s+")
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "ScholarlyArticle", "MedicalScholarlyArticle", "Report"}
FIELDS = ("title", "description", "abstract", "authors", "published_date", "updated_date", "category")
PUBLISHED_META = {"article:published_time", "citation_publication_date", "pubdate", "datepublished", "dc.date.issued", "dcterms.issued"}
UPDATED_META = {"article:modified_time", "datemodified", "lastmod", "dcterms.modified"}


def display(value):
    """Normalize a metadata view, never the original evidence text."""
    return SPACE.sub(" ", html.unescape(unicodedata.normalize("NFKC", value))).strip()


def normalize_date(raw, locale="unknown"):
    """Return ISO value/precision, retaining ambiguous dates as unresolved."""
    if not isinstance(raw, str):
        return None, None, "unsupported_date_type"
    value = display(raw)
    if re.fullmatch(r"\d{4}", value):
        return (value, "year", None) if 1 <= int(value) <= 9999 else (None, None, "invalid_date")
    if re.fullmatch(r"\d{4}-\d{2}", value):
        try:
            date.fromisoformat(value + "-01")
            return value, "month", None
        except ValueError:
            return None, None, "invalid_date"
    chinese = re.fullmatch(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日?", value)
    if chinese:
        try:
            return date(*map(int, chinese.groups())).isoformat(), "day", None
        except ValueError:
            return None, None, "invalid_date"
    numeric = re.fullmatch(r"(\d{1,4})/(\d{1,2})/(\d{1,4})", value)
    if numeric:
        a, b, c = map(int, numeric.groups())
        if len(numeric[1]) == 4:
            parts = (a, b, c)
        elif locale == "vi":
            parts = (c, b, a)
        else:
            return None, None, "ambiguous_date_order"
        try:
            return date(*parts).isoformat(), "day", None
        except ValueError:
            return None, None, "invalid_date"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat(), "day", None
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(), "time", None
    except ValueError:
        return None, None, "invalid_date"
    return None, None, "unrecognized_date_format"


class MetadataHTML(HTMLParser):
    """Read only explicit local metadata. No resource loading or DOM rewriting."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = []
        self.title_parts = []
        self.in_title = False
        self.script = None
        self.json_documents = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and attrs.get("content"):
                self.meta.append((key.lower(), attrs["content"]))
        if tag == "title":
            self.in_title = True
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.script = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.script is not None:
            try:
                self.json_documents.append(json.loads("".join(self.script)))
            except (ValueError, RecursionError):
                self.errors.append("malformed_json_ld")
            self.script = None


def _author_names(value, graph):
    if isinstance(value, str):
        # Author URLs are references, not names.
        return [] if value.startswith(("http://", "https://", "#")) else [display(value)]
    if isinstance(value, list):
        return [name for item in value for name in _author_names(item, graph)]
    if isinstance(value, dict):
        if isinstance(value.get("name"), str):
            return [display(value["name"])]
        reference = value.get("@id")
        target = graph.get(reference) if isinstance(reference, str) else None
        if isinstance(target, dict) and isinstance(target.get("name"), str):
            return [display(target["name"])]
    return []


def extract_metadata(row, domain, options=None):
    """Selected explicit values plus every candidate and conflicting alternative."""
    options = options or {}
    candidates = {field: [] for field in FIELDS}
    errors = []
    locale = options.get("date_locales", {}).get(domain, "unknown")

    def add(field, raw, origin, locator, priority, selectable=True, confidence="explicit_source"):
        if raw is None or raw == "" or raw == []:
            return
        precision, issue = None, None
        if field == "authors":
            normalized = _author_names(raw, {})
            normalized = list(dict.fromkeys(name for name in normalized if name))
            if not normalized:
                issue = "unresolved_author_value"
                normalized = None
        elif field.endswith("_date"):
            normalized, precision, issue = normalize_date(raw, locale)
        elif isinstance(raw, str):
            normalized = display(raw) or None
        else:
            normalized, issue = None, "invalid_metadata_type"
        candidates[field].append({"raw_value": raw, "normalized_value": normalized, "origin": origin,
            "locator": locator, "priority": priority, "selectable": bool(selectable and normalized is not None),
            "confidence": confidence, "date_precision": precision, "issue": issue})

    for target, column in [("title", "title"), ("description", "desc"), ("abstract", "abstract"),
                           ("authors", "authors"), ("published_date", "published_date"),
                           ("updated_date", "updated_date"), ("category", "category")]:
        add(target, row.get(column), "existing_column", {"column": column}, 0)

    parser = MetadataHTML()
    markup = row.get("html_trim")
    if isinstance(markup, str) and markup.strip():
        try:
            parser.feed(markup)
            parser.close()
        except (ValueError, RecursionError, AssertionError) as exc:
            errors.append("html_metadata_parse_error:" + type(exc).__name__)
        errors.extend(parser.errors)
        author_meta = []
        for index, (key, value) in enumerate(parser.meta):
            locator = {"column": "html_trim", "meta_key": key, "occurrence": index}
            target = ("title" if key in {"og:title", "twitter:title"} else
                      "description" if key in {"description", "og:description", "twitter:description"} else
                      "abstract" if key == "citation_abstract" else
                      "published_date" if key in PUBLISHED_META else
                      "updated_date" if key in UPDATED_META else
                      "category" if key in {"article:section", "citation_section"} else None)
            if key in {"author", "citation_author", "dc.creator"}:
                author_meta.append((value, locator))
            elif target:
                add(target, value, "html_meta", locator, 10)
        if author_meta:
            add("authors", [a[0] for a in author_meta], "html_meta",
                {"column": "html_trim", "meta_occurrences": [a[1] for a in author_meta]}, 10)
        if parser.title_parts:
            add("title", "".join(parser.title_parts), "html_title", {"column": "html_trim", "selector": "title"}, 30)
        nodes = []
        pending = [(document, f"$[{i}]") for i, document in enumerate(parser.json_documents)]
        while pending:
            item, path = pending.pop()
            if isinstance(item, dict):
                nodes.append((item, path))
                pending.extend((v, path + "." + k) for k, v in item.items() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                pending.extend((v, path + f"[{i}]") for i, v in enumerate(item))
        graph = {n["@id"]: n for n, _ in nodes if isinstance(n.get("@id"), str)}
        for node, path in nodes:
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list) or not ARTICLE_TYPES.intersection(t for t in types if isinstance(t, str)):
                continue
            for field, key in [("title", "headline"), ("description", "description"), ("abstract", "abstract"),
                               ("published_date", "datePublished"), ("updated_date", "dateModified"),
                               ("category", "articleSection")]:
                add(field, node.get(key), "jsonld_article", {"column": "html_trim", "json_path": path + "." + key}, 20)
            names = _author_names(node.get("author"), graph)
            if names:
                add("authors", names, "jsonld_article", {"column": "html_trim", "json_path": path + ".author",
                    "raw_author": node.get("author")}, 20)

    # Body rules are candidates by default. Generic first/latest dates and clinician mentions are never used.
    text = row.get("text") if isinstance(row.get("text"), str) else ""
    if options.get("body_candidates", True):
        cursor = 0
        nonempty = 0
        for line in text.splitlines(keepends=True):
            value = line.strip()
            if value:
                nonempty += 1
                start = cursor + len(line) - len(line.lstrip())
                byline = re.match(r"Bài viết bởi\s+(.+)", value, re.I)
                if byline and nonempty <= 5:
                    add("authors", byline[1], "body_byline_candidate",
                        {"column": "text", "start": start + byline.start(1), "end": start + byline.end(1)},
                        100, options.get("select_body_candidates", False), "candidate_only")
                family_date = re.match(r"(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)\s+家庭医生在线", value)
                if domain == "familydoctor.com.cn" and nonempty <= 4 and family_date:
                    add("published_date", family_date[1], "body_header_date_candidate",
                        {"column": "text", "start": start, "end": start + len(family_date[1])},
                        100, options.get("select_body_candidates", False), "candidate_only")
            cursor += len(line)
            if nonempty > 5:
                break

    fields = {}
    for field, choices in candidates.items():
        eligible = sorted((c for c in choices if c["selectable"]), key=lambda c: (c["priority"], str(c["locator"])))
        selected = eligible[0] if eligible else None
        distinct = {json.dumps(c["normalized_value"], ensure_ascii=False, sort_keys=True) for c in choices if c["normalized_value"] is not None}
        fields[field] = {"value": selected["normalized_value"] if selected else None,
                         "selected": selected, "candidates": choices, "conflict": len(distinct) > 1}
    return {"fields": fields, "issues": sorted(set(errors)), "policy": "explicit_sources_first",
            "body_candidates_selectable": bool(options.get("select_body_candidates", False)),
            "description_is_not_abstract": True, "crawl_timestamp_is_not_publication_date": True}
