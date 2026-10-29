"""Pinned CPU-only adapters for a small S10 pilot, leaving frozen GPU code intact."""
from pathlib import Path

from .dense_models import DenseEncoder, MODELS, pool_hidden
from .dense_tokenizer_compat import SpecialMaskTokenizer
from .stage9_core import PINNED_RERANKER
from .stage9_reranker import Reranker
from .retrieval_contracts import require


def setup(threads):
    import torch
    require(type(threads) is int and 1 <= threads <= 8, 'Choose 1..8 CPU threads')
    torch.set_num_threads(threads)
    return torch


def weight_file(spec):
    # The pinned July-2024 BGE-M3 snapshot predates its safetensors release.
    return 'pytorch_model.bin' if spec['model_id'] == 'BAAI/bge-m3' else 'model.safetensors'


class CPUEncoder(DenseEncoder):
    def __init__(self, model, cache_dir, *, threads=4, batch_size=4):
        from transformers import AutoModel, AutoTokenizer
        self.torch = setup(threads)
        self.spec = MODELS[model]
        self.c = {'max_tokens': 512, 'window_overlap': 32}
        self.batch_size, self.padded_tokens = batch_size, batch_size * 512
        tokenizer = AutoTokenizer.from_pretrained(self.spec['model_id'], revision=self.spec['revision'],
                    cache_dir=cache_dir, local_files_only=True, use_fast=True, trust_remote_code=False)
        require(tokenizer.is_fast, 'Fast tokenizer required')
        tokenizer.model_max_length = 10**12
        self.tokenizer = SpecialMaskTokenizer(tokenizer)
        self.model = AutoModel.from_pretrained(self.spec['model_id'], revision=self.spec['revision'],
                    cache_dir=cache_dir, local_files_only=True, trust_remote_code=False,
                    use_safetensors=weight_file(self.spec).endswith('.safetensors'),
                    weights_only=True, dtype=self.torch.float32).eval().to('cpu')
        count = sum(p.numel() for p in self.model.parameters())
        require(0 < count <= 15_000_000_000 and self.model.config.hidden_size == self.spec['dimension'], 'Unexpected model size/dimension')
        self.registry = {**self.spec, 'parameters': count, 'device': 'cpu', 'compute_dtype': 'float32',
                         'threads': threads, 'batch_size': batch_size, 'window_policy': self.c,
                         'torch': self.torch.__version__, 'production_equivalence': 'not claimed; production uses CUDA FP16'}

    def _infer(self, features):
        batch = self.tokenizer.pad(features, padding=True, return_tensors='pt')
        special = batch.pop('special_tokens_mask')
        with self.torch.inference_mode():
            hidden = self.model(**batch).last_hidden_state
            pooled = pool_hidden(hidden, batch['attention_mask'], special, self.spec['pooling'])
            return self.torch.nn.functional.normalize(pooled, p=2, dim=1).numpy()

    def _safe(self, features):
        return self._infer(features)

    def encode_sentences(self, texts):
        """Match S05's CLS atom encoder, including whitespace-only separators.

        Retrieval inputs still use DenseEncoder's strict nonempty/window rules.
        Source separators are real semantic atoms and must not be deleted.
        """
        import numpy as np
        output=[]
        for start in range(0,len(texts),self.batch_size):
            batch=self.tokenizer.tokenizer(texts[start:start+self.batch_size],padding=True,truncation=False,return_tensors='pt')
            require(batch['input_ids'].shape[1] <= 512, 'Semantic atom exceeds the complete 512-token budget')
            with self.torch.inference_mode():
                hidden=self.model(**batch).last_hidden_state[:,0]
                output.extend(self.torch.nn.functional.normalize(hidden.float(),p=2,dim=1).numpy())
        return np.asarray(output,dtype='float32'),[1]*len(texts)


class CPUReranker(Reranker):
    def __init__(self, policy, cache_dir, *, threads=4):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch, self.policy = setup(threads), policy
        self.batch_size, self.padded_tokens, self.wave_size = 2, policy['max_pair_tokens'] * 2, 8
        require(all(policy[k] == v for k, v in PINNED_RERANKER.items()), 'Pinned reranker required')
        self.tokenizer = AutoTokenizer.from_pretrained(policy['model_id'], revision=policy['revision'],
                    cache_dir=cache_dir, local_files_only=True, use_fast=True, trust_remote_code=False)
        require(self.tokenizer.is_fast, 'Fast tokenizer required')
        self.tokenizer.model_max_length = 10**12
        self.model = AutoModelForSequenceClassification.from_pretrained(policy['model_id'], revision=policy['revision'],
                    cache_dir=cache_dir, local_files_only=True, trust_remote_code=False,
                    use_safetensors=True, dtype=self.torch.float32).eval().to('cpu')
        count = sum(p.numel() for p in self.model.parameters())
        require(0 < count <= 15_000_000_000 and self.model.config.num_labels == 1, 'Unexpected reranker head/size')
        self.registry = {**PINNED_RERANKER, 'parameters': count, 'device': 'cpu', 'compute_dtype': 'float32',
                         'threads': threads, 'window_policy': policy, 'scores': 'raw uncalibrated logits',
                         'torch': self.torch.__version__}

    def _infer(self, features):
        batch = self.tokenizer.pad(features, padding=True, return_tensors='pt')
        with self.torch.inference_mode():
            return self.model(**batch, return_dict=True).logits.reshape(-1).float().tolist()

    def _safe(self, features):
        return self._infer(features)


def download_models(cache_dir):
    from huggingface_hub import snapshot_download
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    for spec in [*MODELS.values(), PINNED_RERANKER]:
        snapshot = snapshot_download(spec['model_id'], revision=spec['revision'], cache_dir=cache_dir,
            allow_patterns=['config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
                            'special_tokens_map.json','sentencepiece.bpe.model','vocab.txt',weight_file(spec)], max_workers=2)
        require((Path(snapshot) / weight_file(spec)).is_file(), 'Pinned snapshot is missing its actual model weights')
        print('Pinned model downloaded:', spec['model_id'], spec['revision'], flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', default='artifacts/stage10_models')
    args = parser.parse_args()
    download_models(args.cache_dir)
