"""Offline backup/restore and notebook/package contract tests, run manually."""
import json
from pathlib import Path
import tempfile
import unittest

from vibiomir.chunk_batches import commit_batch, finish, output_store, read_rows
from vibiomir.chunk_colab import restore_checkpoint, save_checkpoint

ROOT = Path(__file__).resolve().parents[1]


class ColabHandoffTest(unittest.TestCase):
    def test_committed_backup_restore_and_checksum_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local, backup, restored = root / "local", root / "drive", root / "restored"
            with output_store(local, {"fixture": True}) as (directory, manifest, committed):
                batch = commit_batch(directory, manifest["signature"], 0, [{"doc_id": 1, "chunks": []}])
                committed.append(batch)
                save_checkpoint(local, backup, batch[0])
                finish(directory, manifest, committed, True)
            save_checkpoint(local, backup)
            restore_checkpoint(backup, restored)
            self.assertEqual(list(read_rows(restored / "batch-000000")), [{"doc_id": 1, "chunks": []}])
            self.assertEqual(json.loads((restored / "manifest.json").read_text())["state"], "complete")
            with (backup / "batch-000000/documents.jsonl.gz").open("ab") as stream:
                stream.write(b"corrupted")
            with self.assertRaisesRegex(ValueError, "checksum"):
                restore_checkpoint(backup, root / "bad-restore")

    def test_notebook_has_no_execution_or_outputs_and_uses_shared_implementation(self):
        notebook = json.loads((ROOT / "colab/stage5_semantic.ipynb").read_text(encoding="utf-8"))
        code = []
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
                code.extend(cell["source"])
        source = "".join(code)
        self.assertIn("from vibiomir.chunk_pipeline import", source)
        self.assertIn("on_batch=", source)
        self.assertIn("restore_checkpoint", source)
        for path in ("requirements-stage5.txt", "requirements-stage5-colab.txt", "configs/chunking_stage5.json"):
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
