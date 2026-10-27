"""Owner-run offline S06 fixtures; no encoder download, CUDA or corpus scan."""
import copy
import json
from pathlib import Path
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from vibiomir.chunk_batches import batches, commit_batch, finish, output_store, read_rows
from vibiomir.icd import (CHAPTERS, RuleTagger, annotate, bounded_segments, evidence, fields,
                          load_taxonomy, normalized_with_offsets, semantic_candidates, settings)
from vibiomir.icd_bundle import bundle
from vibiomir.icd_pipeline import build, tag_text, validate
from vibiomir.io import file_hash, write_json

ROOT = Path(__file__).resolve().parents[1]


def packet(text, doc_id=1):
    return {'doc_id': doc_id, 'derived_version_id': f'clean-{doc_id}', 'parent_version_id': f'raw-{doc_id}',
            'availability': 'content_candidate', 'title': None,
            'regions': [{'region_id': 'r', 'text': text, 'source_start': 7, 'source_kind': 'stage3_text',
                         'source_locator_json': '{"column":"text"}'}]}


class CharacterTokenizer:
    def __call__(self, text, **kwargs):
        return {'offset_mapping': [(i, i + 1) for i in range(len(text))]}

    def encode(self, text, **kwargs):
        return [0] * (len(text) + 2)


class ICDTest(unittest.TestCase):
    def setUp(self):
        self.config = settings(ROOT / 'configs/icd_stage6.json')
        self.taxonomy = load_taxonomy(ROOT / 'taxonomy/icd10_chapters.json')
        self.rules = RuleTagger(self.taxonomy, self.config)

    def annotate(self, source, mode='rules', semantic=None):
        return annotate(source, self.rules.tag(source), semantic or [], self.config, mode, 'fixture')

    def test_all_22_chapters_and_version_ranges_are_required(self):
        self.assertEqual([c['id'] for c in self.taxonomy['chapters']], CHAPTERS)
        with tempfile.TemporaryDirectory() as directory:
            broken = copy.deepcopy(self.taxonomy)
            broken['chapters'][6]['range'] = ['H00', 'H95']
            path = Path(directory) / 'bad.json'
            write_json(path, broken)
            with self.assertRaisesRegex(ValueError, '22 ordered'):
                load_taxonomy(path)

    def test_diabetes_in_all_languages_maps_to_chapter_iv(self):
        for text in ('Điều trị đái tháo đường típ 2.', 'Type 2 diabetes treatment.', '2型糖尿病治疗。'):
            with self.subTest(text=text):
                result = self.annotate(packet(text))['annotations']['rules']
                self.assertEqual(result['labels'], ['IV'])
                self.assertIsNone(result['confidence'])
                self.assertFalse(result['retrieval_boost_enabled'])

    def test_multiple_topics_negation_unknown_and_word_boundaries(self):
        self.assertEqual(set(self.annotate(packet('Diabetes mellitus and hypertension.'))['annotations']['rules']['labels']), {'IV', 'IX'})
        for text in ('No diabetes mellitus.', 'Nghi ngờ đái tháo đường.', '否认糖尿病。', 'vacation photography', 'feverish'):
            result = self.annotate(packet(text))['annotations']['rules']
            self.assertTrue(result['unknown'], text)
        self.assertTrue(self.rules.tag(packet('No diabetes mellitus.'))['suppressed_matches'])

    def test_unicode_offsets_are_exact_after_nfkc_and_combining_normalization(self):
        for text in (unicodedata.normalize('NFD', 'Đái tháo đường; liều 5 mg.'), 'Ｔｙｐｅ ２ ｄｉａｂｅｔｅｓ; 5 mg.'):
            result = self.rules.tag(packet(text))
            item = result['candidates'][0]['evidence'][0]
            self.assertEqual(item['text'], text[item['start']:item['end']])
            self.assertEqual(item['source_start'], 7 + item['start'])
            self.assertEqual(normalized_with_offsets(item['text'])[0], item['seed'])

    def test_chest_pain_does_not_establish_coronary_disease(self):
        source = packet('Người bệnh đau ngực, chưa rõ nguyên nhân.')
        item = evidence(list(fields(source))[0], 0, len(source['regions'][0]['text']))
        semantic = [{'chapter': 'IX', 'score': 0.95, 'evidence': [item]}, {'chapter': 'XVIII', 'score': 0.3, 'evidence': [item]}]
        result = self.annotate(source, 'combined', semantic)['annotations']
        self.assertEqual(result['rules']['labels'], ['XVIII'])
        self.assertEqual(result['semantic']['labels'], [])
        self.assertEqual(result['combined']['labels'], ['XVIII'])

    def test_disagreement_and_semantic_margin_abstain(self):
        source = packet('Diabetes mellitus.')
        item = evidence(list(fields(source))[0], 0, 8)
        scores = [{'chapter': 'IX', 'score': 0.9, 'evidence': [item]}, {'chapter': 'IV', 'score': 0.2, 'evidence': [item]}]
        result = self.annotate(source, 'combined', scores)['annotations']['combined']
        self.assertTrue(result['unknown'])
        self.assertEqual(result['reason'], 'rule_semantic_disagreement')
        scores = [{'chapter': 'IX', 'score': 0.66, 'evidence': [item]}, {'chapter': 'IV', 'score': 0.64, 'evidence': [item]}]
        self.assertTrue(self.annotate(source, 'semantic', scores)['annotations']['semantic']['unknown'])

    def test_segments_bound_tokenization_and_preserve_source_slices(self):
        source = packet('医学 text. ' * 10000)
        source['title'] = 'Clinical title'
        tokenizer = CharacterTokenizer()
        segments = bounded_segments(source, tokenizer, self.config)
        self.assertLessEqual(len(segments), self.config['max_semantic_segments'])
        self.assertEqual(segments[0]['field'], 'title')
        for item in segments:
            self.assertLessEqual(len(tokenizer.encode(item['text'])), 512)
            text = source['title'] if item['field'] == 'title' else source['regions'][0]['text']
            self.assertEqual(item['text'], text[item['start']:item['end']])

    def test_semantic_cosine_multilabel_and_invalid_vectors(self):
        source = packet('Clinical topic')
        item = evidence(list(fields(source))[0], 0, 8)
        rows = [('IV', 'en', 'diabetes'), ('IX', 'en', 'heart')]
        scores = semantic_candidates([item], [[1., 1.]], rows, [[1., 0.], [0., 1.]])
        self.assertAlmostEqual(scores[0]['score'], 2 ** -0.5, places=6)
        result = self.annotate(source, 'semantic', scores)['annotations']['semantic']
        self.assertEqual(set(result['labels']), {'IV', 'IX'})
        with self.assertRaisesRegex(ValueError, 'Zero vector'):
            semantic_candidates([item], [[0., 0.]], rows, [[1., 0.], [0., 1.]])
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            semantic_candidates([item], [[float('nan'), 0.]], rows, [[1., 0.], [0., 1.]])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs, self.output = self.root / 'inputs', self.root / 'labels'
        self.config = ROOT / 'configs/icd_stage6.json'
        self.rows = [packet('Type 2 diabetes.', 1), packet('高血压。', 2), packet('đau ngực', 3),
                     {'doc_id': 4, 'derived_version_id': None, 'parent_version_id': None, 'availability': 'unavailable', 'regions': []}]
        with output_store(self.inputs, {'operation': 'prepare-chunk-inputs', 'fixture': True}) as (directory, manifest, committed):
            for index in range(2):
                committed.append(commit_batch(directory, manifest['signature'], index, self.rows[index * 2:index * 2 + 2]))
            finish(directory, manifest, committed, True)

    def test_rules_pause_resume_complete_validate_and_preserve_inputs(self):
        original = file_hash(self.inputs / 'manifest.json')
        self.assertEqual(build(self.inputs, self.output, self.config, stop_after_batches=1)['state'], 'paused')
        result = build(self.inputs, self.output, self.config)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['documents'], 4)
        self.assertEqual(validate(self.inputs, self.output, self.config)['validation'], 'passed')
        self.assertEqual(original, file_hash(self.inputs / 'manifest.json'))
        self.assertEqual(build(self.inputs, self.output, self.config)['documents'], 4)
        rows = [row for path, _ in batches(self.output) for row in read_rows(path)]
        self.assertTrue(rows[-1]['annotations']['rules']['unknown'])

    def test_wrong_mode_changed_source_and_tampered_evidence_are_rejected(self):
        build(self.inputs, self.output, self.config)
        with patch('vibiomir.chunk_models.load_tokenizer', return_value=(CharacterTokenizer(), 'fake')):
            with self.assertRaisesRegex(ValueError, 'changed'):
                build(self.inputs, self.output, self.config, mode='combined', tokenizer_dir=self.root)
        target = self.output / 'batch-000000/documents.jsonl.gz'
        with target.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate(self.inputs, self.output, self.config)

    def test_missing_output_and_invalid_settings_fail_before_processing(self):
        with self.assertRaisesRegex(ValueError, 'run the ICD build first'):
            validate(self.inputs, self.output, self.config)
        path = self.root / 'bad_config.json'
        write_json(path, [])
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            settings(path)
        with self.assertRaisesRegex(ValueError, 'stop-after-batches'):
            build(self.inputs, self.output, self.config, stop_after_batches=0)
        self.assertFalse(self.output.exists())

    def test_combined_resume_from_drive_backup_preserves_model_registry(self):
        from vibiomir.chunk_models import MODEL_ID, MODEL_REVISION
        class FakeScorer:
            registry = {'model_id': MODEL_ID, 'revision': MODEL_REVISION, 'parameters': 10, 'test_fixture': True}
            def __init__(self, *args, **kwargs):
                pass
            def score(self, packets, config):
                output = []
                for p in packets:
                    fs = list(fields(p))
                    if not fs:
                        output.append([])
                    else:
                        item = evidence(fs[0], 0, len(fs[0]['text']))
                        output.append([{'chapter': chapter, 'score': 0.1 - index / 1000, 'evidence': [item]}
                                       for index, chapter in enumerate(CHAPTERS)])
                return output
        backup = self.root / 'drive'
        with patch('vibiomir.chunk_models.load_tokenizer', return_value=(CharacterTokenizer(), 'fake')):
            build(self.inputs, self.output, self.config, mode='combined', tokenizer_dir=self.root / 'tokenizer',
                  backup_dir=backup, scorer_factory=FakeScorer, stop_after_batches=1)
            restored = self.root / 'restored'
            result = build(self.inputs, restored, self.config, mode='combined', tokenizer_dir=self.root / 'tokenizer',
                           backup_dir=backup, scorer_factory=FakeScorer)
            self.assertEqual(result['documents'], 4)
            self.assertTrue((restored / 'MODEL_REGISTRY.json').exists())
            self.assertEqual(validate(self.inputs, restored, self.config)['annotation_records'], 12)

    def test_text_annotation_reuses_schema_and_preserves_file_provenance(self):
        path, output = self.root / 'query.txt', self.root / 'query_labels.json'
        path.write_text('Cách điều trị đái tháo đường?', encoding='utf-8')
        result = tag_text(path, output, self.config, entity_kind='query', entity_id='42')
        self.assertEqual(result['annotations']['rules']['labels'], ['IV'])
        self.assertEqual(result['source']['text_file_sha256'], file_hash(path))
        self.assertEqual(result['entity_kind'], 'query')

    def test_bundle_is_portable_and_protects_existing_zip(self):
        import zipfile
        tokenizer = self.root / 'tokenizer'
        tokenizer.mkdir()
        write_json(tokenizer / 'REGISTRY.json', {'test_fixture': True})
        output = self.root / 'colab.zip'
        with patch('vibiomir.chunk_models.load_tokenizer', return_value=(CharacterTokenizer(), 'fake')):
            bundle(self.inputs, tokenizer, output, self.config)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                bundle(self.inputs, tokenizer, output, self.config)
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertIn('taxonomy/icd10_chapters.json', names)
            self.assertIn('inputs/batch-000000/documents.jsonl.gz', names)
            self.assertIn('tests/test_stage6_icd.py', names)
            self.assertFalse(any('document_store.sqlite3' in n or n.endswith('.safetensors') for n in names))


if __name__ == '__main__':
    unittest.main()
