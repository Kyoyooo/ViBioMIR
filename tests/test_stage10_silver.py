"""Offline silver-tier, evidence, scoring, cache and experiment control fixtures."""
import copy
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from vibiomir.io import file_hash, text_hash, write_json
from vibiomir.retrieval_contracts import signed
from vibiomir.retrieval_evaluation import compare_runs, score
from vibiomir.stage10_benchmark import compile_silver
from vibiomir.stage10_experiments import (Lexical, cached_encode, fusion, lexical_ranked, pareto,
                                         prediction, ranked, run, source_validate, validate)
from vibiomir.stage10_sources import article_packet, visible
from vibiomir.stage10_models import weight_file
from vibiomir.stage10_analysis import coverage


class SilverBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.docs=[];self.seeds={'human_reviewed':False,'author':'assistant_generated_silver','questions':[]}
        for i,lang,text in [(10,'vi','Không tự ý dùng 5 mg. Hỏi bác sĩ.'),(20,'zh','症状。应咨询医生。'),(30,'en','No fever. Ask a doctor.')]:
            self.docs.append({'doc_id':i,'derived_version_id':str(i),'review_language':lang,'url':f'https://example.org/{i}',
                              'regions':[{'region_id':'r','text':text,'source_start':11}]})
            a,b=text.split(' ',1) if lang!='zh' else ('症状。','应咨询医生。')
            self.seeds['questions'].append({'doc_id':i,'language':lang,'query':f'Câu hỏi {i}?','intent':['definition'],
                                            'begin':a,'end':b})
        self.corpus=signed({'documents':self.docs})

    def test_compiled_labels_remain_silver_and_exact_source_slices(self):
        b=compile_silver(self.corpus,self.seeds,expected=3)
        self.assertFalse(b['human_reviewed']);self.assertFalse(b['production_tuning_allowed'])
        self.assertFalse(b['competition_training_eligible']);self.assertEqual(b['label_tier'],'silver')
        for q,d in zip(b['queries'],self.docs):
            c=q['relevant_chunks'][0]
            self.assertEqual(c['chunk_text'],d['regions'][0]['text'][c['region_start']:c['region_end']])
            self.assertEqual(c['source_start'],11+c['region_start'])
            self.assertEqual(q['judged_docs'],[])
            self.assertEqual(c['reviewer'],'')

    def test_changed_quotes_duplicate_sources_or_human_claim_are_rejected(self):
        for mutate in (lambda x:x['questions'][0].update(begin='invented'),
                       lambda x:x['questions'].append(x['questions'][0]),
                       lambda x:x.update(human_reviewed=True)):
            s=copy.deepcopy(self.seeds);mutate(s)
            with self.assertRaises(ValueError):compile_silver(self.corpus,s,expected=len(s['questions']))

    def test_seed_language_and_corpus_signature_must_match(self):
        s=copy.deepcopy(self.seeds);s['questions'][0]['language']='en'
        with self.assertRaises(ValueError):compile_silver(self.corpus,s,expected=3)
        c=copy.deepcopy(self.corpus);c['documents'][0]['regions'][0]['text']+='changed'
        with self.assertRaises(ValueError):compile_silver(c,self.seeds,expected=3)

    def test_human_comparison_gate_is_not_weakened(self):
        b=compile_silver(self.corpus,self.seeds,expected=3)
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'benchmark.json';write_json(p,b)
            with self.assertRaisesRegex(ValueError,'human-reviewed'):
                compare_runs(p,{'a':directory,'b':directory},Path(directory)/'out',baseline='a')

    def test_full_offline_pipeline_controls_resume_and_output_integrity(self):
        class Tokenizer:
            def encode(self,text,add_special_tokens=True,**kwargs):return [0]* (len(text)+(2 if add_special_tokens else 0))
            def __call__(self,text,**kwargs):return {'offset_mapping':[(i,i+1) for i in range(len(text))]}
        class Encoder:
            spec={'dimension':4}
            def __init__(self,model,*args,**kwargs):
                self.registry={'model_id':'fixture/'+model,'revision':'fixture','pooling':'CLS','compute_dtype':'float32','window_policy':{},'test_fixture':True}
            def encode(self,texts):
                values=[]
                for text in texts:
                    h=text_hash(text);v=np.array([int(h[i:i+2],16)+1 for i in (0,2,4,6)],dtype='float32');values.append(v/np.linalg.norm(v))
                return np.asarray(values),[1]*len(values)
            encode_sentences=encode
        class Reranker:
            registry={'test_fixture':True}
            def __init__(self,*args,**kwargs):pass
            def score(self,query,texts,cache):return [{'score':float(len(t)),'windows':1} for t in texts]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);docs=copy.deepcopy(self.docs)
            for d in docs:
                d.update(title='Fixture',parent_version_id=None,availability='content',language_hint=d['review_language'])
                for r in d['regions']:
                    r.update(source_kind='fixture',source_locator_json='{}',units=[{'start':0,'end':len(r['text']),'kind':'p','is_heading':False,'heading':[]}])
            corpus=signed({'documents':docs});benchmark=compile_silver(corpus,self.seeds,expected=3)
            write_json(root/'CORPUS.json',corpus);write_json(root/'benchmark.json',benchmark)
            c=json.loads(Path('configs/experiments_stage10.json').read_text());c.update(root=str(root),model_cache=str(root/'models'),tokenizer_dir=str(root/'tokenizer'),bootstrap=10)
            cfg=root/'config.json';write_json(cfg,c)
            with patch('vibiomir.stage10_models.CPUEncoder',Encoder),patch('vibiomir.stage10_models.CPUReranker',Reranker),\
                 patch('vibiomir.stage10_experiments.load_tokenizer',return_value=(Tokenizer(),'fixture')),\
                 patch('vibiomir.stage10_experiments.Analyzer',return_value=str.split):
                first=run(cfg,accept_silver=True);second=run(cfg,accept_silver=True)
            self.assertEqual(first,second);self.assertEqual(first['validation'],'passed');self.assertFalse(first['human_reviewed'])
            self.assertGreaterEqual(first['runs'],20)
            p=root/'experiments/runs/E05_rrf.json';rows=json.loads(p.read_text());rows[0]['relevant_docs'].append(999);write_json(p,rows)
            with self.assertRaisesRegex(ValueError,'output changed'):validate(root)

    def test_web_markup_preserves_comparisons_and_rejects_missing_abstract(self):
        self.assertEqual(visible('<h4>Result</h4>P<0.01 &amp; 5 mg.<script>bad</script>'),'Result\nP<0.01 & 5 mg.')
        with self.assertRaises(ValueError):article_packet({'id':'1','title':'title'},1,'https://pubmed.ncbi.nlm.nih.gov/1/')

    def test_source_validation_rejects_rewritten_or_orphan_chunks(self):
        valid=[{'id':1,'relevant_docs':[10],'relevant_chunks':[{'doc_id':10,'chunk_text':'Không tự ý dùng 5 mg.'}]}]
        source_validate(self.corpus,valid)
        valid[0]['relevant_chunks'][0]['chunk_text']='Không tự ý dùng 50 mg.'
        with self.assertRaises(ValueError):source_validate(self.corpus,valid)
        valid[0]['relevant_chunks'][0]['doc_id']=20
        with self.assertRaises(ValueError):source_validate(self.corpus,valid)


class ExperimentMathTest(unittest.TestCase):
    def setUp(self):
        self.chunks=[{'chunk_id':str(i),'doc_id':i,'retrieval_text':text,'chunk_text':text,
            'derived_version_id':'v','region_id':'r','region_start':0,'region_end':len(text),
            'source_batch':0,'source_row':i,'chunk_order':0,'retrieval_text_sha256':text_hash(text)}
            for i,text in enumerate(['a a b','b c'],1)]

    def test_bm25_matches_hand_calculation_and_cosine_self_query_is_one(self):
        e=Lexical(self.chunks,str.split)
        expected=math.log(1+(2-1+.5)/(1+.5))*2*2.2/(2+1.2*(.25+.75*3/2.5))
        self.assertAlmostEqual(e.search('a','bm25')[0],expected)
        self.assertAlmostEqual(e.search('a a b','tfidf')[0],1)
        self.assertEqual(lexical_ranked(self.chunks,e.search('missing','bm25'),50),[])

    def test_fusion_family_caps_ties_and_candidate_limits(self):
        bm=ranked(self.chunks,[2,1],2);dense=ranked(self.chunks,[0,1],2)
        rows=fusion(bm,dense,k=1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['chunk_id'],'1')
        r=fusion(bm,dense,extra_dense=(dense,dense),k=2)
        for row in r:
            self.assertAlmostEqual(sum(b['weight'] for b in row['branches'] if b['family']=='dense'),.5)

    def test_pareto_keeps_both_metrics_without_a_combined_score(self):
        def row(d,c):return {'document':{'f2':d},'chunk':{'f2':c}}
        self.assertEqual(set(pareto({'a':row(.9,.4),'b':row(.4,.9),'c':row(.3,.3)})),{'a','b'})

    def test_cache_reuses_duplicate_text_and_separates_model_revisions(self):
        class Encoder:
            registry={'model_id':'fake','revision':'a','pooling':'CLS','compute_dtype':'float32','window_policy':{}}
            spec={'dimension':2}
            calls=0
            def encode(self,texts):
                self.calls+=1;return np.array([[1,0]]*len(texts),dtype='float32'),[1]*len(texts)
        e=Encoder()
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE vectors(key TEXT PRIMARY KEY,vector BLOB NOT NULL)')
            self.assertEqual(cached_encode(e,['one','one'],db).shape,(2,2));self.assertEqual(e.calls,1)
            cached_encode(e,['one'],db);self.assertEqual(e.calls,1)
            e.registry={**e.registry,'revision':'b'};cached_encode(e,['one'],db);self.assertEqual(e.calls,2)
            db.execute('UPDATE vectors SET vector=?',(np.array([0,0],dtype='float32').tobytes(),))
            with self.assertRaises(ValueError):cached_encode(e,['one'],db)

    def test_output_caps_are_independent_and_silver_opt_in_is_required(self):
        rows=ranked(self.chunks,[2,1],2)
        p=prediction(1,rows,{'max_docs':1,'max_chunks':2,'max_chunks_per_doc':1,'overlap_iou_threshold':.8})
        self.assertEqual(p['relevant_docs'],[1]);self.assertEqual(len(p['relevant_chunks']),1)
        with self.assertRaisesRegex(ValueError,'accept-silver'):run(accept_silver=False)

    def test_boolean_and_fabricated_result_doc_ids_fail(self):
        c={'documents':[{'doc_id':1,'regions':[{'text':'a a b'}]}]}
        for doc in (True,99):
            with self.assertRaises(ValueError):source_validate(c,[{'id':1,'relevant_docs':[doc],'relevant_chunks':[]}])

    def test_pinned_snapshot_weight_formats_are_explicit(self):
        self.assertEqual(weight_file({'model_id':'BAAI/bge-m3'}),'pytorch_model.bin')
        self.assertEqual(weight_file({'model_id':'BAAI/bge-reranker-v2-m3'}),'model.safetensors')

    def test_semantic_separator_atoms_use_separate_nontruncating_cache_policy(self):
        class Encoder:
            registry={'model_id':'fake','revision':'a','pooling':'CLS','compute_dtype':'float32','window_policy':{}}
            spec={'dimension':2}
            def encode(self,texts):
                if any(not t.strip() for t in texts):raise ValueError('Empty retrieval input')
                return np.array([[1,0]]*len(texts),dtype='float32'),[1]*len(texts)
            def encode_sentences(self,texts):
                return np.array([[0,1]]*len(texts),dtype='float32'),[1]*len(texts)
        e=Encoder()
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE vectors(key TEXT PRIMARY KEY,vector BLOB NOT NULL)')
            np.testing.assert_equal(cached_encode(e,['\n'],db,semantic=True),[[0,1]])
            with self.assertRaises(ValueError):cached_encode(e,['\n'],db)

    def test_candidate_vs_selection_loss_uses_independent_gold_spans(self):
        q={'id':1,'split':'dev','gold_languages':['vi'],'relevant_docs':[1],'judged_docs':[],
           'relevant_chunks':[{'doc_id':1,'chunk_text':'a b c d e'}]}
        p={'id':1,'relevant_docs':[1],'relevant_chunks':[{'doc_id':1,'chunk_text':'unrelated'}]}
        pool={'doc_ids':[1],'chunks':[{'doc_id':1,'chunk_text':'a b'}]}
        r=coverage(q,pool,p,'whitespace_proxy_v1')
        self.assertEqual(r['candidate_chunk_recall'],1)
        self.assertEqual(r['category'],'chunk_selection_loss')
        pool['chunks'][0]['chunk_text']='a'
        self.assertEqual(coverage(q,pool,p,'whitespace_proxy_v1')['category'],'candidate_evidence_context_loss')


if __name__=='__main__':unittest.main()
