"""Manual CPU fixtures for packed semantic replay, budgets and OOM recovery."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from vibiomir.chunk_models import MODEL_ID, MODEL_REVISION
from vibiomir.chunk_models import load_tokenizer
from vibiomir.chunk_parallel import CachedTokenizer
from vibiomir.chunking import chunk_document
from vibiomir.semantic_compute import (PackedCUDAEncoder, ReplayEncoder, padded_microbatches,
    prepare_document, render_wave, waves)
from test_stage5_chunking import CharacterTokenizer, CONFIG, packet


class FakePackedEncoder:
    dimension = 2
    registry = {"model_id": MODEL_ID, "revision": MODEL_REVISION, "pooling": "CLS + L2",
                "dtype": "float16", "parameters": 100}

    def __init__(self, tokenizer=None, **kwargs):
        self.stats = {}

    @staticmethod
    def vector(text):
        return [1., 0.] if sum(map(ord, text)) % 2 else [0., 1.]

    def encode_into(self, texts, destination):
        for index, text in enumerate(texts):
            destination[index] = self.vector(text)

    def __call__(self, texts):
        return [self.vector(text) for text in texts]


class SemanticComputeTest(unittest.TestCase):
    def test_packed_replay_matches_original_multilingual_chunking(self):
        tokenizer, encoder = CharacterTokenizer(), FakePackedEncoder()
        sources = [packet("Không sốt. 肾结石😀。 No fever; 5 mg.\n" * 20, doc_id=i) for i in (1, 2)]
        prepared = [prepare_document(source, CachedTokenizer(tokenizer), CONFIG) for source in sources]
        with tempfile.TemporaryDirectory() as scratch:
            actual = list(render_wave(prepared, encoder, CachedTokenizer(tokenizer), CONFIG, "same", scratch))
        expected = [chunk_document(source, tokenizer, CONFIG, "semantic", "same", encoder) for source in sources]
        self.assertEqual(actual, expected)

    def test_no_embedding_requests_for_short_sections(self):
        source = packet("No fever.")
        plan = prepare_document(source, CharacterTokenizer(), CONFIG)
        self.assertEqual(plan["sentences"], 0)
        with tempfile.TemporaryDirectory() as scratch:
            result = list(render_wave([plan], FakePackedEncoder(), CachedTokenizer(CharacterTokenizer()), CONFIG, "x", scratch))
        self.assertEqual(len(result[0]["chunks"]), 1)

    def test_wave_budget_and_oversized_document_are_explicit(self):
        plans = [{"sentences": n, "source_characters": 10} for n in (3, 3, 50, 2)]
        grouped = list(waves(plans, max_documents=2, max_sentences=8))
        self.assertEqual([sum(p["sentences"] for p in group) for group in grouped], [6, 50, 2])
        self.assertEqual(len(grouped[1]), 1)

    def test_microbatches_obey_padded_budget_and_preserve_every_index(self):
        lengths = [512, 10, 500, 20, 200, 32]
        batches = list(padded_microbatches(lengths, 3, 1024))
        self.assertEqual(sorted(i for group in batches for i in group), list(range(len(lengths))))
        for group in batches:
            self.assertLessEqual(len(group), 3)
            self.assertLessEqual(max(lengths[i] for i in group) * len(group), 1024)
        with self.assertRaisesRegex(ValueError, "truncation"):
            list(padded_microbatches([513], 4, 8192))

    def test_oom_splitting_retains_vector_to_sentence_mapping(self):
        class FakeOOM(Exception):
            pass
        encoder = object.__new__(PackedCUDAEncoder)
        encoder.torch = SimpleNamespace(OutOfMemoryError=FakeOOM, cuda=SimpleNamespace(empty_cache=lambda: None))
        encoder.max_batch_size = 8
        encoder.stats = dict(sentences=0, gpu_batches=0, oom_splits=0, real_tokens=0, padded_tokens=0)
        def infer(features):
            if len(features) > 2:
                raise FakeOOM()
            return np.asarray([[f["input_ids"][0], 0.] for f in features], dtype="float32")
        encoder._infer = infer
        encoded = {"input_ids": [[i + 1] * n for i, n in enumerate((8, 3, 9, 2, 1, 5))]}
        output = np.zeros((6, 2), dtype="float32")
        encoder._safe_infer([4, 3, 1, 5, 0, 2], encoded, output, 0)
        self.assertEqual(output[:, 0].tolist(), [1., 2., 3., 4., 5., 6.])
        self.assertEqual(encoder.stats["sentences"], 6)
        self.assertGreater(encoder.stats["oom_splits"], 0)

    def test_replay_refuses_mismatched_atom_order(self):
        replay = ReplayEncoder([["original"]], np.zeros((1, 2)), 0)
        with self.assertRaisesRegex(ValueError, "differ"):
            replay(["changed"])

    def test_notebook_has_distinct_shard_paths_and_no_outputs(self):
        path = Path(__file__).resolve().parents[1] / "colab/stage5_semantic_two_shards.ipynb"
        notebook = json.loads(path.read_text())
        code = ""
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                code += "".join(cell["source"])
        self.assertIn("SHARD_ID = 0", code)
        self.assertIn("f'shard_{SHARD_ID}'", code)
        self.assertIn("MAX_PADDED_TOKENS = 8192", code)


@unittest.skipUnless(os.environ.get("VIBIOMIR_RUN_CUDA_TESTS") == "1", "Explicit opt-in required for real CUDA/weights")
class RealCUDAEncodingTest(unittest.TestCase):
    def test_packed_sentence_order_matches_original_encoder(self):
        tokenizer, _ = load_tokenizer(Path(os.environ["VIBIOMIR_TOKENIZER_DIR"]))
        encoder = PackedCUDAEncoder(tokenizer, max_batch_size=8, max_padded_tokens=2048)
        texts = ["Không sốt, không tự dùng kháng sinh.", "肾结石的诊断和治疗。", "No fever; dose 5 mg.",
                 "This longer clinical description contains several original observations. " * 4]
        reference = np.asarray(encoder.base(texts), dtype="float32")
        actual = np.zeros((len(texts), encoder.dimension), dtype="float32")
        encoder.encode_into(iter(texts), actual)
        self.assertTrue(np.isfinite(actual).all())
        self.assertTrue(np.all(np.sum(actual * reference, axis=1) > 0.999))


if __name__ == "__main__":
    unittest.main()
