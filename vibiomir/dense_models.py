"""Pinned CUDA encoders, complete mapped token windows and bounded OOM retries."""
import math
from pathlib import Path

from .chunk_batches import read_json
from .io import canonical_json, file_hash, text_hash, write_json
from .retrieval_contracts import require

MODELS = {
    'bge_m3': {'model_id': 'BAAI/bge-m3', 'revision': '5617a9f61b028005a4858fdac845db406aefb181',
               'commit_date': '2024-07-03', 'dimension': 1024, 'pooling': 'CLS'},
    'mbert': {'model_id': 'google-bert/bert-base-multilingual-cased', 'revision': '3f076fdb1ab68d5b2880cb87a0886f315b8146f8',
              'commit_date': '2024-02-19', 'dimension': 768, 'pooling': 'masked_mean_excluding_special_and_padding'}
}


def settings(path):
    c = read_json(path)
    require(isinstance(c, dict) and c.get('schema') == 'vibiomir-dense-v1', 'Invalid dense settings schema')
    require(c['model'] in MODELS and type(c['workers']) is int and 1 <= c['workers'] <= 6, 'Choose a pinned model and 1..6 workers')
    require(type(c['max_tokens']) is int and 16 <= c['max_tokens'] <= 512 and
            type(c['window_overlap']) is int and 0 <= c['window_overlap'] < c['max_tokens'] - 2, 'Invalid mapped window budget/overlap')
    require(c['storage_dtype'] == 'float16' and type(c['seed']) is int, 'Require float16 storage and integer seed')
    weights = c.get('worker_weights', [1] * c['workers'])
    require(isinstance(weights, list) and len(weights) == c['workers'] and all(type(w) in (int, float) and math.isfinite(w) and w > 0 for w in weights),
            'worker_weights must contain one finite positive number per worker')
    return c


def tokenizer_signature(directory, model):
    registry = read_json(Path(directory) / 'REGISTRY.json')
    require((registry['model_id'], registry['revision']) == (MODELS[model]['model_id'], MODELS[model]['revision']), 'Tokenizer/model revision differs')
    for name, digest in registry['files'].items():
        require(Path(name).name == name and file_hash(Path(directory) / name) == digest, 'Tokenizer file checksum differs')
    return text_hash(canonical_json(registry))


def download_tokenizer(model, output):
    from transformers import AutoTokenizer
    output = Path(output)
    require(model in MODELS and (not output.exists() or not any(output.iterdir())), 'Choose pinned model and new empty tokenizer directory')
    spec = MODELS[model]
    tokenizer = AutoTokenizer.from_pretrained(spec['model_id'], revision=spec['revision'], trust_remote_code=False, use_fast=True)
    output.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(output)
    write_json(output / 'REGISTRY.json', {'model_id': spec['model_id'], 'revision': spec['revision'], 'commit_date': spec['commit_date'],
              'files': {p.name: file_hash(p) for p in sorted(output.iterdir()) if p.is_file()}})


def token_windows(tokenizer, text, max_tokens, overlap):
    require(isinstance(text, str) and text.strip() and len(text) <= 200000, 'Empty or >200000-character encoder input; split explicitly, never truncate')
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    ids, offsets = encoded['input_ids'], encoded['offset_mapping']
    budget = max_tokens - tokenizer.num_special_tokens_to_add(pair=False)
    require(ids and budget > overlap, 'No content tokens or invalid tokenizer/window budget')
    start, output = 0, []
    while start < len(ids):
        end = min(start + budget, len(ids))
        feature = tokenizer.prepare_for_model(ids[start:end], add_special_tokens=True, return_attention_mask=True,
                                               return_special_tokens_mask=True, truncation=False)
        require(len(feature['input_ids']) <= max_tokens, 'Encoder window exceeds budget')
        output.append({'features': feature, 'token_start': start, 'token_end': end,
                       'character_start': offsets[start][0], 'character_end': offsets[end - 1][1],
                       'weight': end - start})
        if end == len(ids):
            break
        start = end - overlap
    return output


def pool_hidden(hidden, attention_mask, special_mask, kind):
    """Kept separate for direct CPU tensor fixtures; no model/network needed."""
    if kind == 'CLS':
        return hidden[:, 0].float()
    mask = attention_mask.bool() & ~special_mask.bool()
    require(bool(mask.any(dim=1).all()), 'Masked-mean window has no content tokens')
    return (hidden.float() * mask.unsqueeze(-1)).sum(dim=1) / mask.sum(dim=1).unsqueeze(-1)


class DenseEncoder:
    def __init__(self, c, tokenizer_dir, *, batch_size=16, padded_tokens=8192):
        import torch
        import transformers
        import tokenizers
        from transformers import AutoModel, AutoTokenizer
        require(torch.cuda.is_available(), 'Dense encoding needs CUDA; select a Colab GPU runtime')
        require(batch_size > 0 and padded_tokens >= c['max_tokens'], 'Invalid GPU microbatch budget')
        tokenizer_signature(tokenizer_dir, c['model'])
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False, use_fast=True)
        require(self.tokenizer.is_fast, 'A fast tokenizer is required for mapped windows')
        self.tokenizer.model_max_length = 10**12
        self.c, self.torch, self.batch_size, self.padded_tokens = c, torch, batch_size, padded_tokens
        self.spec = MODELS[c['model']]
        self.model = AutoModel.from_pretrained(self.spec['model_id'], revision=self.spec['revision'], trust_remote_code=False,
                                               torch_dtype=torch.float16).eval().to('cuda')
        parameters = sum(p.numel() for p in self.model.parameters())
        require(0 < parameters <= 15000000000 and self.model.config.hidden_size == self.spec['dimension'], 'Invalid model size/dimension')
        self.registry = {**self.spec, 'parameters': parameters, 'compute_dtype': 'float16', 'storage_dtype': 'float16',
                         'torch': torch.__version__, 'transformers': transformers.__version__, 'tokenizers': tokenizers.__version__,
                         'device': torch.cuda.get_device_name(0),
                         'aggregation': 'token-count-weighted mean of L2-normalized mapped windows, then L2; overlap counted in each window'}

    def _infer(self, features):
        torch = self.torch
        batch = self.tokenizer.pad(features, padding=True, return_tensors='pt')
        special = batch.pop('special_tokens_mask').to('cuda')
        batch = {k: v.to('cuda') for k, v in batch.items()}
        with torch.inference_mode():
            hidden = self.model(**batch).last_hidden_state
            pooled = pool_hidden(hidden, batch['attention_mask'], special, self.spec['pooling'])
            return torch.nn.functional.normalize(pooled, p=2, dim=1).cpu().numpy()

    def _safe(self, features):
        import numpy as np
        try:
            result = self._infer(features)
        except self.torch.OutOfMemoryError:
            result = None
        if result is not None:
            return result
        self.torch.cuda.empty_cache()
        require(len(features) > 1, 'One mapped window does not fit CUDA; choose more VRAM. Input was not truncated')
        self.batch_size = min(self.batch_size, max(1, len(features) // 2))
        middle = len(features) // 2
        return np.concatenate([self._safe(features[:middle]), self._safe(features[middle:])])

    def encode(self, texts):
        import numpy as np
        # Caller bounds this text wave; all windows remain mapped and no text
        # disappears when the alternate mBERT tokenizer exceeds 512 tokens.
        windows = [token_windows(self.tokenizer, text, self.c['max_tokens'], self.c['window_overlap']) for text in texts]
        flat = [window for group in windows for window in group]
        vectors = np.empty((len(flat), self.spec['dimension']), dtype='float32')
        order = sorted(range(len(flat)), key=lambda i: len(flat[i]['features']['input_ids']))
        pending = []
        def flush():
            if pending:
                vectors[pending] = self._safe([flat[i]['features'] for i in pending])
                pending.clear()
        for index in order:
            cost = len(flat[index]['features']['input_ids'])
            if pending and (len(pending) >= self.batch_size or (len(pending) + 1) * cost > self.padded_tokens):
                flush()
            pending.append(index)
        flush()
        output, offset, counts = [], 0, []
        for group in windows:
            weight = np.asarray([w['weight'] for w in group], dtype='float32')
            vector = (vectors[offset:offset + len(group)] * weight[:, None]).sum(axis=0)
            norm = np.linalg.norm(vector)
            require(np.isfinite(vector).all() and norm > 0, 'Nonfinite or zero dense vector')
            output.append(vector / norm)
            counts.append(len(group))
            offset += len(group)
        return np.asarray(output, dtype='float32'), counts
