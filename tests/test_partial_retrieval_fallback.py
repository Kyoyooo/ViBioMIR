"""Deadline export must keep missing/invalid queries exactly at baseline."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from scripts import partial_retrieval_fallback as f
from tests.test_corpus_multiretrieval import CorpusPipelineTest, FakeEncoder


class Scorer:
    def __init__(self, policy, **kwargs):
        self.registry = dict(model_id=policy['model_id'])

    def score(self, query, passages):
        return [float(text.count(query)) for text in passages]


class FallbackTest(unittest.TestCase):
    def setUp(self):
        fixture = CorpusPipelineTest(methodName='test_bm25_resume_and_query_language')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.root = fixture, fixture.root
        values = {k:v for k,v in fixture.plan.items() if k != 'signature'}
        values.update(operation='corpus-multiretrieval-v1', query_sha256=f.core.EXPECTED_QUERY_SHA256)
        self.old_plan = f.core.signed(values)
        self.queries = self.old_plan['queries']
        self.old = self.root/'old'
        self.old.mkdir()
        f.core.write(self.old/'PLAN.json', self.old_plan)
        f.corpus.retrieve_dense(self.old_plan, self.old, 'qwen', fixture.source_plan, fixture.members,
            stop_after_batches=1, encoder_factory=FakeEncoder)
        f.corpus.retrieve_dense(self.old_plan, self.old, 'bge', fixture.source_plan, fixture.members,
            stop_after_batches=None, encoder_factory=FakeEncoder)
        self.rows = [dict(id=q['id'], relevant_docs=[1, 8], relevant_chunks=[
            dict(doc_id=1, chunk_text='Baseline supporting paragraph.', chunk_order=0)]) for q in self.queries]
        self.raw = json.dumps(self.rows, indent=2).encode()
        self.inputs = dict(baseline_zip_sha256='fixture', baseline_json_sha256=f.core.digest(self.raw),
                           query_sha256=f.core.EXPECTED_QUERY_SHA256)
        self.snapshot_dir = self.root/'snapshot'
        self.snapshot = f.snapshot_retrieval(self.old, self.snapshot_dir, self.queries)
        self.plan = f.new_plan(self.inputs, self.queries, self.snapshot, 3)
        self.run = self.root/'fallback'

    def prepare(self):
        with patch.object(f.recovery, 'inspect_bundles', return_value=(self.fixture.source_plan, self.fixture.members, {})):
            return f.prepare(self.plan, self.run, self.rows, self.queries, self.inputs, self.snapshot_dir,
                self.root/'bundles', self.root/'evidence.sqlite3')

    def test_partial_snapshot_missing_branches_and_frozen_live_checkpoint(self):
        self.assertEqual(self.snapshot['coverage_batches'], dict(qwen=1, bge=2, e5=0, bm25=0))
        self.assertEqual(self.snapshot['unavailable_roles'], ['e5', 'bm25'])
        self.assertFalse(self.snapshot['snapshot_is_full_corpus'])
        old = self.old/'retrieval/qwen/part-0-of-1/checkpoint.json'
        old.write_text('changed by continuing worker', encoding='utf-8')
        manifest, queries, roles, batches = f.frozen_hits(self.snapshot_dir)
        self.assertEqual(manifest, self.snapshot)
        self.assertEqual(batches, [0, 1])
        self.assertTrue(roles['qwen'][0])

    def test_one_query_scored_rest_falls_back_and_full_zip_exported(self):
        ready = self.prepare()
        self.assertEqual(ready['eligible_queries'], 2)
        result = f.score(self.plan, self.run, self.queries, stop_after_queries=1, scorer_factory=Scorer)
        self.assertEqual(result['new_queries'], 1)
        output = self.root/'export'
        report = f.export(self.plan, self.run, self.rows, self.queries, self.inputs, self.raw, output, {1, 2, 3, 4, 8})
        self.assertEqual(report['reranked_queries'], 1)
        self.assertEqual(report['fallback_queries'], 1)
        self.assertGreater(report['selected_new_candidate_occurrences'], 0)
        with zipfile.ZipFile(output/'submission.zip') as z:
            self.assertEqual(z.namelist(), ['submission.json'])
            rows = json.loads(z.read('submission.json'))
        self.assertEqual(rows[1], self.rows[1])
        self.assertEqual([r['id'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['relevant_docs'], self.rows[0]['relevant_docs'])
        f.core.validate_input_rows(rows, self.queries, 2)
        # Resume only scores the remaining query.
        resumed = f.score(self.plan, self.run, self.queries, stop_after_queries=None, scorer_factory=Scorer)
        self.assertEqual((resumed['new_queries'], resumed['reused_queries']), (1, 1))

    def test_no_prepare_and_invalid_scores_preserve_raw_baseline(self):
        out = self.root/'no_prepare'
        report = f.export(self.plan, self.run, self.rows, self.queries, self.inputs, self.raw, out)
        self.assertEqual(report['fallback_queries'], 2)
        self.assertEqual((out/'submission.json').read_bytes(), self.raw)
        self.prepare()
        f.score(self.plan, self.run, self.queries, stop_after_queries=1, scorer_factory=Scorer)
        path = self.run/'scores/query-1.json'
        value = f.core.read(path)
        value['scores'][0] = 123  # keep stale signature, must not accept
        f.core.write(path, value)
        out = self.root/'invalid_score'
        report = f.export(self.plan, self.run, self.rows, self.queries, self.inputs, self.raw, out, {1,2,3,4,8})
        self.assertEqual(report['fallback_queries'], 2)
        self.assertEqual((out/'submission.json').read_bytes(), self.raw)
        audit = f.core.read(out/'QUERY_AUDIT.json')
        self.assertIn('invalid_result', audit[0]['status'])

    def test_new_documents_opt_in_and_missing_allowlist_fallback(self):
        self.prepare()
        f.score(self.plan, self.run, self.queries, stop_after_queries=1, scorer_factory=Scorer)
        no_ids = self.root/'no_ids'
        result = f.export(self.plan, self.run, self.rows, self.queries, self.inputs, self.raw, no_ids)
        self.assertEqual(result['reranked_queries'], 0)
        changed = self.root/'new_docs'
        f.export(self.plan, self.run, self.rows, self.queries, self.inputs, self.raw, changed,
            {1,2,3,4,8}, update_documents=True)
        rows = f.core.read(changed/'submission.json')
        self.assertIn(3, rows[0]['relevant_docs'])
        self.assertEqual(rows[1], self.rows[1])

    def test_tampered_snapshot_and_wrong_query_rejected(self):
        with self.assertRaisesRegex(ValueError, 'query text differs'):
            f.snapshot_retrieval(self.old, self.root/'wrong', [dict(id=1,query='different'), self.queries[1]])
        path = self.snapshot_dir/self.snapshot['files'][0]['name']
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            f.frozen_hits(self.snapshot_dir)

    def test_1200_query_export_with_only_one_processed_query(self):
        queries = self.queries+[dict(id=i, query=f'Additional query {i}') for i in range(3, 1201)]
        rows = self.rows+[dict(id=q['id'], relevant_docs=[8], relevant_chunks=[
            dict(doc_id=8, chunk_text='Original baseline evidence.', chunk_order=0)]) for q in queries[2:]]
        raw = json.dumps(rows, indent=2).encode()
        inputs = {**self.inputs, 'baseline_json_sha256':f.core.digest(raw)}
        snapshot_dir = self.root/'snapshot-full-queries'
        snapshot = f.snapshot_retrieval(self.old, snapshot_dir, queries)
        plan = f.new_plan(inputs, queries, snapshot, 3)
        run = self.root/'full-fallback'
        with patch.object(f.recovery, 'inspect_bundles', return_value=(self.fixture.source_plan, self.fixture.members, {})):
            f.prepare(plan, run, rows, queries, inputs, snapshot_dir, self.root/'bundles', self.root/'full-evidence.db')
        f.score(plan, run, queries, stop_after_queries=1, scorer_factory=Scorer)
        output = self.root/'full-export'
        report = f.export(plan, run, rows, queries, inputs, raw, output, {1,2,3,4,8})
        self.assertEqual((report['queries'], report['reranked_queries'], report['fallback_queries']), (1200, 1, 1199))
        result = f.core.read(output/'submission.json')
        self.assertEqual(result[1:], rows[1:])

    def test_notebook_embeds_exact_support_and_parses(self):
        root = Path(__file__).resolve().parents[1]
        book = json.loads((root/'colab/stage9_partial_retrieval_fallback_colab.ipynb').read_text(encoding='utf-8'))
        found = False
        for cell in book['cells']:
            if cell['cell_type'] != 'code':
                continue
            source = ''.join(cell['source'])
            tree = ast.parse(source)
            if source.startswith('SUPPORT_FILES = '):
                for filename, text in ast.literal_eval(tree.body[0].value).items():
                    self.assertEqual(text, (root/'scripts'/filename).read_text(encoding='utf-8'))
                found = True
        self.assertTrue(found)


if __name__ == '__main__':
    unittest.main()
