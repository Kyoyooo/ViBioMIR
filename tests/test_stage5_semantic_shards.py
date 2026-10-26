"""Manual fixture checks for two shards, legacy reuse, resume and full merge."""
from pathlib import Path
import unittest
from unittest.mock import patch

from vibiomir.chunk_batches import batches, read_json, read_rows
from vibiomir.chunk_pipeline import build_chunks, validate_chunks
from vibiomir.semantic_shards import make_plan, merge_shards, read_plan, run_shard
from vibiomir.io import write_json
from test_stage5_chunking import CharacterTokenizer, CONFIG
from test_stage5_semantic_compute import FakePackedEncoder
import test_stage5_pipeline as fixtures


def rows(directory):
    return [row for path, _ in batches(directory) for row in read_rows(path)]


class SemanticShardsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.root = self.fixture.root
        self.plan = self.root / "plan.json"
        self.tokenizer = self.root / "tokenizer"
        self.loader = patch("vibiomir.semantic_shards.load_tokenizer", return_value=(CharacterTokenizer(), "fixture-tokenizer"))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def make(self, seed=None):
        return make_plan(self.fixture.inputs, self.tokenizer, self.plan, config=CONFIG, seed_dir=seed)

    def run_part(self, shard_id, **kwargs):
        return run_shard(self.fixture.inputs, self.tokenizer, self.plan, shard_id,
            self.root / f"shard_{shard_id}", cpu_workers=0, encoder_factory=FakePackedEncoder, **kwargs)

    def reference(self, output, **kwargs):
        return build_chunks(self.fixture.inputs, output, CharacterTokenizer(), "fixture-tokenizer",
            strategy="semantic", config=CONFIG, encoder=FakePackedEncoder(), encoder_registry=FakePackedEncoder.registry, **kwargs)

    def merge(self, seed=None):
        return merge_shards(self.fixture.inputs, self.plan, [self.root / "shard_0", self.root / "shard_1"],
                            self.root / "merged", seed_dir=seed)

    def test_two_ranges_cover_inputs_once_and_merge_matches_reference(self):
        plan = self.make()
        self.assertEqual(plan["ranges"][0][0], 0)
        self.assertEqual(plan["ranges"][0][1], plan["ranges"][1][0])
        self.assertEqual(plan["ranges"][1][1], 3)
        self.run_part(0)
        self.run_part(1)
        result = self.merge()
        reference = self.root / "reference"
        self.reference(reference)
        self.assertEqual(rows(self.root / "merged"), rows(reference))
        self.assertEqual(result["documents"], 3)
        checked = validate_chunks(self.fixture.inputs, self.root / "merged", CharacterTokenizer(), "fixture-tokenizer")
        self.assertEqual(checked["validation"], "passed")

    def test_legacy_semantic_prefix_is_copied_without_recomputing(self):
        seed = self.root / "legacy"
        self.reference(seed, stop_after_batches=1)
        before = (seed / "batch-000000/documents.jsonl.gz").read_bytes()
        plan = self.make(seed)
        self.assertEqual(plan["seed_batches"], 1)
        self.assertEqual(plan["ranges"][0][0], 1)
        self.run_part(0)
        self.run_part(1)
        self.merge(seed)
        self.assertEqual(before, (self.root / "merged/batch-000000/documents.jsonl.gz").read_bytes())

    def test_resume_preserves_old_shard_batch_bytes(self):
        self.make()
        for shard_id in (0, 1):
            self.run_part(shard_id, stop_after_batches=1)
            directory = self.root / f"shard_{shard_id}"
            previous = {p: p.read_bytes() for p in directory.glob("batch-*/documents.jsonl.gz")}
            self.run_part(shard_id)
            self.assertTrue(all(p.read_bytes() == content for p, content in previous.items()))
        self.assertEqual(self.merge()["state"], "complete")

    def test_merge_rejects_incomplete_or_swapped_parts(self):
        self.make()
        self.run_part(0)
        self.run_part(1)
        path = self.root / "shard_1/manifest.json"
        manifest = read_json(path)
        manifest["state"] = "paused"
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.merge()
        manifest["state"] = "complete"
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "another plan"):
            merge_shards(self.fixture.inputs, self.plan, [self.root / "shard_1", self.root / "shard_0"], self.root / "bad")

    def test_plan_edits_and_wrong_shard_resume_are_rejected(self):
        self.make()
        self.run_part(0)
        with self.assertRaisesRegex(ValueError, "another plan or shard"):
            run_shard(self.fixture.inputs, self.tokenizer, self.plan, 1, self.root / "shard_0",
                      cpu_workers=0, encoder_factory=FakePackedEncoder)
        data = read_json(self.plan)
        data["ranges"][0][1] += 1
        write_json(self.plan, data)
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_plan(self.plan)


if __name__ == "__main__":
    unittest.main()
