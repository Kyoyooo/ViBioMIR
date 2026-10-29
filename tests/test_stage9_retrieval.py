"""Owner-run S09 fixtures: synthetic evidence, no downloads/GPU/corpus scans."""
import copy
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

from vibiomir.chunk_batches import commit_batch, finish as finish_chunks, output_store
from vibiomir.io import file_hash, text_hash, write_json
from vibiomir.retrieval_contracts import chunk_snapshot, signed
from vibiomir.stage9_core import fuse, query_metadata, score_key, select, validate_facets, validate_settings
from vibiomir.stage9_pipeline import (bundle, export, load_packet, prepare, prepared, rerank_worker,
                                     show, validate_results, validate_worker, archive_worker, import_worker, finish)
from vibiomir.stage9_reranker import Reranker, aggregate, pair_windows, recommended_runtime


class FusionAndIntentTest(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((Path(__file__).parents[1] / 'configs/retrieval_stage9.json').read_text())

    @staticmethod
    def item(key, score):
        return {'chunk_id': key, 'score': score, 'doc_id': 1, 'derived_version_id': 'v1',
                'source_batch': 0, 'source_row': 0, 'chunk_order': ord(key[0]), 'retrieval_text_sha256': key}

    def test_shipped_configuration_is_valid_and_rejects_bool_or_bad_budgets(self):
        validate_settings(self.c)
        for key, value in [('workers', True), ('rerank_top_k', 0), ('lexical_alpha', float('nan'))]:
            changed = copy.deepcopy(self.c); changed[key] = value
            with self.assertRaises(ValueError): validate_settings(changed)
        changed = copy.deepcopy(self.c); changed['reranker']['revision'] = 'main'
        with self.assertRaises(ValueError): validate_settings(changed)

    def test_rrf_missing_branch_and_stable_ties(self):
        a, b = self.item('a', 2), self.item('b', 1)
        branches = [({'name': 'dense', 'family': 'dense'}, [a, b]),
                    ({'name': 'bm25', 'family': 'lexical'}, [dict(b, score=3), dict(a, score=0)])]
        output = fuse(branches, self.c)
        self.assertEqual([x['chunk_id'] for x in output], ['a', 'b'])
        self.assertAlmostEqual(output[0]['fused_score'], .5 / 61 + .5 / 62)
        one = fuse([branches[0], (branches[1][0], [])], self.c)
        self.assertAlmostEqual(one[0]['fused_score'], .5 / 61)

    def test_weighted_constant_lists_and_family_weight_cap(self):
        c = {**self.c, 'fusion': 'weighted'}
        rows = [self.item('a', 5), self.item('b', 5)]
        out = fuse([({'name': 'dense', 'family': 'dense'}, rows)], c)
        self.assertEqual([x['fused_score'] for x in out], [0, 0])
        lists = [({'name': f'lex{i}', 'family': 'lexical'}, [self.item('a', 5)]) for i in range(3)]
        lists.append(({'name': 'dense', 'family': 'dense'}, [self.item('b', 5)]))
        result = fuse(lists, self.c)
        self.assertAlmostEqual(result[0]['fused_score'], result[1]['fused_score'])
        self.assertAlmostEqual(result[0]['fused_score'], .5 / 61)

    def test_conflicting_mappings_duplicates_and_nonfinite_scores_fail(self):
        branch = {'name': 'dense', 'family': 'dense'}
        with self.assertRaises(ValueError): fuse([(branch, [self.item('a', math.inf)])], self.c)
        with self.assertRaises(ValueError): fuse([(branch, [self.item('a', 1), self.item('a', 0)])], self.c)
        with self.assertRaises(ValueError):
            fuse([(branch, [self.item('a', 1)]), ({'name': 'lex', 'family': 'lexical'},
                  [dict(self.item('a', 1), doc_id=2)])], self.c)

    def test_original_query_and_conditions_are_preserved(self):
        text = 'Trẻ 5 tuổi không sốt: triệu chứng và điều trị sỏi thận, dùng liều 5 mg không?'
        value = query_metadata(text)
        self.assertEqual(value['original_query'], text)
        self.assertTrue(value['multi_intent_signal'])
        self.assertFalse(value['clinical_intent_verified'])
        a = text.index('triệu chứng'); b = text.index(', dùng')
        facets = [{'id': 'care', 'start': a, 'end': b, 'text': text[a:b]}]
        self.assertEqual(validate_facets(text, facets), facets)
        with self.assertRaises(ValueError): validate_facets(text, [{**facets[0], 'text': 'ung thư'}])

    def test_score_cache_key_changes_for_query_text_passage_or_revision(self):
        key = score_key('không sốt', 'fever', self.c['reranker'])
        self.assertNotEqual(key, score_key('sốt', 'fever', self.c['reranker']))
        self.assertNotEqual(key, score_key('không sốt', 'no fever', self.c['reranker']))
        self.assertNotEqual(key, score_key('không sốt', 'fever', {**self.c['reranker'], 'revision': 'changed'}))


class CharPairTokenizer:
    def __call__(self, text, **kwargs):
        return {'input_ids': [ord(c) for c in text], 'offset_mapping': [(i, i + 1) for i in range(len(text))]}
    def num_special_tokens_to_add(self, pair=True): return 4
    def prepare_for_model(self, ids, pair_ids=None, **kwargs):
        assert kwargs['truncation'] is False
        value = [0] + ids + [2, 2] + pair_ids + [2]
        return {'input_ids': value, 'attention_mask': [1] * len(value)}


class PairWindowTest(unittest.TestCase):
    def setUp(self):
        self.policy = {'max_pair_tokens': 32, 'query_window_tokens': 12, 'window_overlap': 2}

    def test_long_query_and_passage_keep_complete_mapped_token_coverage(self):
        query, passage = 'Không dùng 5 mg! ' * 4, '症状与治疗。' * 10
        windows = pair_windows(CharPairTokenizer(), query, passage, self.policy)
        qcovered, pcovered = set(), set()
        for window in windows:
            a, b = window['query_tokens']; c, d = window['passage_tokens']
            qcovered.update(range(a, b)); pcovered.update(range(c, d))
            self.assertLessEqual(len(window['features']['input_ids']), 32)
            self.assertEqual(window['query_characters'], [a, b])
            self.assertEqual(window['passage_characters'], [c, d])
        self.assertEqual(qcovered, set(range(len(query))))
        self.assertEqual(pcovered, set(range(len(passage))))
        self.assertAlmostEqual(aggregate([{'query_window': 0}, {'query_window': 0}, {'query_window': 1}], [1., 3., 5.]), 4.)

    def test_real_fast_pair_api_does_not_request_unsupported_mask(self):
        from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
        from transformers import BertTokenizerFast
        with tempfile.TemporaryDirectory() as directory:
            names = ['[UNK]', '[CLS]', '[SEP]', '[PAD]', '[MASK]', 'Không', '5', 'mg', '肾结石']
            backend = Tokenizer(models.WordLevel({name: i for i, name in enumerate(names)}, unk_token='[UNK]'))
            backend.normalizer = normalizers.BertNormalizer(lowercase=False, strip_accents=False)
            backend.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
            backend.post_processor = processors.TemplateProcessing(single='[CLS] $A [SEP]',
                pair='[CLS] $A [SEP] $B:1 [SEP]:1', special_tokens=[('[CLS]', 1), ('[SEP]', 2)])
            path = Path(directory) / 'tokenizer.json'; backend.save(str(path))
            tokenizer = BertTokenizerFast(tokenizer_file=str(path), unk_token='[UNK]', cls_token='[CLS]',
                sep_token='[SEP]', pad_token='[PAD]', mask_token='[MASK]', do_lower_case=False, strip_accents=False)
            windows = pair_windows(tokenizer, 'Không 5 mg', '肾结石 5 mg', self.policy)
            self.assertEqual(windows[0]['features']['input_ids'], tokenizer('Không 5 mg', '肾结石 5 mg')['input_ids'])

    def test_oom_splits_microbatch_without_dropping_or_reordering(self):
        class OOM(Exception): pass
        class CUDA:
            calls = 0
            @classmethod
            def empty_cache(cls): cls.calls += 1
        class Torch:
            OutOfMemoryError = OOM
            cuda = CUDA
        instance = Reranker.__new__(Reranker)
        instance.torch, instance.batch_size = Torch, 8
        def infer(features):
            if len(features) > 1: raise OOM()
            return [features[0]['value']]
        instance._infer = infer
        self.assertEqual(instance._safe([{'value': x} for x in range(5)]), list(range(5)))
        self.assertEqual(instance.batch_size, 1)
        self.assertGreater(CUDA.calls, 0)

    def test_model_query_passage_cache_reuses_scored_windows(self):
        scorer = Reranker.__new__(Reranker)
        scorer.policy, scorer.tokenizer = self.policy, CharPairTokenizer()
        scorer.batch_size, scorer.padded_tokens = 2, 64
        calls = []
        def infer(features):
            calls.append(len(features))
            return [float(len(x['input_ids'])) for x in features]
        scorer._safe = infer
        with sqlite3.connect(':memory:') as cache:
            cache.execute('CREATE TABLE scores(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            first = scorer.score('Không sốt', ['肾结石治疗', 'fever'] * 10, cache)
            count = len(calls)
            self.assertEqual(scorer.score('Không sốt', ['肾结石治疗', 'fever'] * 10, cache), first)
            self.assertEqual(len(calls), count)
            scorer.score('Sốt', ['fever'], cache)
            self.assertGreater(len(calls), count)

    def test_gpu_memory_profiles_are_bounded_and_override_friendly(self):
        self.assertEqual([recommended_runtime(n)['batch_size'] for n in (15, 24, 40, 80)], [8, 16, 32, 64])
        self.assertEqual(recommended_runtime(80)['padded_tokens'], 65536)
        for bad in (0, -1, float('nan'), True):
            with self.assertRaises(ValueError): recommended_runtime(bad)

    def test_registered_local_bge_pair_windows_cover_real_vi_en_zh_tokens(self):
        from transformers import AutoTokenizer
        root = Path(__file__).parents[1] / 'artifacts/tokenizers/bge_m3_stage5'
        if not (root / 'REGISTRY.json').is_file(): self.skipTest('Optional local registered BGE tokenizer')
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, use_fast=True)
        for query, passage in [('Không dùng 5 mg khi sốt. ' * 5, 'No fever; dose 5 mg. ' * 30),
                               ('Triệu chứng và điều trị sỏi thận? ' * 4, '肾结石的症状及治疗。' * 50)]:
            windows = pair_windows(tokenizer, query, passage, self.policy)
            for field, text in [('query', query), ('passage', passage)]:
                count = len(tokenizer(text, add_special_tokens=False)['input_ids'])
                covered = set()
                for w in windows: covered.update(range(*w[field + '_tokens']))
                self.assertEqual(covered, set(range(count)))
            self.assertTrue(all(len(w['features']['input_ids']) <= self.policy['max_pair_tokens'] for w in windows))


class FixtureScorer:
    calls = 0
    def __init__(self, policy, **kwargs):
        from vibiomir.stage9_core import PINNED_RERANKER
        self.registry = {**PINNED_RERANKER, 'parameters': 10, 'window_policy': policy}
    def score(self, query, passages, cache):
        FixtureScorer.calls += 1
        return [{'score': float(len(text)), 'windows': 1} for text in passages]


class Stage9PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.chunk_dir, self.candidate_dir = self.root / 'chunks', self.root / 'dense_candidates'
        self.prepared, self.workers = self.root / 'prepared', self.root / 'workers'
        self.config = self.root / 'config.json'
        self.queries = self.root / 'queries.json'
        write_json(self.queries, [{'id': i, 'query': text} for i, text in
                                [(101, 'Trẻ 5 tuổi không sốt: triệu chứng và điều trị?'), (202, 'Không tìm thấy'), (303, '肾结石治疗')]])
        text = 'Triệu chứng và điều trị sỏi thận.'
        source_rows, mappings = [], []
        for doc_id, slices in [(1, [(0, 21), (1, 22)]), (2, [(0, 21)]), (3, [])]:
            chunks = []
            for order, (a, b) in enumerate(slices):
                evidence = text[a:b]
                chunk = {'chunk_id': f'c{doc_id}-{order}', 'doc_id': doc_id, 'derived_version_id': f'v{doc_id}',
                         'region_id': 'r1', 'region_start': a, 'region_end': b, 'source_start': 100 + a,
                         'source_end': 100 + b, 'source_kind': 'fixture', 'source_locator_json': '{}',
                         'chunk_text': evidence, 'text_sha256': text_hash(evidence),
                         'retrieval_text': 'Title\n' + evidence, 'language_hint': 'vi'}
                chunks.append(chunk)
                mappings.append({'chunk_id': chunk['chunk_id'], 'doc_id': doc_id, 'derived_version_id': f'v{doc_id}',
                                 'source_batch': doc_id - 1, 'source_row': 0, 'chunk_order': order,
                                 'retrieval_text_sha256': text_hash(chunk['retrieval_text']), 'score': 10 - len(mappings)})
            source_rows.append({'doc_id': doc_id, 'derived_version_id': f'v{doc_id}', 'chunks': chunks})
        with output_store(self.chunk_dir, {'operation': 'build-chunks', 'fixture': True}) as (folder, state, done):
            for index, row in enumerate(source_rows): done.append(commit_batch(folder, state['signature'], index, [row]))
            finish_chunks(folder, state, done, True)
        write_json(self.chunk_dir / 'VALIDATION.json', {'validation': 'passed', 'documents': 3, 'chunks': 3})
        self.candidate_dir.mkdir()
        identity = {'operation': 'dense-candidates', 'source': chunk_snapshot(self.chunk_dir)}
        from vibiomir.io import canonical_json
        self.candidate_state = {'identity': identity, 'signature': text_hash(canonical_json(identity)), 'state': 'complete'}
        write_json(self.candidate_dir / 'manifest.json', self.candidate_state)
        for query_id in (101, 202, 303):
            write_json(self.candidate_dir / f'query-{query_id}.json', signed({'id': query_id,
                       'run_signature': self.candidate_state['signature'], 'candidates': mappings if query_id != 202 else []}))
        write_json(self.candidate_dir / 'VALIDATION.json', {'validation': 'passed',
            'manifest_sha256': file_hash(self.candidate_dir / 'manifest.json'), 'query_sha256': file_hash(self.queries)})
        self.c = json.loads((Path(__file__).parents[1] / 'configs/retrieval_stage9.json').read_text())
        self.c.update(queries=str(self.queries), chunk_dir=str(self.chunk_dir), output_dir=str(self.prepared))
        self.c['branches'] = [self.c['branches'][0]]  # isolate the synthetic dense fixture
        self.c['branches'][0]['candidate_dir'] = str(self.candidate_dir)
        write_json(self.config, self.c)
        FixtureScorer.calls = 0

    def validate(self, output, **kwargs):
        return validate_results(self.prepared, output, chunk_dir=self.chunk_dir, queries_path=self.queries, **kwargs)

    def test_prepare_resume_baseline_empty_results_and_cross_doc_identity(self):
        self.assertEqual(prepare(self.config, stop_after_batches=1)['state'], 'paused')
        state = prepare(self.config)
        self.assertEqual(state['queries'], 3)
        self.assertEqual(state['unique_evidence_chunks'], 3)
        self.assertEqual(prepare(self.config)['signature'], state['signature'])
        output = self.root / 'baseline'
        export(self.prepared, output)
        report = self.validate(output)
        self.assertEqual(report['validation'], 'passed')
        results = json.loads((output / 'predictions.json').read_text())
        self.assertEqual(results[1], {'id': 202, 'relevant_docs': [], 'relevant_chunks': []})
        self.assertEqual(len(results[0]['relevant_chunks']), 2)
        self.assertEqual({x['doc_id'] for x in results[0]['relevant_chunks']}, {1, 2})
        self.assertEqual(results[0]['relevant_chunks'][0]['chunk_text'], results[0]['relevant_chunks'][1]['chunk_text'])
        self.assertTrue(any(x['reason'] == 'overlapping_span' for x in show(output, 101)['decisions']))
        self.assertNotIn('Title', results[0]['relevant_chunks'][0]['chunk_text'])

    def test_rerank_pause_restore_no_repeat_and_complete_export(self):
        state = prepare(self.config)
        backup = self.root / 'backup'
        for worker in range(2):
            target = self.workers / f'worker_{worker}'
            part = rerank_worker(self.prepared, target, worker, backup_dir=backup / f'worker_{worker}',
                                 stop_after_queries=1, scorer_factory=FixtureScorer)
            if worker == 0: self.assertEqual(part['state'], 'paused')
            rerank_worker(self.prepared, target, worker, backup_dir=backup / f'worker_{worker}', scorer_factory=FixtureScorer)
            self.assertEqual(validate_worker(self.prepared, target, worker)['validation'], 'passed')
        calls = FixtureScorer.calls
        import shutil
        shutil.rmtree(self.workers)
        for worker in range(2):
            rerank_worker(self.prepared, self.workers / f'worker_{worker}', worker,
                          backup_dir=backup / f'worker_{worker}', scorer_factory=FixtureScorer)
        self.assertEqual(FixtureScorer.calls, calls)
        output = self.root / 'reranked'
        export(self.prepared, output, mode='reranked', workers_dir=self.workers)
        self.assertEqual(self.validate(output, workers_dir=self.workers)['queries'], 3)

    def test_missing_workers_changed_config_and_tampered_packets_fail_early(self):
        state = prepare(self.config)
        with self.assertRaisesRegex(ValueError, 'Missing rerank worker'):
            export(self.prepared, self.root / 'bad', mode='reranked', workers_dir=self.workers)
        changed = copy.deepcopy(self.c); changed['selection']['max_chunks'] = 2; write_json(self.config, changed)
        with self.assertRaisesRegex(ValueError, 'changed'): prepare(self.config)
        packet = self.prepared / state['packets'][0]['path']
        with packet.open('ab') as stream: stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'packet changed'): export(self.prepared, self.root / 'bad2')

    def test_selected_original_source_mutation_and_changed_prediction_fail(self):
        prepare(self.config)
        output = self.root / 'baseline'; export(self.prepared, output)
        with (self.chunk_dir / 'batch-000000/documents.jsonl.gz').open('ab') as stream: stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'payload changed'): self.validate(output)
        results = json.loads((output / 'predictions.json').read_text()); results[0]['relevant_chunks'][0]['chunk_text'] = 'generated'
        write_json(output / 'predictions.json', results)
        with self.assertRaisesRegex(ValueError, 'prediction queries'): self.validate(output)

    def test_bounds_document_scores_exact_dedup_and_no_cross_doc_dedup(self):
        state = prepare(self.config); packet = load_packet(self.prepared, state['packets'][0])
        rows = packet['candidates']
        limits = {**self.c['selection'], 'max_docs': 1, 'max_chunks': 1, 'max_chunks_per_doc': 1}
        prediction, review = select(rows, [10., 9., 8.], limits)
        self.assertEqual(prediction['relevant_docs'], [1]); self.assertEqual(len(prediction['relevant_chunks']), 1)
        self.assertEqual(review['document_scores'][0]['score'], 10.)
        self.assertNotIn(19., [x['score'] for x in review['document_scores']])
        duplicate = {**rows[0], 'chunk_id': 'duplicate'}
        _, review = select([rows[0], duplicate], [10., 9.], self.c['selection'])
        self.assertEqual(review['decisions'][1]['reason'], 'exact_duplicate_in_document')

    def test_portable_bundle_checks_payloads_and_reuses_identical_archive(self):
        prepare(self.config)
        archive = self.root / 'stage9.zip'
        self.assertEqual(bundle(self.prepared, archive)['state'], 'complete')
        self.assertEqual(bundle(self.prepared, archive)['state'], 'reused')
        import zipfile
        with zipfile.ZipFile(archive) as z:
            self.assertIn('vibiomir/stage9_reranker.py', z.namelist())
            self.assertNotIn('evidence.sqlite3', z.namelist())
            portable = self.root / 'portable'; z.extractall(portable)
        self.assertEqual(prepared(portable)['queries'], 3)
        self.assertEqual(load_packet(portable, prepared(portable)['packets'][0])['id'], 101)

    def test_query_subset_is_labeled_and_cannot_be_validated_as_full(self):
        state = prepare(self.config, output_dir=self.root / 'pilot', limit_queries=1)
        self.assertEqual(state['identity']['scope'], 'query_subset')
        self.assertEqual(state['queries'], 1)

    def test_reranker_cannot_return_more_scores_than_input_candidates(self):
        prepare(self.config)
        class ExtraScorer(FixtureScorer):
            def score(self, query, passages, cache):
                return super().score(query, passages, cache) + [{'score': 999., 'windows': 1}]
        with self.assertRaisesRegex(ValueError, 'missing/extra scores'):
            rerank_worker(self.prepared, self.workers / 'worker_0', 0, scorer_factory=ExtraScorer)

    def test_worker_archives_finish_and_reuse_without_gpu(self):
        prepare(self.config)
        downloaded = self.root / 'downloaded_workers'
        for worker in range(2):
            target = self.workers / f'worker_{worker}'
            rerank_worker(self.prepared, target, worker, scorer_factory=FixtureScorer)
            archive = downloaded / f'stage9_worker_{worker}.zip'
            self.assertEqual(archive_worker(self.prepared, target, worker, archive)['state'], 'complete')
            self.assertEqual(archive_worker(self.prepared, target, worker, archive)['state'], 'reused')
        output = self.root / 'finished'
        for _ in range(2):
            report = finish(self.prepared, downloaded, output, chunk_dir=self.chunk_dir, queries_path=self.queries)
            self.assertEqual(report['validation'], 'passed')
            self.assertEqual(report['queries'], 3)
        self.assertEqual(import_worker(self.prepared, downloaded / 'stage9_worker_0.zip', downloaded)['state'], 'reused')

    def test_missing_and_tampered_worker_archives_fail_before_export(self):
        prepare(self.config)
        with self.assertRaisesRegex(ValueError, 'Missing GPU worker'):
            finish(self.prepared, self.root / 'missing', self.root / 'bad', chunk_dir=self.chunk_dir, queries_path=self.queries)
        target = self.workers / 'worker_0'
        rerank_worker(self.prepared, target, 0, scorer_factory=FixtureScorer)
        archive = self.root / 'worker.zip'
        archive_worker(self.prepared, target, 0, archive)
        import zipfile
        with zipfile.ZipFile(archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files['query-101.json'] += b'changed'
        with zipfile.ZipFile(archive, 'w') as destination:
            for name, data in files.items(): destination.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            import_worker(self.prepared, archive, self.root / 'restore')


if __name__ == '__main__':
    unittest.main()
