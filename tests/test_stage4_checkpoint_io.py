"""Check the optional WAL execution profile against the existing resume contract."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from scripts.run_reviewed_stage4_apply import wal_checkpoints, wal_database
from vibiomir import cleaning
from vibiomir.validate_cleaning import validate_cleaned_documents, iter_export
from vibiomir.io import write_json
import test_stage4_pipeline as pipeline_fixtures
from test_stage3_documents import record


class CheckpointIOTest(unittest.TestCase):
    def test_local_connection_uses_atomic_wal_with_normal_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.sqlite3"
            with closing(wal_database(path)) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 1)
                with db:
                    db.execute("INSERT INTO jobs VALUES (1,'complete',NULL,'{}')")
            with closing(wal_database(path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_delete_mode_checkpoint_resumes_under_wal_and_validates(self):
        fixture = pipeline_fixtures.CleaningPipelineTest(methodName="test_apply_uses_separate_reviewed_profile_and_never_marks_final")
        fixture.setUp()
        try:
            data, parent = fixture.source([record(1), record(2), record(3)])
            import json
            config = json.loads(fixture.profile.read_text())
            for rule in config["rules"]:
                rule["review_status"] = "approved"
            write_json(fixture.profile, config)
            original_factory = cleaning.database
            output, paused = fixture.run_clean(data, parent, mode="apply", stop_after_documents=1)
            self.assertEqual(paused["state"], "paused")
            with wal_checkpoints():
                _, completed = fixture.run_clean(data, parent, mode="apply", resume=True)
            self.assertIs(cleaning.database, original_factory)
            self.assertEqual(completed["state"], "complete_provisional")
            self.assertEqual(completed["stats"]["documents_complete"], 3)
            self.assertEqual(validate_cleaned_documents(output, parent, data)["validation"], "passed")
            selections = list(iter_export(output, completed, "document_selection"))
            self.assertEqual({r["doc_id"] for r in selections}, {1, 2, 3})
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
