"""Reject broken query vectors, FAISS ID coverage and source candidate joins."""
import json
import unittest
from unittest.mock import patch

import numpy as np
import test_stage78_retrieval as fixtures
from vibiomir.dense_index import build, search
from vibiomir.dense_stage8 import encode_queries
from vibiomir.io import file_hash, write_json
from vibiomir.retrieval_contracts import open_run, signed
from vibiomir.stage678_validation import candidates, dense_index, lexical_snapshot, query_vectors


class BaselineValidationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RetrievalTest('test_backup_restore_and_six_way_merge_match_source_mapping')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.workers()
        from vibiomir.dense_stage8 import merge
        f = self.fixture
        self.merged = f.dense_root / 'merged'
        merge(f.plan_path, f.chunks, f.dense_root, self.merged)
        self.query_file = f.root / 'queries.json'
        write_json(self.query_file, [{'id': 11, 'query': 'fever fever'}, {'id': 12, 'query': 'pain'}])
        self.query_dir = f.dense_root / 'query_vectors'
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            encode_queries(f.plan_path, f.tokenizer, self.query_file, self.query_dir, encoder_factory=fixtures.FakeEncoder)

    def test_query_source_and_resealed_nonfinite_vectors_are_rejected(self):
        f = self.fixture
        self.assertEqual(query_vectors(f.plan_path, self.query_dir, self.query_file)['queries'], 2)
        path = self.query_dir / 'vectors.npy'
        values = np.load(path, allow_pickle=False)
        values[0, 0] = np.nan
        np.save(path, values, allow_pickle=False)
        state = json.loads((self.query_dir / 'manifest.json').read_text())
        state['vectors_sha256'] = file_hash(path)
        write_json(self.query_dir / 'manifest.json', state)
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            query_vectors(f.plan_path, self.query_dir, self.query_file)

    def test_lexical_snapshot_seal_rejects_changed_database(self):
        from vibiomir.lexical_stage7 import build as lexical_build, validate as lexical_validate
        f = self.fixture
        lexical_build(f.lexical)
        lexical_validate(f.lexical)
        self.assertEqual(lexical_snapshot(f.lexical)['chunks'], 2)
        with (f.lexical_output / 'index.sqlite3').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'snapshot seal differs'):
            lexical_snapshot(f.lexical)

    def test_candidate_validation_rejects_wrong_mapping_and_changed_query_text(self):
        f = self.fixture
        index = f.dense_root / 'exact'
        build(self.merged, index, kind='exact')
        self.assertEqual(dense_index(self.merged, index)['rows'], 2)
        output = f.root / 'candidates'
        search(self.merged, index, self.query_dir, output, top_k=2)
        self.assertEqual(candidates(self.query_file, output, self.merged)['candidates'], 4)
        path = output / 'query-11.json'
        record = json.loads(path.read_text())
        record['candidates'][0]['doc_id'] = 999
        record.pop('signature')
        write_json(path, signed(record))
        with self.assertRaisesRegex(ValueError, 'source mapping differs'):
            candidates(self.query_file, output, self.merged)
        write_json(self.query_file, [{'id': 11, 'query': 'different'}, {'id': 12, 'query': 'pain'}])
        with self.assertRaisesRegex(ValueError, 'query source differs'):
            candidates(self.query_file, output, self.merged)

    def test_ivfpq_duplicate_global_ids_are_rejected(self):
        import faiss
        from pathlib import Path
        threads = faiss.omp_get_max_threads()
        faiss.omp_set_num_threads(1)
        self.addCleanup(faiss.omp_set_num_threads, threads)
        train = np.random.default_rng(1).normal(size=(80, 1024)).astype('float32')
        train /= np.linalg.norm(train, axis=1, keepdims=True)
        index = faiss.IndexIVFPQ(faiss.IndexFlatIP(1024), 1024, 1, 64, 1, faiss.METRIC_INNER_PRODUCT)
        index.train(train)
        index.add_with_ids(train[:2], np.array([0, 0], dtype='int64'))
        output = self.fixture.dense_root / 'ivfpq'
        plan = json.loads(self.fixture.plan_path.read_text())
        manifest = open_run(output, {'plan_signature': plan['signature'], 'kind': 'ivfpq', 'm': 64,
                            'code_sha256': file_hash(Path(__file__).parents[1] / 'vibiomir/dense_index.py')})
        faiss.write_index(index, str(output / 'index.faiss'))
        manifest.update(state='complete', rows=2, checkpoint='index.faiss', effective_nlist=1, effective_nbits=1,
                        index_sha256=file_hash(output / 'index.faiss'))
        write_json(output / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Duplicate/out-of-range'):
            dense_index(self.merged, output)
        index.reset()
        index.add_with_ids(train[:2], np.array([0, 1], dtype='int64'))
        faiss.write_index(index, str(output / 'index.faiss'))
        manifest['index_sha256'] = file_hash(output / 'index.faiss')
        write_json(output / 'manifest.json', manifest)
        self.assertTrue(dense_index(self.merged, output)['unique_complete_vector_ids'])


if __name__ == '__main__':
    unittest.main()
