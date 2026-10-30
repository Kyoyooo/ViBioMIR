"""Offline notebook selection/export checks; no Drive, model or corpus required."""
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / 'colab/stage9_colab_pro_submission.ipynb'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path):
    return digest(Path(path).read_bytes())


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.nb = json.loads(NOTEBOOK.read_text(encoding='utf-8'))

    def cell(self, index):
        return ''.join(self.nb['cells'][index]['source'])

    def fixture(self, directory):
        # Load the production pure selection functions without POSIX I/O imports.
        # Frozen bundle/worker integrity and actual GPU inference need runtime checks.
        tree = ast.parse((ROOT / 'vibiomir/stage9_core.py').read_text(encoding='utf-8'))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('select', 'overlap')]
        def require(ok, message):
            if not ok:
                raise ValueError(message)
        import math
        scope = dict(Counter=Counter, math=math, require=require)
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'selection', 'exec'), scope)
        candidates = []
        for i in range(3):
            text = f'Original evidence {i}'
            candidates.append(dict(chunk_id=str(i), doc_id=100+i, derived_version_id='v1',
                                   region_id='r1', region_start=0, region_end=len(text),
                                   source_start=20, source_end=20+len(text), chunk_text=text,
                                   text_sha256=digest(text.encode())))
        packet = dict(id=7, query='Query', candidates=candidates)
        scores = dict(scores=[dict(score=3-i) for i in range(3)])
        score_path = directory / 'query-7.json'
        score_path.write_text(json.dumps(scores), encoding='utf-8')
        scope.update(load_packet=lambda *a: packet, checked=lambda p: json.loads(Path(p).read_text()),
                     verify_scores=lambda record, *a: record['scores'], file_hash=file_hash,
                     text_hash=lambda s: digest(s.encode()), PROJECT=directory,
                     state=dict(signature='fixture', packets=[dict(id=7, worker_id=0)],
                                identity=dict(settings=dict(selection=dict(max_docs=1, max_chunks=1,
                                              max_chunks_per_doc=1, overlap_iou_threshold=.8)))),
                     worker_roots={0: directory}, worker_manifests={0: dict(signature='fixture')},
                     official_queries=[dict(id=7, query='Query')], allowed={100,101,102},
                     LIMITS=dict(max_docs=2, max_chunks=2, max_chunks_per_doc=1, overlap_iou_threshold=.8))
        return scope, packet

    def test_clean_notebook_and_python_syntax(self):
        for i, cell in enumerate(self.nb['cells']):
            if cell['cell_type'] == 'code':
                ast.parse(''.join(cell['source']), filename=f'cell-{i}')
                self.assertIsNone(cell['execution_count'])
                self.assertEqual(cell['outputs'], [])

    def test_selection_and_single_json_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            scope, _ = self.fixture(directory)
            exec(self.cell(5), scope)
            self.assertEqual(scope['predictions'][0]['relevant_docs'], [100,101])
            self.assertEqual(scope['diagnostics'][0]['new_docs_without_chunks'], 0)
            fixture = directory / 'input'
            fixture.write_bytes(b'fixture')
            scope.update(OUTPUT_PARENT=directory / 'output', json=json, zipfile=zipfile, digest=digest,
                         BUNDLE=fixture, query_path=fixture, links_path=fixture, RUN_MISSING_RERANK=False)
            downloads = []
            google = types.ModuleType('google')
            colab = types.ModuleType('google.colab')
            colab.files = types.SimpleNamespace(download=downloads.append)
            google.colab = colab
            with patch.dict('sys.modules', {'google': google, 'google.colab': colab}):
                exec(self.cell(7), scope)
            with zipfile.ZipFile(downloads[0]) as archive:
                self.assertEqual(archive.namelist(), ['submission.json'])
                self.assertEqual(json.loads(archive.read('submission.json')), scope['predictions'])
            report = json.loads((scope['out'] / 'REPORT.json').read_text())
            self.assertFalse(report['relevance_measured'])

    def test_ineligible_document_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            scope, packet = self.fixture(Path(temporary))
            packet['candidates'][0]['doc_id'] = 999
            with self.assertRaises(AssertionError):
                exec(self.cell(5), scope)

    def test_corrupt_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            scope, packet = self.fixture(Path(temporary))
            packet['candidates'][0]['chunk_text'] = 'Changed evidence'
            with self.assertRaises(AssertionError):
                exec(self.cell(5), scope)

    def test_query_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            scope, packet = self.fixture(Path(temporary))
            packet['query'] = 'Different query'
            with self.assertRaises(AssertionError):
                exec(self.cell(5), scope)

    def test_unchanged_selection_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            scope, _ = self.fixture(Path(temporary))
            scope['LIMITS'] = copy.deepcopy(scope['state']['identity']['settings']['selection'])
            with self.assertRaisesRegex(AssertionError, 'No predictions changed'):
                exec(self.cell(5), scope)


if __name__ == '__main__':
    unittest.main()
