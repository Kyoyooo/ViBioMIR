"""Manual offline equivalence/resume tests; optional real spawned workers."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from vibiomir.chunk_batches import batches, read_rows
from vibiomir.chunk_models import load_tokenizer
from vibiomir.chunk_parallel import CachedTokenizer, build_parallel, restore_best_checkpoint
from vibiomir.chunk_pipeline import build_chunks
from vibiomir.chunking import chunk_document
from vibiomir.chunk_colab import save_checkpoint
from test_stage5_chunking import CharacterTokenizer, CONFIG, packet
import test_stage5_pipeline as fixtures


class CountingTokenizer(CharacterTokenizer):
    def __init__(self):
        self.encode_calls = self.offset_calls = 0

    def encode(self, *args, **kwargs):
        self.encode_calls += 1
        return super().encode(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        self.offset_calls += 1
        return super().__call__(*args, **kwargs)


def rows(directory):
    return [row for path, _ in batches(directory) for row in read_rows(path)]


class InlinePool:
    """Exercise ordered scheduling/initialization without claiming OS concurrency."""
    def __init__(self, max_workers, mp_context, initializer, initargs):
        initializer(*initargs)

    def map(self, function, packets, chunksize):
        return map(function, packets)

    def shutdown(self, **kwargs):
        pass


class ParallelChunkingTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.loader = patch("vibiomir.chunk_parallel.load_tokenizer", return_value=(CharacterTokenizer(), "fixture-tokenizer"))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def parallel(self, output=None, **kwargs):
        return build_parallel(self.fixture.inputs, output or self.fixture.output,
            self.fixture.root / "tokenizer", config=CONFIG, **kwargs)

    def test_cache_preserves_chunk_ids_text_budgets_and_reduces_calls(self):
        source = packet("Không sốt. 原文。 No fever; dose 5 mg.\n" * 30)
        for strategy in ("fixed", "structure"):
            with self.subTest(strategy=strategy):
                original, cached_base = CountingTokenizer(), CountingTokenizer()
                reference = chunk_document(source, original, CONFIG, strategy, "same-identity")
                actual = chunk_document(source, CachedTokenizer(cached_base), CONFIG, strategy, "same-identity")
                self.assertEqual(reference, actual)
                self.assertLess(cached_base.encode_calls, original.encode_calls)

    def test_cache_limits_and_clear(self):
        cached = CachedTokenizer(CountingTokenizer(), max_entries=2, max_characters=4)
        for text in ("ab", "cd", "ef", "abcdef"):
            self.assertEqual(cached.encode(text), CharacterTokenizer().encode(text))
            self.assertLessEqual(cached.characters, 4)
            self.assertLessEqual(len(cached.cache), 2)
        cached.clear()
        self.assertEqual(cached.characters, 0)
        self.assertFalse(cached.cache)

    def test_resume_existing_legacy_batch_without_rewriting_it(self):
        self.fixture.build(stop_after_batches=1)
        before = (self.fixture.output / "batch-000000/documents.jsonl.gz").read_bytes()
        result = self.parallel(resume=True, workers=1)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(before, (self.fixture.output / "batch-000000/documents.jsonl.gz").read_bytes())
        other = self.fixture.root / "reference"
        build_chunks(self.fixture.inputs, other, CharacterTokenizer(), "fixture-tokenizer", config=CONFIG)
        self.assertEqual(rows(other), rows(self.fixture.output))

    def test_ordered_worker_schedule_matches_serial_and_supports_resume(self):
        with patch("vibiomir.chunk_parallel.ProcessPoolExecutor", InlinePool):
            self.assertEqual(self.parallel(workers=2, stop_after_batches=1)["state"], "paused")
            self.assertEqual(self.parallel(workers=2, resume=True)["state"], "complete")
        other = self.fixture.root / "serial"
        self.parallel(other, workers=1)
        self.assertEqual(rows(other), rows(self.fixture.output))

    def test_restore_prefers_newer_backup_and_rejects_strategy_change(self):
        self.fixture.build(stop_after_batches=1)
        seed = self.fixture.root / "seed"
        save_checkpoint(self.fixture.output, seed)
        self.fixture.build(resume=True)
        backup = self.fixture.root / "backup"
        save_checkpoint(self.fixture.output, backup)
        restored = self.fixture.root / "restored"
        restore_best_checkpoint(restored, backup, seed)
        self.assertEqual(rows(restored), rows(self.fixture.output))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.parallel(resume=True, strategy="fixed")

    def test_failed_worker_does_not_commit_partial_document_batch(self):
        self.fixture.build(stop_after_batches=1)
        before = (self.fixture.output / "batch-000000/documents.jsonl.gz").read_bytes()
        class FailedPool(InlinePool):
            def map(self, *args, **kwargs):
                raise RuntimeError("simulated worker exit")
        with patch("vibiomir.chunk_parallel.ProcessPoolExecutor", FailedPool):
            with self.assertRaisesRegex(RuntimeError, "worker exit"):
                self.parallel(workers=2, resume=True)
        self.assertFalse((self.fixture.output / "batch-000001").exists())
        self.assertEqual(before, (self.fixture.output / "batch-000000/documents.jsonl.gz").read_bytes())
        self.assertEqual(self.parallel(workers=1, resume=True)["state"], "complete")

    def test_invalid_workers_and_missing_or_changed_checkpoint_fail(self):
        with self.assertRaisesRegex(ValueError, "workers"):
            self.parallel(workers=0)
        self.parallel(workers=1)
        with self.assertRaisesRegex(ValueError, "--resume"):
            self.parallel(workers=1)


@unittest.skipUnless(os.environ.get("VIBIOMIR_TOKENIZER_DIR"), "Real spawn check needs the manually downloaded tokenizer")
class RealParallelWorkersTest(unittest.TestCase):
    def test_cached_real_tokenizer_keeps_multilingual_offsets_and_ids(self):
        tokenizer, _ = load_tokenizer(Path(os.environ["VIBIOMIR_TOKENIZER_DIR"]))
        source = packet("Question: Tiếng Việt?\nKhông sốt; 5 mg. 肾结石😀。No fever &amp; no pain.\n" * 30)
        for strategy in ("fixed", "structure"):
            with self.subTest(strategy=strategy):
                reference = chunk_document(source, tokenizer, CONFIG, strategy, "same-identity")
                actual = chunk_document(source, CachedTokenizer(tokenizer), CONFIG, strategy, "same-identity")
                self.assertEqual(reference, actual)

    def test_two_real_processes_match_legacy_output(self):
        fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        directory = Path(os.environ["VIBIOMIR_TOKENIZER_DIR"])
        tokenizer, signature = load_tokenizer(directory)
        build_chunks(fixture.inputs, fixture.output, tokenizer, signature, config=CONFIG)
        parallel = fixture.root / "parallel"
        build_parallel(fixture.inputs, parallel, directory, workers=2, config=CONFIG)
        self.assertEqual(rows(fixture.output), rows(parallel))


if __name__ == "__main__":
    unittest.main()
