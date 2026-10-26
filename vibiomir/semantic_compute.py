"""Bounded CPU preparation and length-bucketed CUDA embeddings for semantic chunks.

The original chunker is called twice: first to collect its exact embedding
requests, then to replay those requests using real vectors. Evidence and
semantic boundary rules remain in the original chunker, not a second algorithm.
"""
from collections import OrderedDict, deque
from itertools import islice
import os
from pathlib import Path
import shutil
import tempfile

from .chunk_models import load_tokenizer, SentenceEncoder
from .chunk_parallel import CachedTokenizer
from .chunking import chunk_document


def prepare_document(packet, tokenizer, config):
    groups = []
    def collect(texts):
        groups.append(texts)
        # Boundary decisions do not affect which requests the chunker makes.
        return [[1.0]] * len(texts)
    if isinstance(tokenizer, CachedTokenizer):
        tokenizer.clear()
    chunk_document(packet, tokenizer, config, "semantic", "planning-only", collect)
    return {"packet": packet, "groups": groups,
            "sentences": sum(map(len, groups)),
            "source_characters": sum(len(r["text"]) for r in packet["regions"])}


_TOKENIZER = None
_CONFIG = None


def initialize_preparer(tokenizer_dir, config):
    global _TOKENIZER, _CONFIG
    for name in ("USE_TORCH", "USE_TF", "USE_FLAX"):
        os.environ[name] = "0"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "1"
    tokenizer, _ = load_tokenizer(tokenizer_dir)
    _TOKENIZER, _CONFIG = CachedTokenizer(tokenizer), config


def prepare_in_worker(packet):
    return prepare_document(packet, _TOKENIZER, _CONFIG)


def ordered_preparation(packets, pool=None, *, tokenizer=None, config=None, prefetch=4):
    """At most prefetch CPU documents ahead, even on Python before map(buffersize)."""
    if pool is None:
        for packet in packets:
            yield prepare_document(packet, tokenizer, config)
        return
    packets = iter(packets)
    queue = deque()
    for packet in islice(packets, prefetch):
        queue.append(pool.submit(prepare_in_worker, packet))
    while queue:
        current = queue.popleft().result()
        try:
            packet = next(packets)
        except StopIteration:
            pass
        else:
            queue.append(pool.submit(prepare_in_worker, packet))
        yield current


def waves(plans, *, max_documents=16, max_sentences=1024, max_characters=1_000_000):
    """An oversized single document runs alone, with disk-backed vectors."""
    wave, sentences, characters = [], 0, 0
    for plan in plans:
        if wave and (len(wave) >= max_documents or sentences + plan["sentences"] > max_sentences
                     or characters + plan["source_characters"] > max_characters):
            yield wave
            wave, sentences, characters = [], 0, 0
        wave.append(plan)
        sentences += plan["sentences"]
        characters += plan["source_characters"]
    if wave:
        yield wave


def padded_microbatches(lengths, max_batch_size, max_padded_tokens):
    """Group indices in length order without exceeding padded batch cost."""
    batch = []
    for index in sorted(range(len(lengths)), key=lambda i: lengths[i]):
        if lengths[index] > 512:
            raise ValueError("Semantic atom exceeds 512 tokens; truncation is forbidden")
        if batch and (len(batch) >= max_batch_size or (len(batch) + 1) * lengths[index] > max_padded_tokens):
            yield batch
            batch = []
        batch.append(index)
    if batch:
        yield batch


class PackedCUDAEncoder:
    """One FP16 model per GPU; token-budgeted batches and OOM split/retry."""
    def __init__(self, tokenizer, *, max_batch_size=64, max_padded_tokens=8192,
                 tokenization_window=1024):
        if min(max_batch_size, tokenization_window) < 1 or max_padded_tokens < 512:
            raise ValueError("Require positive batch/window sizes and max_padded_tokens >= 512")
        self.base = SentenceEncoder(tokenizer, batch_size=max_batch_size)
        self.tokenizer, self.torch = tokenizer, self.base.torch
        self.max_batch_size, self.max_padded_tokens = max_batch_size, max_padded_tokens
        self.tokenization_window = tokenization_window
        self.dimension = int(self.base.model.config.hidden_size)
        self.registry = self.base.registry
        self.stats = {"sentences": 0, "gpu_batches": 0, "oom_splits": 0,
                      "real_tokens": 0, "padded_tokens": 0}

    def _infer(self, features):
        torch = self.torch
        inputs = self.tokenizer.pad(features, padding=True, return_tensors="pt")
        inputs = {key: value.to("cuda") for key, value in inputs.items()}
        with torch.inference_mode():
            result = self.base.model(**inputs).last_hidden_state[:, 0]
            result = torch.nn.functional.normalize(result.float(), p=2, dim=1)
            return result.cpu().numpy()

    def _safe_infer(self, indices, encoded, destination, base_index):
        if len(indices) > self.max_batch_size:
            step = self.max_batch_size
            for start in range(0, len(indices), step):
                self._safe_infer(indices[start:start + step], encoded, destination, base_index)
            return
        features = [{key: encoded[key][index] for key in encoded} for index in indices]
        try:
            result = self._infer(features)
        except self.torch.OutOfMemoryError:
            # Leave this handler before retrying: its traceback can retain CUDA tensors.
            result = None
        if result is None:
            self.stats["oom_splits"] += 1
            self.torch.cuda.empty_cache()
            if len(indices) == 1:
                raise ValueError("CUDA cannot encode one atom. Stop other GPU jobs or choose a larger GPU; evidence was not truncated.")
            self.max_batch_size = min(self.max_batch_size, max(1, len(indices) // 2))
            middle = len(indices) // 2
            self._safe_infer(indices[:middle], encoded, destination, base_index)
            self._safe_infer(indices[middle:], encoded, destination, base_index)
            return
        destination[[base_index + i for i in indices]] = result
        lengths = [len(encoded["input_ids"][i]) for i in indices]
        self.stats["sentences"] += len(indices)
        self.stats["gpu_batches"] += 1
        self.stats["real_tokens"] += sum(lengths)
        self.stats["padded_tokens"] += max(lengths) * len(lengths)

    def encode_into(self, texts, destination):
        texts = iter(texts)
        offset = 0
        while True:
            window = list(islice(texts, self.tokenization_window))
            if not window:
                break
            encoded = self.tokenizer(window, padding=False, truncation=False,
                                     return_attention_mask=True)
            lengths = [len(ids) for ids in encoded["input_ids"]]
            for indices in padded_microbatches(lengths, self.max_batch_size, self.max_padded_tokens):
                self._safe_infer(indices, encoded, destination, offset)
            offset += len(window)
        if offset != len(destination):
            raise ValueError("Embedding accounting mismatch")


class VectorWindow:
    """Expose original Python-float vectors while retaining only two decoded rows."""
    def __init__(self, matrix, offset, size):
        self.matrix, self.offset, self.size = matrix, offset, size
        self.cache = OrderedDict()

    def __len__(self):
        return self.size

    def __getitem__(self, index):
        if not 0 <= index < self.size:
            raise IndexError(index)
        if index not in self.cache:
            self.cache[index] = self.matrix[self.offset + index].tolist()
            if len(self.cache) > 2:
                self.cache.popitem(last=False)
        return self.cache[index]


class ReplayEncoder:
    def __init__(self, groups, matrix, offset):
        self.groups, self.matrix, self.offset = groups, matrix, offset
        self.current = 0

    def __call__(self, texts):
        if self.current >= len(self.groups) or texts != self.groups[self.current]:
            raise ValueError("Prepared/replayed semantic atoms differ; refusing incorrect embeddings")
        result = VectorWindow(self.matrix, self.offset, len(texts))
        self.offset += len(texts)
        self.current += 1
        return result


def render_wave(plans, encoder, tokenizer, config, signature, scratch_dir):
    """GPU vectors spill to local disk; never retain corpus-sized vector lists."""
    import numpy as np
    count = sum(plan["sentences"] for plan in plans)
    if count and shutil.disk_usage(scratch_dir).free < count * encoder.dimension * 4 + 256 * 1024**2:
        raise ValueError("Insufficient local scratch disk for semantic vectors; free disk space and resume")
    with tempfile.TemporaryDirectory(prefix="semantic-wave-", dir=scratch_dir) as directory:
        matrix = np.memmap(Path(directory) / "vectors.bin", dtype="float32", mode="w+",
                           shape=(count, encoder.dimension)) if count else None
        try:
            if count:
                texts = (text for plan in plans for group in plan["groups"] for text in group)
                encoder.encode_into(texts, matrix)
            offset = 0
            for plan in plans:
                tokenizer.clear()
                replay = ReplayEncoder(plan["groups"], matrix, offset)
                row = chunk_document(plan["packet"], tokenizer, config, "semantic", signature, replay)
                if replay.current != len(plan["groups"]):
                    raise ValueError("Unused semantic requests after replay")
                offset = replay.offset
                yield row
        finally:
            if matrix is not None:
                matrix.flush()
                del matrix
