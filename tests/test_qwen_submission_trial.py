"""Offline trial checks: resume, exact spans, fixed docs and honest export scope."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import qwen_submission_trial as trial


class FixtureScorer:
    calls = []

    def __init__(self, policy, **kwargs):
        self.registry = dict(model_id=policy['model_id'], revision=policy['revision'],
            tokenizer_revision=policy['revision'], parameters=10, dtype='float16',
            scoring=policy['scoring'], commit_date=policy['commit_date'])

    def score(self, query, passages):
        self.calls.append((query, len(passages)))
        return [float(len(text)) for text in passages]


class CharacterTokenizer:
    def encode(self, value, **kwargs):
        return [ord(c) for c in value]


class QwenTrialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.text = ('First original sentence with useful context ' * 3 + '.\n'
                     + '第二句保留原文。' * 20 + '\n'
                     + 'Last original sentence ' * 6 + '?')
        self.rows = [dict(id=1, relevant_docs=[10, 20, 30], relevant_chunks=[
            dict(doc_id=10, chunk_text=self.text, chunk_order=0),
            dict(doc_id=20, chunk_text=self.text, chunk_order=1)]),
            dict(id=2, relevant_docs=[40], relevant_chunks=[dict(doc_id=40, chunk_text='Second query text.', chunk_order=0)])]
        self.queries = [dict(id=1, query='Original first query'), dict(id=2, query='Original second query')]
        self.raw = trial.canonical(self.rows).encode()
        self.inputs = dict(submission_json_sha256=trial.digest(self.raw), submission_zip_sha256='zip', query_sha256='query')
        self.identity = trial.run_identity(self.inputs, trial.generation_policy(True), trial.model_policy())
        self.output = self.root / 'scores'
        self.cache = self.root / 'cache.sqlite3'
        FixtureScorer.calls = []

    def run_scores(self, **kwargs):
        return trial.run_scoring(self.rows, self.queries, self.identity, self.output, self.cache,
                                 scorer_factory=FixtureScorer, **kwargs)

    def test_pilot_resume_dedup_and_idempotence(self):
        first = self.run_scores(stop_after_queries=1)
        self.assertEqual(first['state'], 'paused')
        # Same text under different doc IDs is scored once for this query.
        candidates = trial.make_candidates(self.rows[0], self.identity['generation'])
        unique_texts = {trial.evidence_text(self.rows[0], c) for c in candidates}
        self.assertEqual(FixtureScorer.calls[0][1], len(unique_texts))
        self.assertEqual(first['completed_query_ids'], [1])
        first_hash = trial.file_hash(self.output / 'query-1.json')
        finished = self.run_scores(stop_after_queries=None)
        self.assertEqual(finished['state'], 'complete')
        self.assertEqual(finished['completed_query_ids'], [1, 2])
        self.assertEqual(trial.file_hash(self.output / 'query-1.json'), first_hash)
        calls = len(FixtureScorer.calls)
        self.run_scores(stop_after_queries=None)
        self.assertEqual(len(FixtureScorer.calls), calls)

    def test_spans_preserve_originals_offsets_and_bound(self):
        policy = self.identity['generation']
        for row in self.rows:
            cs = trial.make_candidates(row, policy)
            originals = [c for c in cs if c['kind'] == 'original']
            self.assertEqual(len(originals), len(row['relevant_chunks']))
            for c in cs:
                text = trial.evidence_text(row, c)
                parent = row['relevant_chunks'][c['parent_index']]['chunk_text']
                self.assertEqual(text, parent[c['start']:c['end']])
            self.assertLessEqual(len(cs), len(row['relevant_chunks'])*(1+policy['max_subspans_per_parent']))
        self.assertTrue(any(c['kind']=='subspan' for c in trial.make_candidates(self.rows[0], policy)))
        decimal = 'Beta-HCG dưới 0.10 U/L. Câu sau giữ riêng!'
        spans = trial.sentence_spans(decimal)
        self.assertIn('0.10', decimal[spans[0][0]:spans[0][1]])

    def test_package_refuses_partial_then_preserves_docs_and_baseline(self):
        self.run_scores(stop_after_queries=1)
        variants = [dict(name='originals', originals_only=True, limits=dict(max_chunks=120,per_doc=6,overlap_iou=.8)),
                    dict(name='spans', originals_only=False, limits=dict(max_chunks=2,per_doc=1,overlap_iou=.8))]
        with self.assertRaisesRegex(ValueError, 'Finish all'):
            trial.package_variants(self.rows,self.queries,self.identity,self.output,self.root/'partial',self.raw,variants)
        self.assertFalse((self.root/'partial').exists())
        self.run_scores(stop_after_queries=None)
        report = trial.package_variants(self.rows,self.queries,self.identity,self.output,self.root/'export',self.raw,variants)
        self.assertFalse(report['original_corpus_evidence_verified'])
        self.assertFalse(report['official_lcs_evaluated'])
        with zipfile.ZipFile(self.root/'export/S0_baseline.zip') as z:
            self.assertEqual(z.read('submission.json'), self.raw)
        for variant in variants:
            with zipfile.ZipFile(self.root/f"export/{variant['name']}.zip") as z:
                self.assertEqual(z.namelist(), ['submission.json'])
                predictions = json.loads(z.read('submission.json'))
                self.assertEqual([r['id'] for r in predictions],[1,2])
                self.assertEqual([r['relevant_docs'] for r in predictions],[r['relevant_docs'] for r in self.rows])
        self.assertEqual(report['exports'][0]['changed_queries'],0)

    def test_tampered_score_wrong_source_and_changed_policy_rejected(self):
        self.run_scores(stop_after_queries=None)
        path = self.output/'query-1.json'
        original = trial.read(path)
        tampered = copy.deepcopy(original)
        tampered['scores'][0]['score'] += 1
        trial.write(path,tampered)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.run_scores(stop_after_queries=None)
        trial.write(path,original)
        self.identity['generation']['include_spans'] = False
        with self.assertRaisesRegex(ValueError,'changed'):
            self.run_scores(stop_after_queries=None)
        cs=trial.make_candidates(self.rows[0],trial.generation_policy(False))
        cs[0]['start']=1
        with self.assertRaisesRegex(ValueError,'differs'):
            trial.evidence_text(self.rows[0],cs[0])

    def test_token_template_and_no_silent_truncation(self):
        policy=trial.model_policy()
        tokenizer=CharacterTokenizer()
        encoded=trial.encoded_pair(tokenizer,'query','passage',policy)
        self.assertEqual(''.join(chr(v) for v in encoded['input_ids']),trial.formatted_input('query','passage',policy))
        self.assertEqual(encoded['attention_mask'],[1]*len(encoded['input_ids']))
        with self.assertRaisesRegex(ValueError,'tokens'):
            trial.encoded_pair(tokenizer,'query','passage',{**policy,'max_pair_tokens':10})

    def test_notebooks_embed_identical_helper_and_valid_python(self):
        root=Path(__file__).resolve().parents[1]
        support=(root/'scripts/qwen_submission_trial.py').read_text(encoding='utf-8')
        for name in ['stage9_qwen_score_submission.ipynb','stage9_qwen_select_submission.ipynb']:
            nb=trial.read(root/'colab'/name)
            for cell in nb['cells']:
                if cell['cell_type']!='code':
                    continue
                tree=ast.parse(''.join(cell['source']))
                if cell['id']=='helper':
                    self.assertEqual(ast.literal_eval(tree.body[0].value),support)

    def test_qwen_last_token_forward_matches_full_logits_on_tiny_random_model(self):
        # Test the actual Qwen forward/scoring API with random tiny weights on CPU.
        # No network, pretrained model, or CUDA inference is used.
        try:
            import torch
            from transformers import Qwen3Config, Qwen3ForCausalLM
        except ImportError:
            self.skipTest('Optional torch/transformers not installed')
        config = Qwen3Config(vocab_size=64, hidden_size=16, intermediate_size=32,
            num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1,
            head_dim=8, max_position_embeddings=128)
        torch.manual_seed(7)
        model = Qwen3ForCausalLM(config).eval()
        class Padder:
            def pad(self, features, **kwargs):
                width = max(len(f['input_ids']) for f in features)
                return {key: torch.tensor([[0]*(width-len(f[key]))+f[key] for f in features])
                        for key in ('input_ids','attention_mask')}
        scorer = trial.QwenScorer.__new__(trial.QwenScorer)
        scorer.torch, scorer.device, scorer.model = torch, torch.device('cpu'), model
        scorer.tokenizer, scorer.yes, scorer.no = Padder(), [1], [2]
        features = [dict(input_ids=[4,5,6],attention_mask=[1,1,1]),
                    dict(input_ids=[7,8],attention_mask=[1,1])]
        actual = scorer._batch_scores(features)
        with torch.inference_mode():
            full = model(**scorer.tokenizer.pad(features),use_cache=False,logits_to_keep=0).logits[:,-1,:]
            expected=(full[:,1]-full[:,2]).tolist()
        self.assertEqual(len(actual),2)
        for a,b in zip(actual,expected):
            self.assertAlmostEqual(a,b,places=6)


if __name__=='__main__':
    unittest.main()
