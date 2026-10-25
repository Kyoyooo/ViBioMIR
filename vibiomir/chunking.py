"""Source-preserving chunkers. All offsets are Python Unicode code points.

No tokenizer.decode() is ever used to manufacture evidence. Context may be
shortened, but chunk_text is always an unchanged contiguous region slice.
"""
import json
import math
import re
import unicodedata
from bisect import bisect_right
from html import unescape

from .io import canonical_json, text_hash

DEFAULTS = dict(target_tokens=384, max_tokens=512, overlap_tokens=64,
                min_tokens=128, context_tokens=48, semantic_threshold=0.65)
QUESTION = re.compile(r"^(?:question|câu hỏi|hỏi|问题|健康咨询描述)\s*[:：]", re.I)
# Conservative fallback: textual numbered headings only, never dosage/list numbers.
NUMBERED_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)(?:Tổng quan|Nguyên nhân|Triệu chứng|Chẩn đoán|Điều trị|Phòng ngừa|Overview|Causes|Symptoms|Diagnosis|Treatment|Prevention|概述|病因|症状|诊断|治疗|预防)\s*[:：]?\s*$", re.I)
SENTENCE = re.compile(r"[。！？!?]+[\s]*|\.(?=\s+[A-ZÀ-Ỹ]|\s*$)\s*|\n+")


def settings(value=None):
    config = dict(DEFAULTS)
    if value:
        unknown = set(value) - set(config)
        if unknown:
            raise ValueError(f"Unknown chunk settings: {sorted(unknown)}")
        config.update(value)
    for key in set(config) - {"semantic_threshold"}:
        if type(config[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if not (0 <= config["overlap_tokens"] < config["target_tokens"] <= config["max_tokens"]):
        raise ValueError("Require 0 <= overlap_tokens < target_tokens <= max_tokens")
    if not (1 <= config["min_tokens"] <= config["target_tokens"] and
            0 <= config["context_tokens"] < config["max_tokens"] - 4):
        raise ValueError("Invalid minimum/context token budget")
    threshold = config["semantic_threshold"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not -1 <= threshold <= 1:
        raise ValueError("semantic_threshold must be between -1 and 1")
    return config


def count(tokenizer, text):
    return len(tokenizer.encode(text, add_special_tokens=True, truncation=False))


def offsets(tokenizer, text):
    values = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                       truncation=False)["offset_mapping"]
    return [(int(a), int(b)) for a, b in values if b > a]


def context_prefix(tokenizer, context, budget):
    if not context or budget == 0:
        return ""
    spans = offsets(tokenizer, context)
    if len(spans) > budget:
        context = context[:spans[budget - 1][1]]
    # Count again: normalization and special tokens can alter the budget.
    while context and count(tokenizer, context + "\n\n") > budget:
        context = context[:-1]
    return context + "\n\n" if context else ""


def fitting_end(tokenizer, text, start, end, prefix, cap):
    """Shrink a proposed span using tokenizer offsets; guarantee forward progress."""
    while count(tokenizer, prefix + text[start:end]) > cap:
        spans = offsets(tokenizer, text[start:end])
        excess = count(tokenizer, prefix + text[start:end]) - cap
        keep = max(0, len(spans) - max(1, excess))
        next_end = start + spans[keep - 1][1] if keep else start
        if next_end >= end:
            next_end = end - 1
        end = next_end
        if end <= start:
            raise ValueError("Token budget cannot hold a source character; reduce context or increase max_tokens")
    return end


def windows(tokenizer, text, start, end, prefix, config, overlap=True):
    all_spans = [(start + a, start + b) for a, b in offsets(tokenizer, text[start:end])]
    starts = [a for a, _ in all_spans]
    cursor = start
    while cursor < end:
        first = max(0, bisect_right(starts, cursor) - 1) if starts else 0
        capacity = max(1, config["target_tokens"] - count(tokenizer, prefix))
        proposed = end if len(all_spans) - first <= capacity else all_spans[first + capacity][0]
        if proposed <= cursor:
            proposed = cursor + 1
        stop = fitting_end(tokenizer, text, cursor, proposed, prefix, config["max_tokens"])
        yield cursor, stop
        if stop == end:
            break
        next_cursor = stop
        if overlap and config["overlap_tokens"]:
            used = offsets(tokenizer, text[cursor:stop])
            # Normalized token offsets can repeat for emoji/combining marks.
            if used:
                candidate = cursor + used[max(0, len(used) - config["overlap_tokens"])][0]
                if cursor < candidate < stop:
                    next_cursor = candidate
        cursor = next_cursor


def sentence_spans(text, start, end):
    cursor = start
    for match in SENTENCE.finditer(text, start, end):
        if match.end() > cursor:
            yield cursor, match.end()
            cursor = match.end()
    if cursor < end:
        yield cursor, end


def structure_units(region, mapped=()):
    """Partition a kept region; mapped exact blocks take precedence over lines."""
    text = region["text"]
    points = {0, len(text)}
    blocks = []
    for block in mapped:
        if block["start"] is None:
            continue
        a = max(0, block["start"] - region["source_start"])
        b = min(len(text), block["end"] - region["source_start"])
        if a < b:
            blocks.append((a, b, block))
            points.update((a, b))
    for match in re.finditer(r"\n+", text):
        points.add(match.end())
    units, heading = [], []
    points = sorted(points)
    block_index = 0
    for a, b in zip(points, points[1:]):
        while block_index < len(blocks) and blocks[block_index][1] <= a:
            block_index += 1
        block = blocks[block_index][2] if block_index < len(blocks) and blocks[block_index][0] <= a and blocks[block_index][1] >= b else None
        kind = block["kind"] if block else region.get("kind", "p")
        path = json.loads(block["heading_path_json"]) if block else heading
        is_heading = kind in {"h", "h1", "h2", "h3", "h4", "h5", "h6"} or bool(NUMBERED_HEADING.fullmatch(text[a:b].strip()))
        if is_heading:
            heading = path if block else [text[a:b].strip()]
        elif block:
            heading = path
        units.append({"start": a, "end": b, "kind": kind,
                      "heading": list(heading), "is_heading": is_heading})
    return units


def sections(region):
    units = region["units"]
    start, heading = 0, []
    for unit in units:
        if unit["is_heading"] and unit["start"] > start:
            yield start, unit["start"], heading
            start = unit["start"]
        if unit["is_heading"]:
            heading = unit["heading"]
    if start < len(region["text"]):
        # A region can begin mid-section after cleaning removed a UI line.
        yield start, len(region["text"]), heading or (units[0]["heading"] if units else [])


def cosine(a, b):
    denominator = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    if denominator == 0:
        raise ValueError("Semantic encoder returned a zero vector")
    return sum(x*y for x, y in zip(a, b)) / denominator


def structured_spans(tokenizer, text, start, end, prefix, config, units, encoder=None):
    if count(tokenizer, prefix + text[start:end]) <= config["max_tokens"]:
        # Keep short Q&A and compact sections/tables together.
        yield start, end
        return
    atoms = []
    for unit in units:
        a, b = max(start, unit["start"]), min(end, unit["end"])
        if a >= b:
            continue
        candidates = [(a, b)] if unit["kind"] == "tr" else sentence_spans(text, a, b)
        for x, y in candidates:
            atoms.extend(windows(tokenizer, text, x, y, prefix, config, overlap=False))
    vectors = encoder([text[a:b] for a, b in atoms]) if encoder and atoms else None
    if vectors is not None and len(vectors) != len(atoms):
        raise ValueError("Semantic encoder returned the wrong number of vectors")
    first = 0
    for index in range(1, len(atoms) + 1):
        a, b = atoms[first][0], atoms[index - 1][1]
        last = index == len(atoms)
        used = count(tokenizer, prefix + text[a:b])
        boundary = last
        if not last:
            would_overflow = count(tokenizer, prefix + text[a:atoms[index][1]]) > config["max_tokens"]
            shifted = vectors is not None and used >= config["min_tokens"] and cosine(vectors[index - 1], vectors[index]) < config["semantic_threshold"]
            boundary = would_overflow or used >= config["target_tokens"] or shifted
        if boundary:
            yield a, b
            # Overlap only a complete preceding atom/sentence that fits; never
            # retain the entire old chunk or prevent forward progress.
            first = index
            if not last and config["overlap_tokens"] and index >= 2:
                old = atoms[index - 1]
                if old[0] > a and count(tokenizer, text[old[0]:old[1]]) <= config["overlap_tokens"] and count(tokenizer, prefix + text[old[0]:atoms[index][1]]) <= config["max_tokens"]:
                    first = index - 1


def chunk_document(packet, tokenizer, config, strategy, identity, encoder=None):
    if strategy not in {"fixed", "structure", "semantic"}:
        raise ValueError(f"Unknown strategy: {strategy}")
    if strategy == "semantic" and encoder is None:
        raise ValueError("Semantic chunking requires the GPU encoder; see the stage-5 Colab guide")
    chunks = []
    question = ""
    for region in packet["regions"]:
        text = region["text"]
        for a, b, heading in sections(region):
            section = text[a:b]
            if heading:
                question = ""
            explicit_question = bool(QUESTION.match(section.lstrip()))
            if explicit_question:
                # Only the first explicit question line, not an invented Q&A.
                question = section.strip().splitlines()[0]
            rows = [u for u in region["units"] if a <= u["start"] < b and u["kind"] == "tr"]
            # Legacy tr does not identify column headers. Preserve a contextual
            # first row without asserting that it is a header or inventing cells.
            table_context = text[rows[0]["start"]:rows[0]["end"]] if rows else ""
            context = "\n".join(dict.fromkeys(x for x in [packet.get("title"), *heading, question, table_context] if x))
            prefix = context_prefix(tokenizer, context, config["context_tokens"])
            if explicit_question and count(tokenizer, prefix + section) <= config["max_tokens"]:
                spans = [(a, b)]
            elif strategy == "fixed":
                spans = windows(tokenizer, text, a, b, prefix, config)
            else:
                spans = structured_spans(tokenizer, text, a, b, prefix, config,
                                         region["units"], encoder if strategy == "semantic" else None)
            for start, end in spans:
                evidence = text[start:end]
                if not evidence.strip():
                    continue
                retrieval = prefix + evidence
                if count(tokenizer, retrieval) > config["max_tokens"]:
                    raise ValueError("Internal token budget violation")
                key = [identity, packet["doc_id"], packet["derived_version_id"], region["region_id"], start, end]
                chunks.append({"chunk_id": text_hash(canonical_json(key)), "doc_id": packet["doc_id"],
                    "derived_version_id": packet["derived_version_id"], "parent_version_id": packet["parent_version_id"],
                    "region_id": region["region_id"], "region_start": start, "region_end": end,
                    "source_start": region["source_start"] + start, "source_end": region["source_start"] + end,
                    "source_kind": region["source_kind"], "source_locator_json": region["source_locator_json"],
                    "chunk_text": evidence, "text_sha256": text_hash(evidence), "retrieval_text": retrieval,
                    "context_prefix": prefix, "token_count": count(tokenizer, retrieval),
                    "strategy": strategy, "language_hint": packet.get("language_hint"),
                    "evidence_group_sha256": text_hash(re.sub(r"\s+", " ", unescape(unicodedata.normalize("NFKC", evidence)).lower()).strip()),
                    "is_final": False, "official_match": "unverified"})
    return {"doc_id": packet["doc_id"], "derived_version_id": packet["derived_version_id"],
            "availability": packet["availability"], "chunks": chunks}
