"""Parallel batch validation must retain every check of the original validator."""
import copy
from concurrent.futures import Future
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_stage5_pipeline as fixtures
from test_stage5_chunking import CharacterTokenizer
from vibiomir.chunk_batches import batches, read_json, read_rows
from vibiomir.chunk_pipeline import validate_chunks
from vibiomir.chunk_validation import aggregate, validate_parallel, validate_single_batch
from vibiomir.io import file_hash, write_json


class InlinePool:
    def __init__(self, *, initializer, initargs, **kwargs):
        initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, function, job):
        future = Future()
        try:
            future.set_result(function(job))
        except Exception as exc:
            future.set_exception(exc)
        return future


class BatchValidationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.fixture.build()

    def jobs(self):
        f = self.fixture
        parent, manifest = read_json(f.inputs / "manifest.json"), read_json(f.output / "manifest.json")
        return [(i, ip, op, parent, manifest, left, right) for i, ((ip, left), (op, right)) in
                enumerate(zip(batches(f.inputs), batches(f.output)))]

    def test_aggregate_equals_original_full_report_and_sources_unchanged(self):
        f = self.fixture
        before = {str(p): p.read_bytes() for p in f.output.rglob("*") if p.is_file()}
        reports = [validate_single_batch(job, CharacterTokenizer(), "fixture-tokenizer")["report"] for job in self.jobs()]
        self.assertEqual(aggregate(reports), validate_chunks(f.inputs, f.output, CharacterTokenizer(), "fixture-tokenizer"))
        for name, content in before.items():
            self.assertEqual(content, Path(name).read_bytes())

    def test_full_runner_and_resume_equal_original_without_rechecking_cached_rows(self):
        f = self.fixture
        reference = validate_chunks(f.inputs, f.output, CharacterTokenizer(), "fixture-tokenizer")
        with patch("vibiomir.chunk_validation.load_tokenizer", return_value=(CharacterTokenizer(), "fixture-tokenizer")), \
             patch("vibiomir.chunk_validation.ProcessPoolExecutor", InlinePool):
            actual = validate_parallel(f.inputs, f.output, f.root / "tokenizer", f.root / "validation", workers=2)
            self.assertEqual(actual, reference)
            with patch("vibiomir.chunk_validation.worker", side_effect=AssertionError("Cached rows recomputed")):
                self.assertEqual(validate_parallel(f.inputs, f.output, f.root / "tokenizer",
                                 f.root / "validation", workers=2, resume=True), reference)
            payload = f.output / "batch-000000/documents.jsonl.gz"
            payload.write_bytes(payload.read_bytes() + b"corruption")
            with self.assertRaisesRegex(ValueError, "checksum"):
                validate_parallel(f.inputs, f.output, f.root / "tokenizer", f.root / "validation", resume=True)

    def test_full_runner_rejects_false_manifest_totals(self):
        f = self.fixture
        manifest = read_json(f.output / "manifest.json")
        manifest["chunks"] += 1
        write_json(f.output / "manifest.json", manifest)
        with patch("vibiomir.chunk_validation.load_tokenizer", return_value=(CharacterTokenizer(), "fixture-tokenizer")), \
             patch("vibiomir.chunk_validation.ProcessPoolExecutor", InlinePool):
            with self.assertRaisesRegex(ValueError, "Global manifest totals"):
                validate_parallel(f.inputs, f.output, f.root / "tokenizer", f.root / "validation")

    def test_wrong_chunk_text_is_rejected_even_with_updated_checksum(self):
        job = self.jobs()[0]
        directory = job[2]
        rows = list(read_rows(directory))
        rows[0]["chunks"][0]["chunk_text"] += "corruption"
        with gzip.open(directory / "documents.jsonl.gz", "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        descriptor = read_json(directory / "batch.json")
        descriptor["sha256"] = file_hash(directory / "documents.jsonl.gz")
        write_json(directory / "batch.json", descriptor)
        with self.assertRaisesRegex(ValueError, "source text/version/offset"):
            validate_single_batch(self.jobs()[0], CharacterTokenizer(), "fixture-tokenizer")

    def test_removed_chunks_are_rejected_by_source_coverage(self):
        job = self.jobs()[0]
        directory = job[2]
        rows = list(read_rows(directory))
        rows[0]["chunks"] = []
        with gzip.open(directory / "documents.jsonl.gz", "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        descriptor = read_json(directory / "batch.json")
        descriptor.update(sha256=file_hash(directory / "documents.jsonl.gz"), chunks=0)
        write_json(directory / "batch.json", descriptor)
        with self.assertRaisesRegex(ValueError, "Missing tail"):
            validate_single_batch(self.jobs()[0], CharacterTokenizer(), "fixture-tokenizer")

    def test_incompatible_contracts_cannot_be_aggregated(self):
        report = validate_single_batch(self.jobs()[0], CharacterTokenizer(), "fixture-tokenizer")["report"]
        changed = copy.deepcopy(report)
        changed["stage4_source"] = {"wrong": "source"}
        with self.assertRaisesRegex(ValueError, "contracts"):
            aggregate([report, changed])


if __name__ == "__main__":
    unittest.main()
