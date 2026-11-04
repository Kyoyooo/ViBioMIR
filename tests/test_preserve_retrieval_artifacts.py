"""Ensure emergency snapshots preserve a local index without changing the source."""
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

path = Path(__file__).resolve().parents[1]/'colab/stage9_preserve_retrieval_artifacts_cell.py'
spec = importlib.util.spec_from_file_location('preserve_cell', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreserveTest(unittest.TestCase):
    def test_sqlite_backup_and_wrong_plan_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source.sqlite3'
            target = Path(temp)/'backup.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
                db.execute("INSERT INTO metadata VALUES('plan','identity')")
                db.execute('CREATE TABLE batches(id INTEGER)')
                db.executemany('INSERT INTO batches VALUES(?)', [(0,), (1,)])
                db.execute('CREATE VIRTUAL TABLE passages USING fts5(text)')
                db.execute("INSERT INTO passages VALUES('original evidence')")
                db.commit()
            before = source.read_bytes()
            report = module.backup_bm25(source, target, dict(signature='identity', batch_indices=[0, 1, 2]))
            self.assertEqual(report['indexed_batches'], 2)
            self.assertEqual(source.read_bytes(), before)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute("SELECT text FROM passages WHERE passages MATCH 'evidence'").fetchone()[0], 'original evidence')
            with self.assertRaisesRegex(ValueError, 'another plan'):
                module.backup_bm25(source, Path(temp)/'wrong.sqlite3', dict(signature='wrong', batch_indices=[0, 1]))
            with self.assertRaisesRegex(ValueError, 'new backup'):
                module.backup_bm25(source, target, dict(signature='identity', batch_indices=[0, 1]))


if __name__ == '__main__':
    unittest.main()
