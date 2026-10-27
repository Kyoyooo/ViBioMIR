"""Owner-run tiny S07/S08 fixtures; no corpus scans, downloads or CUDA jobs."""
import copy
import json
import math
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from vibiomir.chunk_batches import commit_batch, finish, output_store
from vibiomir.dense_models import MODELS, token_windows
from vibiomir.dense_stage8 import make_plan, read_plan, encode_worker, validate_worker, merge, encode_queries, bundles
from vibiomir.dense_index import build as build_index, search as search_dense, exact, sample_ids
from vibiomir.io import file_hash, text_hash, write_json
from vibiomir.lexical_stage7 import Analyzer, build, validate, connect, search
from vibiomir.retrieval_contracts import chunk_snapshot, source_parts, chunk_rows, signed


class FakeTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2
    def __call__(self, text, **kwargs):
        return {'input_ids': [ord(char) for char in text], 'offset_mapping': [(i, i + 1) for i in range(len(text))]}
    def prepare_for_model(self, ids, **kwargs):
        return {'input_ids': [0] + ids + [1], 'attention_mask': [1] * (len(ids) + 2), 'special_tokens_mask': [1] + [0] * len(ids) + [1]}


class FakeEncoder:
    calls = 0
    registry = {**MODELS['bge_m3'], 'parameters': 10, 'test_fixture': True}
    def __init__(self, *args, **kwargs):
        pass
    def encode(self, texts):
        FakeEncoder.calls += 1
        result = np.zeros((len(texts), 1024), dtype='float32')
        for i, text in enumerate(texts):
            result[i, 0 if text == 'fever fever' else 1] = 1
        return result, [1] * len(texts)


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.chunks = self.root / 'chunks'
        rows = []
        for doc_id, text in ((1, 'fever fever'), (2, 'fever pain'), (3, None)):
            chunks = [] if text is None else [{'chunk_id': f'c{doc_id}', 'doc_id': doc_id, 'derived_version_id': f'v{doc_id}',
                       'chunk_text': text, 'text_sha256': text_hash(text), 'retrieval_text': text, 'language_hint': 'en'}]
            rows.append({'doc_id': doc_id, 'derived_version_id': f'v{doc_id}', 'chunks': chunks})
        with output_store(self.chunks, {'operation': 'build-chunks', 'fixture': True}) as (folder, manifest, committed):
            for index, row in enumerate(rows):
                committed.append(commit_batch(folder, manifest['signature'], index, [row]))
            finish(folder, manifest, committed, True)
        write_json(self.chunks / 'VALIDATION.json', {'validation': 'passed', 'documents': 3, 'chunks': 2})
        self.lexical = self.root / 'lexical.json'
        self.lexical_output = self.root / 'lexical'
        write_json(self.lexical, {'schema': 'vibiomir-lexical-v1', 'chunk_dir': str(self.chunks), 'output_dir': str(self.lexical_output),
                                'analyzer': 'unicode_reference_v1', 'k1': 1.2, 'b': .75, 'sqlite_cache_mib': 16, 'top_k': 500})
        self.dense = self.root / 'dense.json'
        self.dense_root = self.root / 'dense'
        self.tokenizer = self.root / 'tokenizer'
        self.tokenizer.mkdir()
        write_json(self.tokenizer / 'REGISTRY.json', {'test_fixture': True})
        write_json(self.dense, {'schema': 'vibiomir-dense-v1', 'chunk_dir': str(self.chunks), 'run_dir': str(self.dense_root),
                               'tokenizer_dir': str(self.tokenizer), 'model': 'bge_m3', 'workers': 6, 'max_tokens': 512,
                               'window_overlap': 32, 'storage_dtype': 'float16', 'seed': 3, 'top_k': 500})
        self.plan_path = self.dense_root / 'plan.json'
        FakeEncoder.calls = 0

    def plan(self):
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            return make_plan(self.dense)

    def workers(self):
        plan = self.plan()
        for worker in plan['workers']:
            with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
                encode_worker(self.plan_path, self.chunks, self.tokenizer, self.dense_root / f"worker_{worker['id']}", worker['id'], encoder_factory=FakeEncoder)
        return plan

    def test_portable_source_ids_empty_documents_and_mutation_detection(self):
        source = chunk_snapshot(self.chunks)
        parts = source_parts(self.chunks, source)
        rows = [r for p in parts for r in chunk_rows(self.chunks, p)]
        self.assertEqual([r['row_id'] for r in rows], [0, 1])
        self.assertEqual(parts[-1]['documents'], 1)
        self.assertEqual(parts[-1]['rows'], 0)
        with (self.chunks / 'batch-000000/documents.jsonl.gz').open('ab') as stream:
            stream.write(b'x')
        with self.assertRaisesRegex(ValueError, 'changed'):
            list(chunk_rows(self.chunks, parts[0]))

    def test_lexical_pause_resume_global_df_and_hand_calculated_scores(self):
        self.assertEqual(build(self.lexical, stop_after_batches=1)['state'], 'paused')
        manifest = build(self.lexical)
        self.assertEqual(validate(self.lexical)['validation'], 'passed')
        analyzer = Analyzer('unicode_reference_v1')
        with connect(self.lexical_output / 'index.sqlite3') as db:
            bm = search(db, analyzer, 'fever', manifest)
            repeated = search(db, analyzer, 'fever fever', manifest)
            self.assertEqual(bm, repeated)  # BM25 uses unique query terms.
            self.assertEqual(bm[0]['chunk_id'], 'c1')
            self.assertAlmostEqual(bm[0]['score'], math.log(1.2) * 2 * 2.2 / 3.2)
            tfidf = search(db, analyzer, 'fever', manifest, method='tfidf')
            self.assertAlmostEqual(tfidf[0]['score'], 1.)
            self.assertAlmostEqual(tfidf[1]['score'], 1 / math.sqrt(1 + (math.log(3/2)+1)**2))
            self.assertEqual(search(db, analyzer, 'unseen', manifest), [])
            self.assertEqual(db.execute("SELECT df FROM terms WHERE term='w:fever'").fetchone()[0], 2)

    def test_analyzer_preserves_negation_units_accents_and_unspaced_han(self):
        result = Analyzer('unicode_reference_v1')('Không dùng 5 mg. 无发热。')
        self.assertIn('w:không', result)
        self.assertIn('w:5', result)
        self.assertIn('zh:发热', result)

    @unittest.skipUnless(importlib.util.find_spec('pyvi') and importlib.util.find_spec('jieba'), 'Install stage-7 requirements for the real analyzer fixture')
    def test_installed_real_analyzers_process_vi_en_zh(self):
        analyzer = Analyzer('pyvi_jieba_v1')
        for text in ('Không dùng 5 mg khi sốt.', 'No fever; dose 5 mg.', '肾结石治疗。'):
            result = analyzer(text)
            self.assertTrue(result)
        self.assertIn('không', ' '.join(analyzer('Không dùng 5 mg.')))

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Optional CPU tensor pooling fixture; no model download')
    def test_mbert_pooling_excludes_special_and_padding_tokens(self):
        import torch
        from vibiomir.dense_models import pool_hidden
        hidden = torch.tensor([[[100., 100.], [1., 3.], [3., 1.], [200., 200.], [0., 0.]]])
        attention, special = torch.tensor([[1, 1, 1, 1, 0]]), torch.tensor([[1, 0, 0, 1, 1]])
        result = pool_hidden(hidden, attention, special, 'masked_mean_excluding_special_and_padding')
        self.assertTrue(torch.equal(result, torch.tensor([[2., 2.]])))

    def test_changed_lexical_configuration_cannot_reuse_index(self):
        build(self.lexical)
        value = json.loads(self.lexical.read_text())
        value['b'] = .4
        write_json(self.lexical, value)
        with self.assertRaisesRegex(ValueError, 'changed'):
            build(self.lexical)

    def test_parallel_tokenization_matches_single_process_global_scores(self):
        first = build(self.lexical)
        with connect(self.lexical_output / 'index.sqlite3') as db:
            expected = search(db, Analyzer('unicode_reference_v1'), 'fever', first)
        config = json.loads(self.lexical.read_text())
        config.update(workers=2, output_dir=str(self.root / 'parallel-index'))
        write_json(self.lexical, config)
        second = build(self.lexical)
        with connect(Path(config['output_dir']) / 'index.sqlite3') as db:
            self.assertEqual(expected, search(db, Analyzer('unicode_reference_v1'), 'fever', second))

    def test_six_worker_plan_is_deterministic_and_has_no_overlap(self):
        first = self.plan()
        self.assertEqual(first, self.plan())
        self.assertEqual(len(first['workers']), 6)
        self.assertEqual(sorted(i for w in first['workers'] for i in w['parts']), [0, 1, 2])
        self.assertEqual(sum(w['rows'] for w in first['workers']), 2)

    def test_bad_plan_overlap_is_rejected_even_after_resigning(self):
        value = self.plan()
        value['workers'][1]['parts'].append(value['workers'][0]['parts'][0])
        value = signed({k: v for k, v in value.items() if k != 'signature'})
        write_json(self.plan_path, value)
        with self.assertRaisesRegex(ValueError, 'missing/duplicate'):
            read_plan(self.plan_path)

    def test_six_bundles_include_each_source_batch_once_and_reject_changed_queries(self):
        import zipfile
        self.plan()
        queries_path = self.root / 'queries.json'
        write_json(queries_path, [{'id': 1, 'query': 'fever'}])
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            result = bundles(self.dense, query_file=queries_path)
            self.assertEqual(result, bundles(self.dense, query_file=queries_path))
            occurrences = []
            for filename in result['bundles']:
                with zipfile.ZipFile(filename) as archive:
                    occurrences.extend(n for n in archive.namelist() if n.startswith('chunks/batch-') and n.endswith('documents.jsonl.gz'))
            self.assertEqual(len(occurrences), 3)
            self.assertEqual(len(set(occurrences)), 3)
            write_json(queries_path, [{'id': 1, 'query': 'pain'}])
            with self.assertRaisesRegex(ValueError, 'source/query differs'):
                bundles(self.dense, query_file=queries_path)

    def test_long_inputs_have_complete_mapped_windows(self):
        text = 'Không sốt; liều 5 mg. 中文。' * 10
        windows = token_windows(FakeTokenizer(), text, 16, 2)
        covered = set()
        for window in windows:
            covered.update(range(window['token_start'], window['token_end']))
            self.assertLessEqual(len(window['features']['input_ids']), 16)
        self.assertEqual(covered, set(range(len(text))))
        self.assertGreater(len(windows), 1)

    def test_worker_resume_does_not_reencode_completed_batch(self):
        plan = self.plan()
        target = self.dense_root / 'worker_0'
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            encode_worker(self.plan_path, self.chunks, self.tokenizer, target, 0, encoder_factory=FakeEncoder, stop_after_batches=1)
            calls = FakeEncoder.calls
            encode_worker(self.plan_path, self.chunks, self.tokenizer, target, 0, encoder_factory=FakeEncoder)
            self.assertEqual(FakeEncoder.calls, calls)
        self.assertEqual(validate_worker(self.plan_path, self.chunks, target, 0)['rows'], plan['workers'][0]['rows'])

    def test_backup_restore_and_six_way_merge_match_source_mapping(self):
        plan = self.workers()
        result = merge(self.plan_path, self.chunks, self.dense_root, self.dense_root / 'merged')
        self.assertEqual(result['rows'], 2)
        # A merge output is separate from worker directories, not their parent.
        self.assertEqual(read_plan(self.plan_path), plan)

    def test_drive_restore_reuses_vectors_and_recovers_registry_from_batch(self):
        self.plan()
        original, restored, backup = self.root / 'first', self.root / 'restored', self.root / 'drive'
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            encode_worker(self.plan_path, self.chunks, self.tokenizer, original, 0, backup_dir=backup, encoder_factory=FakeEncoder)
            calls = FakeEncoder.calls
            (backup / 'MODEL_REGISTRY.json').unlink()
            encode_worker(self.plan_path, self.chunks, self.tokenizer, restored, 0, backup_dir=backup, encoder_factory=FakeEncoder)
        self.assertEqual(calls, FakeEncoder.calls)
        self.assertTrue((restored / 'MODEL_REGISTRY.json').exists())
        self.assertEqual(validate_worker(self.plan_path, self.chunks, restored, 0)['validation'], 'passed')

    def test_nonfinite_vectors_are_rejected_even_with_updated_checksums(self):
        plan = self.workers()
        directory = self.dense_root / 'worker_0' / f"batch-{plan['workers'][0]['parts'][0]:06d}"
        values = np.load(directory / 'vectors.npy', allow_pickle=False)
        values[0, 0] = float('nan')
        np.save(directory / 'vectors.npy', values, allow_pickle=False)
        info = json.loads((directory / 'part.json').read_text())
        info['files']['vectors.npy'] = file_hash(directory / 'vectors.npy')
        write_json(directory / 'part.json', signed({k: v for k, v in info.items() if k != 'signature'}))
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            validate_worker(self.plan_path, self.chunks, self.dense_root / 'worker_0', 0)

    def test_missing_worker_prevents_successful_merge(self):
        self.plan()
        with self.assertRaisesRegex(ValueError, 'Missing worker_0'):
            merge(self.plan_path, self.chunks, self.dense_root, self.root / 'merged-missing')

    def test_exact_dense_candidates_use_stable_original_chunk_ids(self):
        self.workers()
        merged = self.root / 'merged'
        merge(self.plan_path, self.chunks, self.dense_root, merged)
        query_file = self.root / 'queries.json'
        write_json(query_file, [{'id': 42, 'query': 'fever fever'}])
        query_dir = self.root / 'query-vectors'
        with patch('vibiomir.dense_stage8.tokenizer_signature', return_value='fixture'):
            encode_queries(self.plan_path, self.tokenizer, query_file, query_dir, encoder_factory=FakeEncoder)
        index_dir, output = self.root / 'index', self.root / 'candidates'
        build_index(merged, index_dir, kind='exact')
        search_dense(merged, index_dir, query_dir, output, top_k=2)
        result = json.loads((output / 'query-42.json').read_text())
        self.assertEqual(result['candidates'][0]['chunk_id'], 'c1')
        self.assertAlmostEqual(result['candidates'][0]['score'], 1.)


if __name__ == '__main__':
    unittest.main()
