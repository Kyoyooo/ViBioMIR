"""Exercise corpus-to-submission flow using real gzip/FTS/CPU matrix search."""
import ast
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
from scripts import corpus_multiretrieval as p


class FakeEncoder:
    calls = 0

    def __init__(self, policy):
        self.policy = policy

    def encode(self, texts, query=False):
        FakeEncoder.calls += 1
        values = np.array([[1+text.lower().count('heart'), 1+text.lower().count('lung')] for text in texts], dtype=np.float32)
        return values/np.linalg.norm(values, axis=1, keepdims=True)


class FakeScorer:
    def __init__(self, policy):
        self.policy = policy

    def score(self, query, passages):
        return [float(text.lower().count(query.lower())) for text in passages]


class CorpusPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root/'run'
        self.plan = p.core.signed(dict(source=p.SOURCE, embeddings=p.embedding_policies(),
            rerankers=p.multi.policies(), queries=[dict(id=1, query='heart'), dict(id=2, query='lung')],
            batch_indices=[0, 1], full_scope=False, top_k=3, rerank_k=3, bm25_query_terms=128,
            code={name: p.core.file_hash(path) for name, path in dict(pipeline=p.__file__,
                core=p.core.__file__, multi=p.multi.__file__, recovery=p.recovery.__file__).items()}))
        self.parts, self.members = [], {}
        for index in range(2):
            rows = []
            for d in range(index*2+1, index*2+3):
                text = ('heart ' if d % 2 else 'lung ')*d+'evidence'
                chunk = dict(chunk_id=f'chunk-{d}', doc_id=d, derived_version_id=f'version-{d}',
                    retrieval_text='Title '+text, chunk_text=text, text_sha256=p.core.digest(text.encode()),
                    region_id='body', source_start=0, source_end=len(text))
                rows.append(dict(doc_id=d, derived_version_id=f'version-{d}', chunks=[chunk]))
            raw = gzip.compress(('\n'.join(p.core.canonical(row) for row in rows)+'\n').encode())
            archive = self.root/f'worker-{index}.zip'
            prefix = f'chunks/batch-{index:06d}/'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(prefix+'documents.jsonl.gz', raw)
            self.members[index] = (archive, prefix, len(raw))
            self.parts.append(dict(index=index, source_sha256=p.core.digest(raw), rows=2, documents=2))
        self.source_plan = dict(parts=self.parts)

    def retrieve(self):
        p.retrieve_bm25(self.plan, self.run, self.source_plan, self.members, self.root/'bm25.db', None)
        for role in ('bge', 'qwen', 'e5'):
            p.retrieve_dense(self.plan, self.run, role, self.source_plan, self.members,
                stop_after_batches=None, encoder_factory=FakeEncoder)

    def test_end_to_end_new_corpus_evidence_and_pilot_guard(self):
        self.retrieve()
        report = p.prepare_pool(self.plan, self.run, self.source_plan, self.members, self.root/'evidence.db')
        self.assertTrue(report['original_evidence_verified'])
        item = p.packet(self.plan, self.run, self.plan['queries'][0])
        self.assertTrue(all(c['branches'] and c['chunk_id'].startswith('chunk-') for c in item['candidates']))
        for role in ('qwen', 'bge'):
            first = p.rerank(self.plan, self.run, role, stop_after_queries=1, scorer_factory=FakeScorer)
            self.assertEqual(first['new_queries'], 1)
            p.rerank(self.plan, self.run, role, stop_after_queries=None, scorer_factory=FakeScorer)
        out = self.root/'export'
        result = p.merge_export(self.plan, self.run, out)
        self.assertEqual(result['state'], 'pilot_only_not_for_submission')
        self.assertFalse((out/'submission.zip').exists())
        predictions = p.core.read(out/'PILOT_PREDICTIONS.json')
        self.assertEqual([row['id'] for row in predictions], [1, 2])
        self.assertTrue(all(c['chunk_text'].endswith('evidence') for row in predictions for c in row['relevant_chunks']))
        changed = copy.deepcopy(item)
        changed['candidates'][0]['chunk_text'] = 'tampered'
        path = self.run/'prepared/query-1.json'
        p.core.write(path, p.core.signed({k: v for k, v in changed.items() if k != 'signature'}))
        with self.assertRaisesRegex(ValueError, 'changed'):
            p.packet(self.plan, self.run, self.plan['queries'][0])

    def test_resume_dense_and_full_shard_coverage(self):
        p.retrieve_dense(self.plan, self.run, 'bge', self.source_plan, self.members,
            stop_after_batches=1, encoder_factory=FakeEncoder)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            p.collect_branch(self.plan, self.run, 'bge')
        result = p.retrieve_dense(self.plan, self.run, 'bge', self.source_plan, self.members,
            stop_after_batches=None, encoder_factory=FakeEncoder)
        self.assertEqual(result['completed_batches'], [0, 1])
        for shard in range(2):
            p.retrieve_dense(self.plan, self.run, 'e5', self.source_plan, self.members,
                shard, 2, None, encoder_factory=FakeEncoder)
        self.assertEqual(p.collect_branch(self.plan, self.run, 'bge'), p.collect_branch(self.plan, self.run, 'e5'))
        # A different partitioning cannot silently mix into an existing branch.
        p.retrieve_dense(self.plan, self.run, 'e5', self.source_plan, self.members,
            0, 1, None, encoder_factory=FakeEncoder)
        with self.assertRaises(ValueError):
            p.collect_branch(self.plan, self.run, 'e5')

    def test_bm25_resume_and_query_language(self):
        partial = p.retrieve_bm25(self.plan, self.run, self.source_plan, self.members, self.root/'bm25.db', 1)
        self.assertEqual(partial['state'], 'paused')
        result = p.retrieve_bm25(self.plan, self.run, self.source_plan, self.members, self.root/'bm25.db', None)
        self.assertEqual(result['state'], 'complete')
        self.assertTrue(all(result['hits']))
        self.assertIn('心脏', p.lexical('心脏病 và tim'))
        self.assertIn('tim', p.lexical('心脏病 và tim'))

    def test_tampered_source_and_embedding_receipts_rejected(self):
        original = self.parts[0]['source_sha256']
        self.parts[0]['source_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, 'Source batch changed'):
            p.source_batch(self.source_plan, self.members, 0)
        self.parts[0]['source_sha256'] = original
        p.retrieve_dense(self.plan, self.run, 'qwen', self.source_plan, self.members,
            stop_after_batches=1, encoder_factory=FakeEncoder)
        path = p.branch_root(self.run, 'qwen', 0, 1)/'checkpoint.json'
        saved = p.core.read(path)
        saved['hits'][0]['chunk-1'] = 999
        p.core.write(path, saved)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            p.retrieve_dense(self.plan, self.run, 'qwen', self.source_plan, self.members,
                encoder_factory=FakeEncoder)

    def test_selection_version_duplicate_overlap_and_allowlist(self):
        base = dict(doc_id=1, derived_version_id='a', region_id='r', source_start=0, source_end=100,
                    chunk_id='a', chunk_text='test', text_sha256='one')
        item = dict(id=1, candidates=[base, {**base, 'chunk_id':'b', 'text_sha256':'two'},
            {**base, 'chunk_id':'c', 'derived_version_id':'other', 'source_start':200, 'source_end':300}])
        pred, selected = p.choose(item, [3, 2, 1])
        self.assertEqual(len(selected), 1)
        full = p.core.signed({**{k:v for k,v in self.plan.items() if k != 'signature'}, 'full_scope':True})
        with self.assertRaises(ValueError):
            p.merge_export(full, self.run, self.root/'blocked')
        self.assertFalse((self.root/'blocked').exists())

    def test_notebook_embeds_exact_source(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('retrieval', 'rerank', 'export'):
            book = json.loads((root/'colab'/f'stage9_corpus_{name}_colab.ipynb').read_text(encoding='utf-8'))
            found = False
            for cell in book['cells']:
                if cell['cell_type'] != 'code':
                    continue
                source = ''.join(cell['source'])
                tree = ast.parse(source)
                if source.startswith('SUPPORT_FILES = '):
                    files = ast.literal_eval(tree.body[0].value)
                    for filename, content in files.items():
                        self.assertEqual(content, (root/'scripts'/filename).read_text(encoding='utf-8'))
                    found = True
            self.assertTrue(found)


if __name__ == '__main__':
    unittest.main()
