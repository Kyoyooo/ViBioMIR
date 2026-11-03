"""Linux offline integration checks for expanded-pool score reuse."""
import ast
import json
from pathlib import Path
import sqlite3
import unittest
import zipfile

from test_stage9_retrieval import Stage9PipelineTest, FixtureScorer
from vibiomir.io import canonical_json, write_json
from vibiomir.stage9_core import score_key
from vibiomir.stage9_pipeline import prepare, rerank_worker, export, validate_results
from scripts.stage9_expand_support import seed_cache, package


class CachedScorer(FixtureScorer):
    misses = 0

    def __init__(self, policy, **kwargs):
        super().__init__(policy)
        self.policy = policy

    def score(self, query, passages, cache):
        result = []
        for text in passages:
            key = score_key(query, text, self.policy)
            row = cache.execute('SELECT payload FROM scores WHERE key=?', (key,)).fetchone()
            if row:
                value = json.loads(row[0])
            else:
                type(self).misses += 1
                value = dict(score=float(len(text)), windows=1)
                cache.execute('INSERT INTO scores VALUES(?,?)', (key, canonical_json(value)))
            result.append(value)
        cache.commit()
        return result


class ExpandedPoolTest(unittest.TestCase):
    def setUp(self):
        self.f = Stage9PipelineTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        f.c['rerank_top_k'] = 1
        write_json(f.config, f.c)
        prepare(f.config)
        for worker in range(2):
            rerank_worker(f.prepared, f.workers / f'worker_{worker}', worker, scorer_factory=FixtureScorer)
        self.expanded = f.root / 'expanded'
        self.new_workers = f.root / 'new_workers'
        f.c.update(rerank_top_k=3, workers=1, output_dir=str(self.expanded))
        write_json(f.config, f.c)
        prepare(f.config)

    def seed(self):
        return seed_cache(self.f.prepared, self.f.workers, self.expanded, self.new_workers / 'worker_0')

    def test_reuse_resume_source_validation_and_zip(self):
        report = self.seed()
        self.assertGreater(report['reused_pairs'], 0)
        self.assertGreater(report['new_pairs'], 0)
        self.assertEqual(self.seed()['reused_pairs'], report['reused_pairs'])
        CachedScorer.misses = 0
        output = self.new_workers / 'worker_0'
        rerank_worker(self.expanded, output, 0, stop_after_queries=1, scorer_factory=CachedScorer)
        rerank_worker(self.expanded, output, 0, scorer_factory=CachedScorer)
        self.assertEqual(CachedScorer.misses, report['new_pairs'])
        rerank_worker(self.expanded, output, 0, scorer_factory=CachedScorer)
        self.assertEqual(CachedScorer.misses, report['new_pairs'])
        results = self.f.root / 'results'
        export(self.expanded, results, mode='reranked', workers_dir=self.new_workers)
        validate_results(self.expanded, results, chunk_dir=self.f.chunk_dir,
                         queries_path=self.f.queries, workers_dir=self.new_workers)
        with self.assertRaises(ValueError):
            package(results, [101,202,303], {999}, self.f.root / 'bad')
        archive = package(results, [101,202,303], {1,2,3}, self.f.root / 'submission')
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.namelist(), ['submission.json'])
            self.assertEqual(len(json.loads(z.read('submission.json'))), 3)

    def test_tampered_old_score_rejected(self):
        path = self.f.workers / 'worker_0/query-101.json'
        record = json.loads(path.read_text())
        record['scores'][0]['score'] += 1
        write_json(path, record)
        with self.assertRaises(ValueError):
            self.seed()

    def test_conflicting_cache_rejected(self):
        self.seed()
        with sqlite3.connect(self.new_workers / 'worker_0/score_cache.sqlite3') as db:
            db.execute('UPDATE scores SET payload=?', ('{"score":-999,"windows":1}',))
        with self.assertRaises(ValueError):
            self.seed()

    def test_notebook_syntax_and_embedded_helper_match(self):
        root = Path(__file__).resolve().parents[1]
        nb = json.loads((root / 'colab/stage9_expand_pool_colab.ipynb').read_text())
        helper = None
        for cell in nb['cells']:
            if cell['cell_type'] != 'code':
                continue
            tree = ast.parse(''.join(cell['source']))
            self.assertIsNone(cell['execution_count'])
            self.assertFalse(cell['outputs'])
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'HELPER_SOURCE' for t in node.targets):
                    helper = ast.literal_eval(node.value)
        self.assertEqual(helper.strip(), (root / 'scripts/stage9_expand_support.py').read_text().strip())


if __name__ == '__main__':
    unittest.main()
