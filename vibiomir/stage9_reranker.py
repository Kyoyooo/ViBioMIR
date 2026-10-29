"""Pinned multilingual CUDA cross-encoder with complete bounded pair windows."""
import json
import math

from .io import canonical_json
from .retrieval_contracts import require
from .stage9_core import PINNED_RERANKER, score_key


def recommended_runtime(memory_gib):
    """Conservative initial budgets; explicit overrides remain available."""
    require(type(memory_gib) in (int, float) and math.isfinite(memory_gib) and memory_gib > 0,
            'Positive finite CUDA memory size required')
    if memory_gib >= 70: return {'batch_size': 64, 'padded_tokens': 65536}
    if memory_gib >= 35: return {'batch_size': 32, 'padded_tokens': 32768}
    if memory_gib >= 20: return {'batch_size': 16, 'padded_tokens': 16384}
    return {'batch_size': 8, 'padded_tokens': 8192}


def spans(length, budget, overlap):
    require(length > 0 and budget > overlap >= 0, 'Invalid complete-window token budget')
    start = 0
    while start < length:
        end = min(start + budget, length)
        yield start, end
        if end == length: break
        start = end - overlap


def pair_windows(tokenizer, query, passage, policy):
    """Cartesian windows preserve every token of both inputs without truncation."""
    require(isinstance(query, str) and query.strip() and isinstance(passage, str) and passage.strip() and
            len(query) <= 200000 and len(passage) <= 200000, 'Empty or >200000-character reranker input')
    q = tokenizer(query, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    p = tokenizer(passage, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    require(q['input_ids'] and p['input_ids'], 'Pair has no content tokens')
    special = tokenizer.num_special_tokens_to_add(pair=True)
    result = []
    for query_index, (a, b) in enumerate(spans(len(q['input_ids']), policy['query_window_tokens'], policy['window_overlap'])):
        capacity = policy['max_pair_tokens'] - (b - a) - special
        for c, d in spans(len(p['input_ids']), capacity, policy['window_overlap']):
            require(len(result) < 4096, 'Pair needs more than 4096 windows; use an explicitly reviewed smaller input or a new larger-context plan')
            feature = tokenizer.prepare_for_model(q['input_ids'][a:b], pair_ids=p['input_ids'][c:d],
                        add_special_tokens=True, return_attention_mask=True, return_token_type_ids=False,
                        truncation=False)
            require(len(feature['input_ids']) <= policy['max_pair_tokens'], 'Pair window exceeds context budget')
            result.append({'features': dict(feature), 'query_window': query_index,
                           'query_tokens': [a, b], 'passage_tokens': [c, d],
                           'query_characters': [q['offset_mapping'][a][0], q['offset_mapping'][b - 1][1]],
                           'passage_characters': [p['offset_mapping'][c][0], p['offset_mapping'][d - 1][1]]})
    return result


def aggregate(windows, logits):
    require(len(windows) == len(logits) and windows and all(math.isfinite(x) for x in logits), 'Invalid window logits')
    groups = {}
    for window, score in zip(windows, logits):
        group = window['query_window']
        groups[group] = max(groups.get(group, -math.inf), float(score))
    return sum(groups.values()) / len(groups)


class Reranker:
    def __init__(self, policy, *, batch_size=8, padded_tokens=8192):
        import torch
        import transformers
        import tokenizers
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        require(torch.cuda.is_available(), 'Reranking requires CUDA; use the supplied S09 Colab notebook')
        require(batch_size > 0 and padded_tokens >= policy['max_pair_tokens'],
                'GPU padded-token budget must hold one complete pair window')
        require(all(policy[k] == v for k, v in PINNED_RERANKER.items()), 'Unexpected reranker revision')
        self.torch, self.policy = torch, policy
        self.batch_size, self.padded_tokens = batch_size, padded_tokens
        self.wave_size = min(128, max(16, batch_size * 2))
        self.tokenizer = AutoTokenizer.from_pretrained(policy['model_id'], revision=policy['revision'],
                                                       trust_remote_code=False, use_fast=True)
        require(self.tokenizer.is_fast, 'Mapped reranker windows require a fast tokenizer')
        self.tokenizer.model_max_length = 10**12
        self.model = AutoModelForSequenceClassification.from_pretrained(policy['model_id'], revision=policy['revision'],
                        trust_remote_code=False, torch_dtype=torch.float16, use_safetensors=True).eval().to('cuda')
        count = sum(p.numel() for p in self.model.parameters())
        require(0 < count <= 15000000000 and self.model.config.num_labels == 1 and
                policy['max_pair_tokens'] <= self.model.config.max_position_embeddings - 2, 'Unexpected reranker model size/context/head')
        self.registry = {**PINNED_RERANKER, 'parameters': count, 'compute_dtype': 'float16',
                         'scores': 'raw logits; not calibrated relevance probabilities',
                         'window_policy': policy, 'torch': torch.__version__, 'transformers': transformers.__version__,
                         'tokenizers': tokenizers.__version__, 'device': torch.cuda.get_device_name(0),
                         'batch_size_requested': batch_size, 'padded_tokens_requested': padded_tokens,
                         'passage_wave': self.wave_size}

    def _infer(self, features):
        batch = self.tokenizer.pad(features, padding=True, return_tensors='pt')
        batch = {k: v.to('cuda') for k, v in batch.items()}
        with self.torch.inference_mode():
            return self.model(**batch, return_dict=True).logits.reshape(-1).float().cpu().tolist()

    def _safe(self, features):
        try:
            return self._infer(features)
        except self.torch.OutOfMemoryError:
            pass
        self.torch.cuda.empty_cache()
        require(len(features) > 1, 'One complete pair window cannot fit CUDA; select a larger GPU or a new smaller-window plan')
        self.batch_size = min(self.batch_size, max(1, len(features) // 2))
        middle = len(features) // 2
        return self._safe(features[:middle]) + self._safe(features[middle:])

    def score(self, query, passages, cache):
        # Bound token/feature memory independently of the GPU microbatch size.
        wave = getattr(self, 'wave_size', 16)
        if len(passages) > wave:
            return [value for start in range(0, len(passages), wave)
                    for value in self.score(query, passages[start:start + wave], cache)]
        output, windows, owners, pending, aliases = [None] * len(passages), [], [], {}, []
        for index, passage in enumerate(passages):
            key = score_key(query, passage, self.policy)
            hit = cache.execute('SELECT payload FROM scores WHERE key=?', (key,)).fetchone()
            if hit:
                value = json.loads(hit[0])
                require(type(value['score']) in (int, float) and math.isfinite(value['score']) and
                        type(value['windows']) is int and value['windows'] > 0, 'Invalid cached rerank score')
                output[index] = value
                continue
            if key in pending:
                aliases.append((index, pending[key]))
                continue
            pending[key] = index
            current = pair_windows(self.tokenizer, query, passage, self.policy)
            offset = len(windows)
            owners.append((index, key, offset, len(current)))
            windows.extend(current)
        logits = [None] * len(windows)
        order = sorted(range(len(windows)), key=lambda i: len(windows[i]['features']['input_ids']))
        start = 0
        while start < len(order):
            batch, maximum = [], 0
            for index in order[start:]:
                length = len(windows[index]['features']['input_ids'])
                proposed = max(maximum, length)
                if batch and (len(batch) >= self.batch_size or (len(batch) + 1) * proposed > self.padded_tokens): break
                batch.append(index); maximum = proposed
            require(batch and len(batch) * maximum <= self.padded_tokens, 'One pair exceeds padded-token budget')
            values = self._safe([windows[i]['features'] for i in batch])
            require(len(values) == len(batch) and all(math.isfinite(v) for v in values), 'Nonfinite or missing reranker logits')
            for index, value in zip(batch, values): logits[index] = value
            start += len(batch)
        for index, key, offset, count in owners:
            current = windows[offset:offset + count]
            value = {'score': aggregate(current, logits[offset:offset + count]), 'windows': count,
                     'window_spans': [{k: v for k, v in x.items() if k != 'features'} for x in current]}
            output[index] = value
            cache.execute('INSERT OR REPLACE INTO scores VALUES(?,?)', (key, canonical_json(value)))
        for index, owner in aliases: output[index] = output[owner]
        cache.commit()
        return output
