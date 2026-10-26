"""Manual full handoff contract checks; no GPU, network or original corpus."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from vibiomir.chunk_full_bundle import make_full_bundle
from vibiomir.io import write_json, writer_lock
from test_stage5_chunking import CharacterTokenizer, CONFIG
import test_stage5_pipeline as fixtures

ROOT = Path(__file__).resolve().parents[1]


class FullBundleTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.Stage5PipelineTest("test_prepare_resume_build_resume_and_zero_chunk_accounting")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.fixture.build(stop_after_batches=1)
        self.tokenizer = self.fixture.root / "tokenizer"
        self.tokenizer.mkdir()
        write_json(self.tokenizer / "REGISTRY.json", {"fixture": True})
        self.config = self.fixture.root / "chunking.json"
        write_json(self.config, CONFIG)
        self.patch = patch("vibiomir.chunk_full_bundle.load_tokenizer", return_value=(CharacterTokenizer(), "fixture-tokenizer"))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def bundle(self, **kwargs):
        return make_full_bundle(self.fixture.inputs, self.tokenizer, self.fixture.root / "full.zip",
            checkpoint_dir=self.fixture.output, config_path=self.config, **kwargs)

    def test_partial_checkpoint_and_full_inputs_are_included_with_code(self):
        result = self.bundle()
        self.assertEqual(result["reused_checkpoint_batches"], 1)
        with zipfile.ZipFile(self.fixture.root / "full.zip") as archive:
            names = set(archive.namelist())
            self.assertIn("checkpoint/batch-000000/documents.jsonl.gz", names)
            self.assertIn("inputs/batch-000002/documents.jsonl.gz", names)
            self.assertIn("vibiomir/chunk_parallel.py", names)
            self.assertIn("tests/test_stage5_parallel.py", names)
            self.assertFalse(any(n.startswith("Data/") or n.endswith("sqlite3") for n in names))
            self.assertNotIn("checkpoint/.store.lock", names)

    def test_live_writer_is_rejected_before_creating_bundle(self):
        with writer_lock(self.fixture.output):
            with self.assertRaisesRegex(ValueError, "still running"):
                self.bundle()
        self.assertFalse((self.fixture.root / "full.zip").exists())

    def test_config_mismatch_is_rejected_without_editing_checkpoint(self):
        write_json(self.config, {**CONFIG, "target_tokens": 41})
        with self.assertRaisesRegex(ValueError, "differs"):
            self.bundle()

    def test_full_notebook_is_unexecuted_and_uses_importable_runner(self):
        notebook = json.loads((ROOT / "colab/stage5_full.ipynb").read_text())
        source = []
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                source.extend(cell["source"])
        joined = "".join(source)
        self.assertIn("vibiomir.chunk_parallel", joined)
        self.assertIn("--seed-checkpoint-dir", joined)
        self.assertIn("STOP_AFTER_BATCHES = 2", joined)
        self.assertIn("validate-chunks", joined)


if __name__ == "__main__":
    unittest.main()
