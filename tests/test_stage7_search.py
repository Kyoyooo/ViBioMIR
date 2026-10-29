"""Exact compact-postings parity, tie handling, thread and checkpoint fixtures."""
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import test_stage78_retrieval as fixtures
from vibiomir.io import write_json
from vibiomir.lexical_stage7 import Analyzer, build, validate as validate_index, connect, search
from vibiomir.lexical_projection import ARRAYS, build_projection, validate_projection, mapped
from vibiomir.lexical_search import ExactLexical, run, validate
from vibiomir.chunk_batches import read_json


class CompactLexicalTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RetrievalTest('test_lexical_pause_resume_global_df_and_hand_calculated_scores')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.index = build(f.lexical); validate_index(f.lexical)
        self.query_file = f.root / 'query.json'
        write_json(self.query_file, [{'id': 1, 'query': 'fever fever pain'}, {'id': 2, 'query': 'fever'},
                                     {'id': 3, 'query': 'unseen'}, {'id': 4, 'query': 'pain pain'}])
        self.projection = f.root / 'projection'
        build_projection(f.lexical, self.query_file, self.projection, builder='python')
        self.engine = ExactLexical(self.projection, self.index, f.lexical_output / 'index.sqlite3', weight_cache_mib=16)
        self.addCleanup(self.engine.close)

    def test_float64_scores_and_ranking_match_original_sql_both_methods(self):
        f = self.fixture
        with connect(f.lexical_output / 'index.sqlite3') as db:
            for q in read_json(self.projection / 'queries.json'):
                actual = self.engine.search(q, top_k=10)
                for method in ('bm25', 'tfidf'):
                    reference = search(db, Analyzer('unicode_reference_v1'), q['query'], self.index, method=method, top_k=10)
                    self.assertEqual([r['chunk_id'] for r in reference], [r['chunk_id'] for r in actual[method]])
                    for a, b in zip(actual[method], reference):
                        self.assertAlmostEqual(a['score'], b['score'], places=12)
                        self.assertEqual({k:v for k,v in a.items() if k!='score'}, {k:v for k,v in b.items() if k!='score'})

    def test_ties_use_chunk_ids_and_do_not_return_unmatched_zeroes(self):
        result = self.engine.top(np.array([1., 1.]), 1)
        self.assertEqual(result, [(0, 1.)])
        self.assertEqual(self.engine.top(np.zeros(2), 500), [])
        self.assertEqual(self.engine.top(np.array([0., 1.]), 500), [(1, 1.)])

    def test_one_and_two_workers_and_pause_resume_are_equivalent(self):
        f = self.fixture
        outputs = {m: f.root / m for m in ('bm25', 'tfidf')}
        first = run(f.lexical, self.query_file, self.projection, outputs, workers=2, stop_after_queries=1, top_k=2, weight_cache_mib=16)
        self.assertTrue(all(r['state']=='paused' and r['queries']==1 for r in first.values()))
        previous = {m:(p/'query-1.json').read_bytes() for m,p in outputs.items()}
        result = run(f.lexical, self.query_file, self.projection, outputs, workers=2, top_k=2, weight_cache_mib=16)
        self.assertTrue(all(r['state']=='complete' for r in result.values()))
        for method, path in outputs.items():
            self.assertEqual((path/'query-1.json').read_bytes(),previous[method])
            self.assertEqual(validate(f.lexical,self.query_file,path)['queries'],4)
        other = {m:f.root/('single_'+m) for m in outputs}
        run(f.lexical,self.query_file,self.projection,other,workers=1,top_k=2,weight_cache_mib=16)
        for method in outputs:
            for q in (1,2,3,4):
                self.assertEqual(read_json(outputs[method]/f'query-{q}.json')['candidates'],
                                 read_json(other[method]/f'query-{q}.json')['candidates'])

    @unittest.skipUnless(shutil.which('g++'), 'Optional native C++17 builder requires g++')
    def test_native_and_python_arrays_are_byte_identical(self):
        native = self.fixture.root / 'native'
        build_projection(self.fixture.lexical,self.query_file,native,builder='native')
        for name in ARRAYS: self.assertEqual((native/name).read_bytes(),(self.projection/name).read_bytes())

    def test_projection_checksum_and_different_query_cannot_be_reused(self):
        target = self.projection/'tf.u32'
        with target.open('ab') as stream: stream.write(b'changed')
        with self.assertRaises(ValueError): validate_projection(self.projection)
        write_json(self.query_file,[{'id':1,'query':'changed'}])
        with self.assertRaises(ValueError): build_projection(self.fixture.lexical,self.query_file,self.projection,builder='python')

    def test_weight_cache_is_bounded_and_reuses_same_values(self):
        query = read_json(self.projection/'queries.json')[0]
        first = self.engine.search(query)
        self.assertGreater(self.engine.cache_misses,0)
        misses = self.engine.cache_misses
        self.assertEqual(self.engine.search(query),first)
        self.assertEqual(self.engine.cache_misses,misses)
        self.assertGreater(self.engine.cache_hits,0)
        self.assertLessEqual(self.engine.bytes,self.engine.budget)

    def test_validated_lexical_candidates_bind_to_stage9_source_and_queries(self):
        from vibiomir.stage9_pipeline import _inputs
        f = self.fixture
        output = f.root / 'bm25_for_stage9'
        run(f.lexical,self.query_file,self.projection,{'bm25':output},workers=2,top_k=2,weight_cache_mib=16)
        validate(f.lexical,self.query_file,output)
        c = read_json(Path(__file__).parents[1]/'configs/retrieval_stage9.json')
        c.update(queries=str(self.query_file),chunk_dir=str(f.chunks))
        c['branches']=[{'name':'bm25','family':'lexical','facet_id':'original','candidate_dir':str(output)}]
        rows,identity = _inputs(c)
        self.assertEqual(len(rows),4)
        self.assertEqual(identity['source'],self.index['identity']['source'])
        self.assertEqual(identity['branches'][0]['family'],'lexical')


if __name__=='__main__': unittest.main()
