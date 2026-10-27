"""Conservative chapter-level topic metadata, never patient diagnoses or gold labels."""
import math
from pathlib import Path
import re
import unicodedata

from .chunk_batches import read_json
from .io import canonical_json, text_hash

CHAPTERS = ('I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX XXI XXII').split()
RANGES = ('A00:B99 C00:D48 D50:D89 E00:E90 F00:F99 G00:G99 H00:H59 H60:H95 I00:I99 J00:J99 '
          'K00:K93 L00:L99 M00:M99 N00:N99 O00:O99 P00:P96 Q00:Q99 R00:R99 S00:T98 V01:Y98 Z00:Z99 U00:U85').split()
NEGATION = re.compile(r'(?:\bno|\bnot|\bwithout|\bdenies|\brule out|\bsuspected|\bpossible|'
                      r'không(?: mắc| có)?|chưa chẩn đoán|nghi ngờ|否认|疑似|排除|无)\s*$', re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def settings(path):
    value = read_json(path)
    require(isinstance(value, dict), 'ICD settings must be a JSON object')
    require(value.get('schema') == 'vibiomir-icd-settings-v1', 'Unsupported ICD settings schema')
    for key in ('rule_threshold', 'semantic_threshold', 'semantic_margin'):
        require(type(value.get(key)) in (int, float) and math.isfinite(value[key]) and 0 <= value[key] <= 1,
                f'{key} must be finite and within [0,1]')
    for key in ('max_labels', 'max_evidence_per_label', 'max_semantic_segments', 'segment_tokens',
                'segment_characters', 'rules_scan_characters_per_field'):
        require(type(value.get(key)) is int and value[key] > 0, f'{key} must be a positive integer')
    require(value['segment_tokens'] <= 510 and value['max_labels'] <= 22, 'Require segment_tokens <=510 and max_labels <=22')
    require(value.get('calibration') == 'pending' and value.get('retrieval_boost_enabled') is False and
            value.get('hard_filter_enabled') is False, 'This uncalibrated delivery requires disabled ICD boosts/filters')
    require(isinstance(value.get('rules_revision'), str) and value['rules_revision'], 'Declare rules_revision')
    return value


def load_taxonomy(path):
    value = read_json(path)
    require(isinstance(value, dict) and value.get('schema') == 'vibiomir-icd10-chapters-v1', 'Unsupported taxonomy schema')
    require(value.get('classification') == 'WHO ICD-10' and value.get('version') == '2019', 'Pin WHO ICD-10 2019; do not mix ICD-10-CM')
    rows = value.get('chapters', [])
    require([r['id'] for r in rows] == CHAPTERS and [':'.join(r['range']) for r in rows] == RANGES,
            'Require all 22 ordered chapters and exact chapter ranges')
    for row in rows:
        require(set(row['topics']) == set(row['terms']) == {'vi', 'en', 'zh'}, 'Every chapter needs VI/EN/ZH seeds')
        require(all(isinstance(t, str) and t.strip() for t in row['topics'].values()), 'Empty chapter description')
        require(all(isinstance(v, list) and v and all(isinstance(t, str) and t.strip() for t in v)
                    for v in row['terms'].values()), 'Empty/invalid seed phrases')
    return value


def normalized_with_offsets(text):
    """Normalize combining clusters, retaining original Python code-point spans."""
    folded = text.casefold()
    if unicodedata.normalize('NFKC', text) == text and len(folded) == len(text) and not re.search(r'\s{2,}', text):
        return re.sub(r'\s', ' ', folded), range(len(text)), range(1, len(text) + 1)
    result, starts, ends, index = [], [], [], 0
    while index < len(text):
        end = index + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        normalized = unicodedata.normalize('NFKC', text[index:end]).casefold()
        for character in normalized:
            if character.isspace():
                if result and result[-1] == ' ':
                    ends[-1] = end
                    continue
                character = ' '
            result.append(character)
            starts.append(index)
            ends.append(end)
        index = end
    return ''.join(result), starts, ends


def fields(packet):
    if packet.get('title'):
        yield {'field': 'title', 'region_id': None, 'text': packet['title'], 'source_start': None,
               'source_kind': 'selected_metadata', 'source_locator_json': None}
    for region in packet.get('regions', []):
        yield {'field': 'body', **region}


def evidence(field, start, end):
    result = {key: field.get(key) for key in ('field', 'region_id', 'source_kind', 'source_locator_json')}
    result.update(start=start, end=end, text=field['text'][start:end])
    result['text_sha256'] = text_hash(result['text'])
    base = field.get('source_start')
    result.update(source_start=base + start if base is not None else None,
                  source_end=base + end if base is not None else None)
    return result


class RuleTagger:
    def __init__(self, taxonomy, config):
        self.config, self.phrases = config, {}
        for chapter in taxonomy['chapters']:
            for language, terms in chapter['terms'].items():
                for term in terms:
                    normalized = normalized_with_offsets(term)[0]
                    self.phrases.setdefault(normalized, []).append((chapter['id'], language))
        patterns = []
        for term in sorted(self.phrases, key=lambda t: (-len(t), t)):
            han = bool(re.search(r'[\u3400-\u9fff]', term))
            patterns.append(re.escape(term) if han else r'(?<!\w)' + re.escape(term) + r'(?!\w)')
        self.pattern = re.compile('|'.join(patterns))

    def tag(self, packet):
        matches, suppressed, scanned, total = {}, [], 0, 0
        cap = self.config['max_evidence_per_label']
        for field in fields(packet):
            text = field['text']
            total += len(text)
            limited = text[:self.config['rules_scan_characters_per_field']]
            scanned += len(limited)
            normalized, starts, ends = normalized_with_offsets(limited)
            for match in self.pattern.finditer(normalized):
                for chapter, language in self.phrases[match.group()]:
                    item = {**evidence(field, starts[match.start()], ends[match.end() - 1]),
                            'chapter': chapter, 'language': language, 'seed': match.group(),
                            'assertion': 'topic_mention_not_diagnosis'}
                    if NEGATION.search(normalized[max(0, match.start() - 60):match.start()]):
                        item['assertion'] = 'negated_or_uncertain'
                        if len(suppressed) < cap * 22:
                            suppressed.append(item)
                        continue
                    group = matches.setdefault(chapter, {'score': 0.0, 'evidence': []})
                    group['score'] = max(group['score'], 1.0 if field['field'] == 'title' else 0.8)
                    if len(group['evidence']) < cap:
                        group['evidence'].append(item)
        return {'candidates': [{'chapter': ch, **value} for ch, value in sorted(matches.items(), key=lambda x: (-x[1]['score'], CHAPTERS.index(x[0])))],
                'suppressed_matches': suppressed, 'scanned_characters': scanned, 'available_characters': total,
                'scan_limited': scanned < total}


def bounded_segments(packet, tokenizer, config):
    """Sample source character windows before tokenization; no whole-document token arrays."""
    available = list(fields(packet))
    available = [field for field in available if field['text'].strip()]
    if not available:
        return []
    body = [f for f in available if f['field'] != 'title']
    title = [f for f in available if f['field'] == 'title']
    selected = []
    if title:
        selected.append((title[0], 0))
    slots = config['max_semantic_segments'] - len(selected)
    total = sum(len(f['text']) for f in body)
    for index in range(max(0, slots)):
        if not total:
            break
        position = round((total - 1) * index / max(1, slots - 1))
        for field in body:
            if position < len(field['text']):
                start = min(position, max(0, len(field['text']) - config['segment_characters']))
                selected.append((field, start))
                break
            position -= len(field['text'])
    segments, seen = [], set()
    for field, start in selected:
        text = field['text'][start:start + config['segment_characters']]
        offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)['offset_mapping']
        if not offsets:
            continue
        stop = min(len(text), offsets[min(len(offsets), config['segment_tokens']) - 1][1])
        while stop > 0 and len(tokenizer.encode(text[:stop], add_special_tokens=True, truncation=False)) > 512:
            stop //= 2
        if stop and text[:stop].strip():
            item = evidence(field, start, start + stop)
            key = (item['field'], item['region_id'], item['start'], item['end'])
            if key not in seen:
                segments.append(item)
                seen.add(key)
    return segments


def prototypes(taxonomy):
    return [(row['id'], language, row['topics'][language] + '. ' + '; '.join(row['terms'][language]))
            for row in taxonomy['chapters'] for language in ('vi', 'en', 'zh')]


def semantic_candidates(segments, vectors, prototype_rows, prototype_vectors):
    import numpy as np
    require(len(segments) == len(vectors) and len(prototype_rows) == len(prototype_vectors), 'Vector accounting differs')
    if not segments:
        return []
    left, right = np.asarray(vectors, dtype=np.float32), np.asarray(prototype_vectors, dtype=np.float32)
    require(left.ndim == right.ndim == 2 and left.shape[1] == right.shape[1] and left.shape[1] > 0,
            'Prototype dimension error')
    require(np.isfinite(left).all() and np.isfinite(right).all(), 'Nonfinite vector')
    ln, rn = np.linalg.norm(left, axis=1), np.linalg.norm(right, axis=1)
    require((ln > 0).all() and (rn > 0).all(), 'Zero vector')
    scores = np.clip((left / ln[:, None]) @ (right / rn[:, None]).T, -1, 1)
    output = {}
    for index, (chapter, language, _) in enumerate(prototype_rows):
        best = int(scores[:, index].argmax())
        cosine = float(scores[best, index])
        if chapter not in output or cosine > output[chapter]['score']:
            output[chapter] = {'chapter': chapter, 'score': cosine, 'prototype_language': language,
                               'evidence': [segments[best]]}
    return sorted(output.values(), key=lambda row: (-row['score'], CHAPTERS.index(row['chapter'])))


def select(candidates, threshold, config, *, margin=None):
    chosen = [c['chapter'] for c in candidates if c['score'] >= threshold]
    if not chosen:
        return [], 'below_threshold_or_no_evidence'
    if margin is not None and len(chosen) == 1 and len(candidates) > 1 and candidates[0]['score'] - candidates[1]['score'] < margin:
        return [], 'ambiguous_semantic_margin'
    if len(chosen) > config['max_labels']:
        return [], 'label_limit_exceeded'
    return chosen, 'topic_evidence'


def annotate(packet, rule_result, semantic, config, mode, signature):
    rule_labels, rule_reason = select(rule_result['candidates'], config['rule_threshold'], config)
    semantic_labels, semantic_reason = select(semantic, config['semantic_threshold'], config, margin=config['semantic_margin'])
    # Symptoms do not establish a disease; uncertain/negated mentions cannot
    # be promoted into a positive label solely by embedding similarity.
    if rule_labels == ['XVIII']:
        semantic_labels = [ch for ch in semantic_labels if ch == 'XVIII']
        semantic_reason = 'symptom_only_guard'
    suppressed = {r['chapter'] for r in rule_result['suppressed_matches']} - set(rule_labels)
    semantic_labels = [ch for ch in semantic_labels if ch not in suppressed]
    if not semantic_labels and suppressed:
        semantic_reason = 'negated_or_uncertain_guard'
    methods = ['rules'] if mode == 'rules' else ['semantic'] if mode == 'semantic' else ['rules', 'semantic', 'combined']
    outputs = {}
    for method in methods:
        labels, reason = (rule_labels, rule_reason) if method == 'rules' else (semantic_labels, semantic_reason)
        if method == 'combined':
            if rule_labels and semantic_labels and not set(rule_labels) & set(semantic_labels):
                labels, reason = [], 'rule_semantic_disagreement'
            elif rule_labels:
                labels, reason = sorted(set(rule_labels) | set(semantic_labels), key=CHAPTERS.index), 'rules_with_optional_semantic_support'
            else:
                labels, reason = semantic_labels, semantic_reason
            if len(labels) > config['max_labels']:
                labels, reason = [], 'label_limit_exceeded'
        candidates = rule_result['candidates'] if method == 'rules' else semantic if method == 'semantic' else rule_result['candidates'] + semantic
        if method == 'semantic':
            # Keep all 22 scores for deterministic threshold/margin validation;
            # retain source text only for leading/selected candidates.
            retained = [{**c, 'evidence': c['evidence'] if c['chapter'] in labels or i < config['max_labels'] else []}
                        for i, c in enumerate(candidates)]
        else:
            retained = [c for c in candidates if c['chapter'] in labels or c in candidates[:config['max_labels']]]
        outputs[method] = {'annotation_id': text_hash(canonical_json([signature, packet['doc_id'], packet.get('derived_version_id'), method])),
            'method': method, 'labels': labels, 'unknown': not bool(labels), 'reason': reason,
            'confidence': None, 'score_semantics': 'rule strength / cosine; neither is a calibrated probability',
            'candidates': retained, 'calibration': 'pending', 'human_reviewed': False,
            'retrieval_boost_enabled': False, 'hard_filter_enabled': False}
    return {'doc_id': packet['doc_id'], 'derived_version_id': packet.get('derived_version_id'),
            'parent_version_id': packet.get('parent_version_id'), 'availability': packet.get('availability'),
            'scope': 'document_topic_not_patient_diagnosis', 'annotations': outputs,
            'rule_scan': {k: v for k, v in rule_result.items() if k != 'candidates'},
            'semantic_scope': 'bounded source samples; not exhaustive document encoding' if mode != 'rules' else 'not run'}
