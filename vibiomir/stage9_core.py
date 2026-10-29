"""Deterministic fusion, conservative query metadata and original-span selection."""
from collections import Counter
import math
import re

from .io import canonical_json, text_hash
from .retrieval_contracts import require


PINNED_RERANKER = {
    'model_id': 'BAAI/bge-reranker-v2-m3',
    'revision': '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e',
    'commit_date': '2024-06-24',
}
MAPPING_FIELDS = ('chunk_id', 'doc_id', 'derived_version_id', 'source_batch', 'source_row',
                  'chunk_order', 'retrieval_text_sha256')


def positive(value):
    return type(value) is int and value > 0


def validate_settings(c):
    require(c.get('schema') == 'vibiomir-stage9-v1', 'Invalid S09 config schema')
    require(c['fusion'] in {'rrf', 'weighted'} and positive(c['rrf_k']), 'Invalid fusion settings')
    require(type(c['lexical_alpha']) in (int, float) and math.isfinite(c['lexical_alpha']) and
            0 < c['lexical_alpha'] < 1, 'lexical_alpha must be between zero and one')
    require(positive(c['fused_top_k']) and positive(c['rerank_top_k']) and
            c['rerank_top_k'] <= c['fused_top_k'], 'Invalid candidate budgets')
    require(type(c['workers']) is int and 1 <= c['workers'] <= 6, 'Choose 1..6 independent rerank workers')
    branches = c['branches']
    require(isinstance(branches, list) and branches and len({b['name'] for b in branches}) == len(branches),
            'Branches must have unique names')
    require(any(b.get('facet_id', 'original') == 'original' for b in branches), 'Keep an original-query branch')
    require(all(b['family'] in {'dense', 'lexical'} and isinstance(b['name'], str) and b['name']
                for b in branches), 'Invalid branch names/families')
    require(len({b.get('facet_id', 'original') for b in branches} - {'original'}) <= 3,
            'At most three supplementary facet branches')
    model = c['reranker']
    require(all(model.get(k) == v for k, v in PINNED_RERANKER.items()), 'Use the pinned public reranker revision')
    require(positive(model['max_pair_tokens']) and 32 <= model['max_pair_tokens'] <= 8192 and
            positive(model['query_window_tokens']) and model['query_window_tokens'] <= model['max_pair_tokens'] - 16 and
            type(model['window_overlap']) is int and 0 <= model['window_overlap'] <
            min(model['query_window_tokens'], model['max_pair_tokens'] - model['query_window_tokens'] - 4),
            'Invalid pair/query window budgets; overlap must leave room for both inputs')
    require(model['aggregation'] == 'mean_query_windows_of_max_passage_logit', 'Unsupported window aggregation')
    selection = c['selection']
    require(all(positive(selection[k]) for k in ('max_docs', 'max_chunks', 'max_chunks_per_doc')) and
            type(selection['overlap_iou_threshold']) in (int, float) and
            0 <= selection['overlap_iou_threshold'] <= 1, 'Invalid output selection limits')
    return c


def query_metadata(text):
    """Signals for review only; never rewrite queries or infer diagnoses."""
    patterns = {
        'definition': r'\b(là gì|định nghĩa)\b', 'symptoms': r'\b(triệu chứng|dấu hiệu|biểu hiện)\b',
        'diagnosis': r'\b(chẩn đoán|xét nghiệm)\b', 'treatment': r'\b(điều trị|xử trí|chữa|cần làm gì)\b',
        'drugs_doses': r'\b(thuốc|liều|mg|ml)\b', 'prevention': r'\b(phòng ngừa|phòng tránh)\b',
        'prognosis': r'\b(tiên lượng|biến chứng)\b',
    }
    signals = [{'intent': name, 'start': m.start(), 'end': m.end(), 'text': text[m.start():m.end()]}
               for name, pattern in patterns.items() for m in re.finditer(pattern, text, flags=re.I)]
    return {'original_query': text, 'intent_signals': signals,
            'multi_intent_signal': len({s['intent'] for s in signals}) > 1,
            'clinical_intent_verified': False, 'automatic_query_rewrite': False}


def validate_facets(text, facets):
    require(isinstance(facets, list) and len(facets) <= 3, 'At most three mapped facets per query')
    require(len({f['id'] for f in facets}) == len(facets), 'Duplicate facet IDs')
    for facet in facets:
        a, b = facet['start'], facet['end']
        require(isinstance(facet['id'], str) and facet['id'] and facet['id'] != 'original' and
                type(a) is int and type(b) is int and 0 <= a < b <= len(text) and
                facet['text'] == text[a:b], 'Facet must be an exact original-query slice')
    return facets


def fuse(branches, c):
    """Equal branch shares within each family; stable chunk-ID tie breaking."""
    counts = Counter(b['family'] for b, _ in branches)
    weights = {'lexical': c['lexical_alpha'], 'dense': 1 - c['lexical_alpha']}
    denominator = sum(weights[f] for f in counts)
    merged = {}
    for branch, items in branches:
        seen, previous = set(), math.inf
        scores = [item['score'] for item in items]
        require(all(type(s) in (int, float) and math.isfinite(s) for s in scores), 'Nonfinite candidate score')
        low, high = (min(scores), max(scores)) if scores else (0, 0)
        weight = weights[branch['family']] / denominator / counts[branch['family']]
        for rank, item in enumerate(items, 1):
            key, score = item['chunk_id'], item['score']
            require(key not in seen and score <= previous, 'Duplicate or unsorted branch candidates')
            seen.add(key); previous = score
            require(type(item['doc_id']) is int and isinstance(key, str) and key and
                    all(k in item for k in MAPPING_FIELDS) and
                    all(type(item[k]) is int and item[k] >= 0 for k in ('source_batch', 'source_row', 'chunk_order')),
                    'Invalid candidate mapping')
            if key not in merged:
                merged[key] = {k: item[k] for k in MAPPING_FIELDS}
                merged[key].update(fused_score=0.0, branches=[])
            output = merged[key]
            require(all(output[k] == item[k] for k in MAPPING_FIELDS), 'Conflicting mappings for one chunk')
            normalized = (score - low) / (high - low) if high > low else 0.0
            contribution = weight / (c['rrf_k'] + rank) if c['fusion'] == 'rrf' else weight * normalized
            output['fused_score'] += contribution
            output['branches'].append({'name': branch['name'], 'family': branch['family'],
                'facet_id': branch.get('facet_id', 'original'), 'rank': rank, 'raw_score': score,
                'normalized_score': normalized, 'weight': weight})
    return sorted(merged.values(), key=lambda x: (-x['fused_score'], x['chunk_id']))[:c['fused_top_k']]


def overlap(a, b):
    if (a['doc_id'], a['derived_version_id'], a['region_id']) != (b['doc_id'], b['derived_version_id'], b['region_id']):
        return 0.0
    intersection = max(0, min(a['region_end'], b['region_end']) - max(a['region_start'], b['region_start']))
    union = max(a['region_end'], b['region_end']) - min(a['region_start'], b['region_start'])
    return intersection / union if union else 0.0


def select(candidates, scores, limits):
    """Max chunk score per document; dedup only within a doc/version."""
    require(len(scores) == len(candidates) and all(type(s) in (int, float) and math.isfinite(s) for s in scores),
            'Invalid selection scores')
    ranked = sorted(({**item, 'selection_score': score} for item, score in zip(candidates, scores)),
                    key=lambda x: (-x['selection_score'], x['chunk_id']))
    document_scores = {}
    for item in ranked:
        document_scores.setdefault(item['doc_id'], item['selection_score'])
    docs = sorted(document_scores, key=lambda d: (-document_scores[d], d))[:limits['max_docs']]
    allowed, selected, reasons, per_doc = set(docs), [], [], Counter()
    versions = {}
    for item in ranked:
        reason = None
        doc = item['doc_id']
        if doc not in allowed: reason = 'document_cap'
        elif len(selected) >= limits['max_chunks']: reason = 'global_chunk_cap'
        elif per_doc[doc] >= limits['max_chunks_per_doc']: reason = 'per_document_cap'
        elif doc in versions and versions[doc] != item['derived_version_id']: reason = 'different_document_version'
        else:
            peers = [x for x in selected if (x['doc_id'], x['derived_version_id']) == (doc, item['derived_version_id'])]
            if any(x['chunk_text'] == item['chunk_text'] for x in peers): reason = 'exact_duplicate_in_document'
            elif any(overlap(x, item) >= limits['overlap_iou_threshold'] and overlap(x, item) > 0 for x in peers): reason = 'overlapping_span'
        if reason is None:
            selected.append(item); per_doc[doc] += 1; versions[doc] = item['derived_version_id']
        reasons.append({'chunk_id': item['chunk_id'], 'doc_id': doc,
                        'score': item['selection_score'], 'reason': reason or 'selected'})
    # Documents can retain a score even when the independent global chunk cap
    # leaves no chunk for them. Every exported chunk still has its parent doc.
    prediction = {'relevant_docs': docs, 'relevant_chunks': [
        {'doc_id': x['doc_id'], 'chunk_text': x['chunk_text'], 'chunk_order': n}
        for n, x in enumerate(selected)]}
    return prediction, {'document_scores': [{'doc_id': d, 'score': document_scores[d]} for d in docs],
                        'selected_chunks': selected, 'decisions': reasons}


def score_key(query, passage, policy):
    return text_hash(canonical_json({'query_sha256': text_hash(query),
                                    'passage_sha256': text_hash(passage), 'policy': policy}))
