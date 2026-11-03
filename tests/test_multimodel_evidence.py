"""Offline coverage for multi-session scoring, fusion and arbitration."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts import qwen_submission_trial as core
from scripts import multimodel_evidence as multi


class FixtureModel:
    calls=[]

    def __init__(self,policy,**kwargs):
        self.policy=policy
        self.registry=dict(model_id=policy['model_id'],revision=policy['revision'],
            tokenizer_revision=policy['revision'],commit_date=policy['commit_date'],
            dtype=policy['dtype'],scoring=policy['scoring'],parameters=10)

    def score(self,query,passages):
        self.calls.append((self.policy['model_id'],query,len(passages)))
        sign=-1 if self.policy['model_id'].startswith('BAAI') else 1
        return [float(sign*len(p)) for p in passages]


class EnsembleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.rows=[dict(id=i,relevant_docs=[10,20,30],relevant_chunks=[
            dict(doc_id=10,chunk_text='Long supporting paragraph.\n' * 20,chunk_order=0),
            dict(doc_id=20,chunk_text='Other evidence.',chunk_order=1)]) for i in range(1,5)]
        self.queries=[dict(id=i,query=f'Query {i}') for i in range(1,5)]
        self.raw=core.canonical(self.rows).encode()
        self.inputs=dict(submission_json_sha256=core.digest(self.raw),submission_zip_sha256='zip',query_sha256='query')
        self.plan=multi.build_plan(self.inputs,core.generation_policy(False))
        FixtureModel.calls=[]

    def worker(self,role,**kwargs):
        return multi.score_worker(self.rows,self.queries,self.plan,self.root,role,
            local_cache=self.root/f'cache-{role}.sqlite3',scorer_factory=FixtureModel,**kwargs)

    def merge(self,name,**kwargs):
        return multi.merge_and_export(self.rows,self.queries,self.plan,self.root,self.root/name,self.raw,
                                       top_k=1,rank_gap=1,request_limit=2,**kwargs)

    def test_pilot_no_zip_resume_and_full_fusion_then_arbiter(self):
        for role in ('qwen','bge'):
            self.assertEqual(self.worker(role,stop_after_queries=1)['state'],'paused')
        pilot=self.merge('pilot')
        self.assertEqual(pilot['state'],'pilot_diagnostics_only')
        self.assertFalse(list((self.root/'pilot').glob('*.zip')))
        for role in ('qwen','bge'):
            self.worker(role,stop_after_queries=None)
        calls=len(FixtureModel.calls)
        self.worker('qwen',stop_after_queries=None)
        self.assertEqual(len(FixtureModel.calls),calls)
        report=self.merge('full')
        self.assertEqual(report['state'],'full_export')
        self.assertEqual(report['shared_queries'],4)
        self.assertFalse(report['model_judgments_are_ground_truth'])
        with zipfile.ZipFile(self.root/'full/S0_baseline.zip') as z:
            self.assertEqual(z.read('submission.json'),self.raw)
        request=core.read(report['request_path'])
        self.assertEqual(request['scope'],'full')
        self.assertEqual(len(request['tasks']),4)
        self.assertEqual(self.worker('arbiter',request=request,stop_after_queries=None)['state'],'complete')
        report=self.merge('arbitrated',request=request)
        self.assertTrue(report['arbiter_complete'])
        for item in report['exports']:
            with zipfile.ZipFile(self.root/'arbitrated'/item['zip']) as z:
                rows=json.loads(z.read('submission.json'))
                self.assertEqual([r['id'] for r in rows],[1,2,3,4])
                self.assertEqual([r['relevant_docs'] for r in rows],[r['relevant_docs'] for r in self.rows])
        self.assertIn('Arbitrated_120_cap6.zip',[e['zip'] for e in report['exports']])

    def test_shards_are_disjoint_and_mixed_shard_counts_rejected(self):
        for role in ('qwen','bge'):
            a=self.worker(role,shard_index=0,shard_count=2,stop_after_queries=None)
            b=self.worker(role,shard_index=1,shard_count=2,stop_after_queries=None)
            self.assertFalse(set(a['completed_query_ids'])&set(b['completed_query_ids']))
        self.assertEqual(self.merge('sharded')['state'],'full_export')
        self.worker('qwen',shard_count=1,stop_after_queries=None)
        with self.assertRaises(ValueError):
            self.merge('mixed')

    def test_wrong_pool_and_tampered_scores_are_rejected(self):
        for role in ('qwen','bge'):
            self.worker(role,stop_after_queries=None)
        qid=multi.worker_identity(self.plan,'qwen',0,1)
        path=multi.worker_directory(self.root,qid)/'query-1.json'
        value=core.read(path); value['scores'][0]['score']+=1; core.write(path,value)
        with self.assertRaisesRegex(ValueError,'changed'):
            self.merge('tampered')
        plan=copy.deepcopy(self.plan); plan['generation']['include_spans']=True
        with self.assertRaises(ValueError):
            multi.validate_plan(plan,self.inputs)

    def test_existing_qwen_scores_can_be_reused_without_model_load(self):
        old=self.root/'old-qwen'
        identity=core.run_identity(self.inputs,self.plan['generation'],self.plan['models']['qwen'])
        core.run_scoring(self.rows,self.queries,identity,old,self.root/'old-cache.sqlite3',
                         stop_after_queries=None,scorer_factory=FixtureModel)
        def forbidden(*args,**kwargs):
            raise AssertionError('Should import exact old scores without loading model')
        result=multi.score_worker(self.rows,self.queries,self.plan,self.root,'qwen',
            local_cache=self.root/'new-cache.sqlite3',stop_after_queries=None,reuse_qwen=old,scorer_factory=forbidden)
        self.assertEqual(result['state'],'complete')
        path=multi.worker_directory(self.root,result['identity'])/'query-1.json'
        self.assertIsNotNone(core.read(path)['imported_from'])

    def test_fusion_uses_ranks_and_arbiter_only_reorders_requested_slots(self):
        candidates=[dict(candidate_id=c) for c in 'abcde']
        a=[5.,4.,3.,2.,1.]; b=[1.,2.,3.,4.,5.]
        first=multi.fused_scores(candidates,a,b)
        scaled=multi.fused_scores(candidates,[x*1000 for x in a],[x/100 for x in b])
        self.assertEqual(first,scaled)
        result=multi.reorder_disputed_slots(candidates,dict(zip('abcde',a)),['b','d'],[1.,2.])
        order=sorted(range(5),key=lambda i:-result[i])
        self.assertEqual(''.join('abcde'[i] for i in order),'adcbe')
        with self.assertRaises(ValueError):
            multi.reorder_disputed_slots(candidates,dict(zip('abcde',a)),['x'],[1.])

    def test_notebooks_embed_matching_sources(self):
        root=Path(__file__).resolve().parents[1]
        base=(root/'scripts/qwen_submission_trial.py').read_text(encoding='utf-8')
        support=(root/'scripts/multimodel_evidence.py').read_text(encoding='utf-8')
        for name in ['stage9_multimodel_worker_colab.ipynb','stage9_multimodel_merge_colab.ipynb']:
            nb=core.read(root/'colab'/name)
            for c in nb['cells']:
                if c['cell_type']!='code':
                    continue
                tree=ast.parse(''.join(c['source']))
                if c['id']=='helpers':
                    self.assertEqual(ast.literal_eval(tree.body[0].value),base)
                    self.assertEqual(ast.literal_eval(tree.body[1].value),support)

    def test_bge_forward_api_with_tiny_random_cpu_model(self):
        try:
            import torch
            from transformers import XLMRobertaConfig,XLMRobertaForSequenceClassification
        except ImportError:
            self.skipTest('Optional model libraries not installed')
        config=XLMRobertaConfig(vocab_size=64,hidden_size=16,intermediate_size=32,num_hidden_layers=1,
            num_attention_heads=2,max_position_embeddings=32,num_labels=1,pad_token_id=0)
        model=XLMRobertaForSequenceClassification(config).eval()
        class Padder:
            def pad(self,features,**kwargs):
                n=max(len(f['input_ids']) for f in features)
                return {k:torch.tensor([f[k]+[0]*(n-len(f[k])) for f in features])
                        for k in ('input_ids','attention_mask')}
        scorer=multi.ModelScorer.__new__(multi.ModelScorer)
        scorer.is_bge=True; scorer.torch=torch; scorer.model=model; scorer.device=torch.device('cpu'); scorer.tokenizer=Padder()
        features=[dict(input_ids=[2,4,3],attention_mask=[1,1,1]),dict(input_ids=[2,5,6,3],attention_mask=[1,1,1,1])]
        values=scorer._batch_scores(features)
        with torch.inference_mode():
            expected=model(**scorer.tokenizer.pad(features),return_dict=True).logits.reshape(-1).tolist()
        for a,b in zip(values,expected):
            self.assertAlmostEqual(a,b,places=6)


if __name__=='__main__':
    unittest.main()
