"""Conservative, reviewable source-region rules; never rewrite clinical prose."""
from collections import Counter
import json
from pathlib import Path
import re

from exploration.common import normalize
from .io import canonical_json, text_hash

REVISION = "stage4-source-regions-v1"
DEFAULTS = {"max_html_chars": 5000000, "max_removed_fraction": 0.50,
            "min_body_chars": 80, "review_sample_limit": 100, "export_batch_rows": 128,
            "export_rows_per_file": 50000, "rules": []}
NUMBER = re.compile(r"\d")
NEGATION = re.compile(r"\b(?:không|chưa|đừng|chớ|not|no|never|without|avoid|contraindicated)\b|不|无|未|禁|勿", re.I)
UNIT = re.compile(r"\b(?:mg|mcg|ml|mmhg|mmol|kg|μg|µg)\b", re.I)
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ERROR_PAGE = re.compile(r"(?:404 not found|403 forbidden|502 bad gateway|503 service unavailable)"
    r"(?: (?:nginx|apache|openresty)[\w/. -]*)?")
GATE_PAGE = re.compile(r"(?:access denied|verify you are human|please verify you are human|"
    r"checking your browser|choose your language|select your language|vui lòng chọn ngôn ngữ|访问验证|安全验证|"
    r"(?:this )?(?:content|article) (?:is )?(?:unavailable|not available in (?:english|this language))|"
    r"nội dung không khả dụng)[.!]?")


def load_cleaning_config(path=None):
    config = json.loads(json.dumps(DEFAULTS))
    supplied = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    if not isinstance(supplied, dict) or set(supplied) - set(config):
        raise ValueError("Unknown stage-4 configuration key")
    config.update(supplied)
    for key in ("max_html_chars", "min_body_chars", "review_sample_limit", "export_batch_rows", "export_rows_per_file"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(key + " must be a positive integer")
    ratio = config["max_removed_fraction"]
    if type(ratio) not in (float, int) or not 0 < ratio <= 1:
        raise ValueError("max_removed_fraction must be in (0, 1]")
    if not isinstance(config["rules"], list):
        raise ValueError("rules must be a list")
    ids = set()
    for rule in config["rules"]:
        if not isinstance(rule, dict) or set(rule) != {"id", "revision", "domains", "pattern", "action", "enabled", "review_status", "evidence"}:
            raise ValueError("Invalid rule fields")
        if not isinstance(rule["id"], str) or not rule["id"] or rule["id"] in ids:
            raise ValueError("Rule IDs must be nonempty and unique")
        ids.add(rule["id"])
        if type(rule["revision"]) is not int or rule["revision"] < 1 or type(rule["enabled"]) is not bool:
            raise ValueError("Invalid rule revision/enabled value")
        if rule["action"] not in {"drop_line", "trim_tail"} or rule["review_status"] not in {"proposal", "approved"}:
            raise ValueError("Unknown rule action/review status")
        if not isinstance(rule["domains"], list) or not rule["domains"] or any(not isinstance(d, str) or not d or d == "*" for d in rule["domains"]):
            raise ValueError("Rules require explicit domains; wildcard deletion is not supported")
        if not isinstance(rule["evidence"], str) or not rule["evidence"]:
            raise ValueError("Rules require an evidence reference")
        try:
            re.compile(rule["pattern"], re.I)
        except (re.error, TypeError) as exc:
            raise ValueError("Invalid rule regex: " + rule["id"]) from exc
    return config


def check_mode(config, mode):
    if mode not in {"dry-run", "apply"}:
        raise ValueError("mode must be dry-run or apply")
    if mode == "apply" and any(r["enabled"] and r["review_status"] != "approved" for r in config["rules"]):
        raise ValueError("Apply requires reviewed rules: approve tested/reviewed rules in a separate config, or disable them. Start with dry-run.")


def clinical_signals(text):
    return {"digits": len(NUMBER.findall(text)), "negations": len(NEGATION.findall(text)),
            "units": len(UNIT.findall(text))}


def failure_reason(text, final_url="", title=None):
    """Narrow full-payload failures; mentions of captcha in articles are not gates."""
    from urllib.parse import urlsplit
    if not isinstance(text, str) or not text.strip():
        return "missing_content"
    compact = normalize(text)
    if ERROR_PAGE.fullmatch(compact):
        return "exact_error_placeholder"
    if GATE_PAGE.fullmatch(compact):
        return "exact_language_or_access_gate"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if (title and len(lines) == 2 and normalize(lines[0]) == normalize(title)
            and GATE_PAGE.fullmatch(normalize(lines[1]))):
        return "exact_language_or_access_gate"
    try:
        image = urlsplit(final_url or "").path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
    except ValueError:
        image = False
    binary_signature = any(token in text[:4096] for token in ("Exif", "Adobe", "JFIF", "\x89PNG"))
    damaged = text.count("\ufffd") >= max(3, len(text) * .05)
    if damaged and (image or binary_signature):
        return "binary_payload_as_text"
    return None


def title_body_diagnostic(title, body):
    """CJK n-grams avoid the whitespace-word mismatch; this never rejects a body."""
    if not title or not body:
        return {"method": "insufficient_text", "overlap": None, "decision": "review_only"}
    title, body = normalize(title), normalize(body)
    characters = "".join(CJK.findall(title))
    if characters:
        width = min(2, len(characters))
        a = {characters[i:i + width] for i in range(len(characters) - width + 1)}
        # Keep Chinese runs separate so punctuation cannot fabricate shared bigrams.
        b = {run[i:i + width] for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", body)
             for i in range(len(run) - width + 1)}
        method = "cjk_character_ngrams"
    else:
        a = set(re.findall(r"[^\W\d_]{2,}", title))
        b = set(re.findall(r"[^\W\d_]{2,}", body))
        method = "unicode_word_overlap"
    score = len(a & b) / len(a) if a else None
    return {"method": method, "overlap": score, "low_overlap": score is not None and score < .2,
            "decision": "review_only"}


def plan_removals(text, domain, config):
    """Propose exact line/tail intervals, vetoing protected signals and large cuts."""
    applicable = [r for r in config["rules"] if r["enabled"] and domain in r["domains"]]
    if not applicable:
        return []
    candidates = []
    lines, cursor = [], 0
    for line in text.splitlines(keepends=True):
        lines.append((cursor, cursor + len(line), line.strip()))
        cursor += len(line)
    for rule in sorted(applicable, key=lambda r: r["id"]):
        pattern = re.compile(rule["pattern"], re.I)
        for start, end, value in lines:
            if not value or not pattern.fullmatch(value):
                continue
            if rule["action"] == "trim_tail":
                end = len(text)
            reasons = []
            removed = text[start:end]
            signals = clinical_signals(removed)
            if any(signals.values()):
                reasons.append("protected_clinical_signal_veto")
            if len(text[:start].strip()) < config["min_body_chars"]:
                reasons.append("insufficient_preceding_body_veto")
            if (end - start) / max(1, len(text)) > config["max_removed_fraction"]:
                reasons.append("large_removal_veto")
            candidates.append({"start": start, "end": end, "rule_id": rule["id"],
                "rule_revision": rule["revision"], "reasons": reasons, "signals": signals})
            if rule["action"] == "trim_tail":
                break
    accepted, accepted_chars = [], 0
    for candidate in sorted(candidates, key=lambda c: (c["start"], c["rule_id"], c["end"])):
        if not candidate["reasons"]:
            if any(candidate["start"] < b["end"] and b["start"] < candidate["end"] for b in accepted):
                candidate["reasons"].append("overlapping_rule_veto")
            elif (accepted_chars + candidate["end"] - candidate["start"]) / max(1, len(text)) > config["max_removed_fraction"]:
                candidate["reasons"].append("cumulative_large_removal_veto")
            else:
                accepted.append(candidate)
                accepted_chars += candidate["end"] - candidate["start"]
        candidate["decision"] = "vetoed" if candidate["reasons"] else "remove"
    return candidates


def repeated_line_diagnostic(text):
    counts = Counter(normalize(line) for line in text.splitlines() if line.strip())
    return {"repeated_line_occurrences": sum(n - 1 for n in counts.values() if n > 1),
            "policy": "diagnostic_only_no_medical_line_deduplication"}


def rulebook_hash(config):
    return text_hash(canonical_json({"revision": REVISION, "config": config}))
