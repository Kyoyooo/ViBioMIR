"""Owner-run offline regressions with real fast BERT/XLM-R tokenizers, no GPU."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
from transformers import AutoTokenizer, BertTokenizerFast, XLMRobertaTokenizerFast

from vibiomir.dense_models import token_windows
from vibiomir.dense_tokenizer_compat import (
    SpecialMaskTokenizer, CompatibleDenseEncoder, compatibility_identity, ENCODER_SHA256)
from vibiomir.io import file_hash


class FastTokenizerCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def tokenizers(self):
        for cls, names in ((BertTokenizerFast, ('[UNK]', '[CLS]', '[SEP]', '[PAD]', '[MASK]')),
                           (XLMRobertaTokenizerFast, ('<unk>', '<s>', '</s>', '<pad>', '<mask>'))):
            unk, start, end, pad, mask = names
            vocab = {s: i for i, s in enumerate((*names, 'fever', 'pain', '5', 'mg', 'Không', '中文'))}
            backend = Tokenizer(models.WordLevel(vocab, unk_token=unk))
            backend.normalizer = normalizers.BertNormalizer(
                lowercase=False, strip_accents=False, handle_chinese_chars=False)
            backend.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
            backend.post_processor = processors.TemplateProcessing(
                single=f'{start} $A {end}', special_tokens=[(start, vocab[start]), (end, vocab[end])])
            path = self.root / f'{cls.__name__}.json'
            backend.save(str(path))
            yield cls(tokenizer_file=str(path), unk_token=unk, cls_token=start, sep_token=end,
                      pad_token=pad, mask_token=mask, bos_token=start, eos_token=end,
                      do_lower_case=False, strip_accents=False, tokenize_chinese_chars=False)

    def test_original_fast_api_reproduces_reported_assertion_and_adapter_matches_native(self):
        for tokenizer in self.tokenizers():
            with self.subTest(type=type(tokenizer).__name__):
                ids = tokenizer('fever pain', add_special_tokens=False)['input_ids']
                with self.assertRaisesRegex(AssertionError, 'already_has_special_tokens=False'):
                    tokenizer.prepare_for_model(ids, return_special_tokens_mask=True)
                fixed = SpecialMaskTokenizer(tokenizer).prepare_for_model(
                    ids, return_attention_mask=True, return_special_tokens_mask=True)
                native = tokenizer('fever pain', return_special_tokens_mask=True)
                for key in ('input_ids', 'attention_mask', 'special_tokens_mask'):
                    self.assertEqual(fixed[key], native[key])

    def test_all_windows_preserve_ids_offsets_overlap_and_unknown_content(self):
        for tokenizer in self.tokenizers():
            for text in ('Không fever 中文 5 mg ' * 20, 'unseen fever', f'fever {tokenizer.mask_token} pain'):
                with self.subTest(type=type(tokenizer).__name__, text=text[:30]):
                    original = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
                    windows = token_windows(SpecialMaskTokenizer(tokenizer), text, 16, 2)
                    covered = set()
                    for window in windows:
                        a, b = window['token_start'], window['token_end']
                        feature = window['features']
                        mask = feature['special_tokens_mask']
                        content = [i for i, m in zip(feature['input_ids'], mask) if not m]
                        self.assertEqual(content, original['input_ids'][a:b])
                        self.assertEqual(sum(mask), tokenizer.num_special_tokens_to_add(False))
                        self.assertEqual((window['character_start'], window['character_end']),
                                         (original['offset_mapping'][a][0], original['offset_mapping'][b - 1][1]))
                        self.assertLessEqual(len(feature['input_ids']), 16)
                        self.assertEqual(window['weight'], b - a)
                        covered.update(range(a, b))
                    self.assertEqual(covered, set(range(len(original['input_ids']))))
                    padded = tokenizer.pad([w['features'] for w in windows], return_tensors='np')
                    self.assertEqual(padded['input_ids'].shape, padded['special_tokens_mask'].shape)

    def test_adapter_registry_is_recorded_without_changing_frozen_encoder(self):
        tokenizer = next(self.tokenizers())
        def fake_init(encoder, *args, **kwargs):
            encoder.tokenizer, encoder.registry = tokenizer, {'parameters': 10}
        with patch('vibiomir.dense_models.DenseEncoder.__init__', fake_init):
            encoder = CompatibleDenseEncoder({}, self.root)
        self.assertIsInstance(encoder.tokenizer, SpecialMaskTokenizer)
        self.assertEqual(encoder.registry['tokenizer_compatibility'], compatibility_identity())
        self.assertEqual(file_hash(Path(__file__).parents[1] / 'vibiomir/dense_models.py'), ENCODER_SHA256)

    def test_registered_bge_tokenizer_offline_when_available(self):
        directory = Path(__file__).parents[1] / 'artifacts/tokenizers/bge_m3_stage5'
        if not (directory / 'REGISTRY.json').is_file():
            self.skipTest('Optional registered BGE tokenizer is not installed locally')
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, use_fast=True)
        for text in ('Không dùng 5 mg khi sốt. ' * 20, 'No fever; dose 5 mg. ' * 20, '肾结石治疗。' * 20):
            ids = tokenizer(text, add_special_tokens=False)['input_ids']
            windows = token_windows(SpecialMaskTokenizer(tokenizer), text, 32, 4)
            covered = set()
            for window in windows:
                features = window['features']
                content = [i for i, m in zip(features['input_ids'], features['special_tokens_mask']) if not m]
                self.assertEqual(content, ids[window['token_start']:window['token_end']])
                covered.update(range(window['token_start'], window['token_end']))
            self.assertEqual(covered, set(range(len(ids))))

    def test_notebook_embeds_the_exact_adapter_and_uses_it_for_queries_and_workers(self):
        root = Path(__file__).parents[1]
        notebook = json.loads((root / 'colab/stage8_dense_worker.ipynb').read_text())
        import ast
        source = ''.join(notebook['cells'][2]['source'])
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == 'DENSE_COMPAT_SOURCE' for t in n.targets))
        self.assertEqual(ast.literal_eval(node.value), (root / 'vibiomir/dense_tokenizer_compat.py').read_text())
        for index in (4, 6):
            self.assertIn('vibiomir.dense_tokenizer_compat', ''.join(notebook['cells'][index]['source']))


if __name__ == '__main__':
    unittest.main()
