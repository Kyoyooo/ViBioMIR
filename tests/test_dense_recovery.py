"""Offline tests for ZIP restoration and candidate-to-text verification."""
import ast
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.stage9_dense_recovery import (
    adapt_candidates, canonical, digest, file_hash, inspect_bundles, read,
    restore_chunks, signed, write,
)


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archives = self.root / 'bundles'
        self.archives.mkdir()
        self.chunks = self.root / 'restored'
        identity = dict(operation='build-chunks', strategy='structure')
        self.source_signature = digest(canonical(identity).encode())
        manifest = dict(identity=identity, signature=self.source_signature, state='complete',
                        documents=2, chunks=2, batches=2)
        validation = dict(validation='passed', documents=2, chunks=2)
        self.metadata = {'manifest.json': canonical(manifest).encode(), 'VALIDATION.json': canonical(validation).encode()}
        self.expected = dict(signature=self.source_signature, documents=2, chunks=2, batches=2,
                             manifest_sha256=digest(self.metadata['manifest.json']),
                             validation_sha256=digest(self.metadata['VALIDATION.json']))
        self.parts, self.payloads, self.items = [], [], []
        for i in range(2):
            text = f'original evidence {i}'
            chunk = dict(chunk_id=f'chunk-{i}', doc_id=i+1, derived_version_id=f'v{i}',
                         chunk_text=text, retrieval_text=text, text_sha256=digest(text.encode()))
            document = dict(doc_id=i+1, derived_version_id=f'v{i}', chunks=[chunk])
            data = gzip.compress((canonical(document)+'\n').encode())
            info = dict(index=i, signature=self.source_signature, chunks=1, documents=1,
                        sha256=digest(data), first_doc_id=i+1, last_doc_id=i+1)
            info_bytes = canonical(info).encode()
            self.parts.append(dict(index=i, rows=1, documents=1, start=i,
                                   source_sha256=digest(data), batch_json_sha256=digest(info_bytes)))
            self.payloads.append((info_bytes, data))
            self.items.append(dict(chunk_id=chunk['chunk_id'], doc_id=i+1, derived_version_id=f'v{i}',
                                   source_batch=i, source_row=0, chunk_order=0,
                                   retrieval_text_sha256=digest(text.encode()), score=1-i*.1))
        self.plan = signed(dict(source=self.expected, parts=self.parts))
        self.make_archives()
        self.query_path = self.root / 'queries.json'
        self.queries = [dict(id=1, query='original query')]
        write(self.query_path, self.queries)
        self.results = self.root / 'results'
        self.make_candidates()

    def make_archives(self, corrupt=False):
        for i in range(2):
            with zipfile.ZipFile(self.archives / f'worker_{i}.zip', 'w') as z:
                z.writestr('plan.json', canonical(self.plan))
                for name, data in self.metadata.items():
                    z.writestr('chunks/'+name, data)
                info, data = self.payloads[i]
                z.writestr(f'chunks/batch-{i:06d}/batch.json', info)
                z.writestr(f'chunks/batch-{i:06d}/documents.jsonl.gz', b'bad' if corrupt and i==1 else data)

    def make_candidates(self, wrong_text=False):
        identity = dict(operation='dense-results', plan_signature=self.plan['signature'],
                        query_sha256=file_hash(self.query_path))
        signature = digest(canonical(identity).encode())
        write(self.results / 'manifest.json', dict(identity=identity, signature=signature,
              state='complete', corpus_complete=True, validation='not_run'))
        items = [dict(x) for x in self.items]
        if wrong_text:
            items[0]['retrieval_text_sha256'] = 'wrong'
        write(self.results / 'candidates/query-1.json', signed(dict(id=1, query='original query',
              run_signature=signature, candidates=items)))

    def restore(self):
        return restore_chunks(self.archives, self.chunks, self.expected)

    def adapt(self, output='adapted'):
        return adapt_candidates(self.results, self.chunks, self.plan, self.expected,
                                self.query_path, self.queries, self.root / output)

    def test_restore_resume_and_adapter(self):
        self.restore()
        path = self.chunks / 'batch-000000/documents.jsonl.gz'
        stamp = path.stat().st_mtime_ns
        self.restore()
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.adapt()['validation'], 'passed')
        self.assertEqual(self.adapt()['candidates'], 2)
        adapted = read(self.root / 'adapted/query-1.json')
        self.assertEqual(adapted['candidates'], self.items)
        self.assertNotEqual(adapted['run_signature'], read(self.results / 'manifest.json')['signature'])

    def test_missing_bundle_and_wrong_snapshot(self):
        with self.assertRaises(ValueError):
            inspect_bundles(self.archives, {**self.expected, 'chunks': 99})
        (self.archives / 'worker_1.zip').unlink()
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.chunks.exists())

    def test_corrupt_payload_not_committed(self):
        self.make_archives(corrupt=True)
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse((self.chunks / 'batch-000001/documents.jsonl.gz').exists())
        self.assertFalse((self.chunks / 'VALIDATION.json').exists())

    def test_signed_but_wrong_mapping_rejected(self):
        self.restore()
        self.make_candidates(wrong_text=True)
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertFalse((self.root / 'adapted/VALIDATION.json').exists())

    def test_notebook_syntax_embedded_source_and_order(self):
        root = Path(__file__).resolve().parents[1]
        notebook = read(root / 'colab/stage9_dense_recovery_colab.ipynb')
        ids = [c['id'] for c in notebook['cells']]
        self.assertLess(ids.index('preflight'), ids.index('restore'))
        self.assertLess(ids.index('adapt'), ids.index('gpu'))
        for cell in notebook['cells']:
            if cell['cell_type'] != 'code':
                continue
            tree = ast.parse(''.join(cell['source']))
            if cell['id'] == 'helpers':
                assignment = tree.body[0]
                value = ast.literal_eval(assignment.value)
                self.assertEqual(value, (root / 'scripts/stage9_dense_recovery.py').read_text(encoding='utf-8'))
                ast.parse(value)


if __name__ == '__main__':
    unittest.main()
