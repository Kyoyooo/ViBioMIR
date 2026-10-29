"""Explicitly provisional CPU silver experiments on a frozen, bounded corpus.

Never writes production stores, scores the official 1,200 questions or promotes
assistant labels into human-reviewed gold. Existing human benchmark gates stay intact.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import gc
import importlib.metadata
import json
import math
from pathlib import Path
import resource
import shutil
import sqlite3
import time

from .chunk_batches import read_json
from .chunk_models import load_tokenizer
from .chunking import chunk_document, settings as chunk_settings
from .icd import (RuleTagger, annotate, bounded_segments, load_taxonomy, prototypes,
                  semantic_candidates, settings as icd_settings)
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .lexical_stage7 import Analyzer
from .retrieval_contracts import distinct, open_run, require, signed
from .retrieval_evaluation import PROFILES, paired_bootstrap, score
from .review_common import write_csv
from .stage9_core import PINNED_RERANKER, fuse, select
from .stage9_pipeline import verify_value
from .stage10_benchmark import prepare


class Lexical:
    """Exact bounded-corpus global BM25 and cosine TF-IDF, including repeated query terms."""
    def __init__(self, chunks, analyzer):
        self.chunks, self.analyzer = chunks, analyzer
        self.counts = [Counter(analyzer(c['retrieval_text'])) for c in chunks]
        self.df = Counter(term for counts in self.counts for term in counts)
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / len(chunks) if chunks else 0
        self.idf = {t: math.log((len(chunks)+1)/(df+1))+1 for t,df in self.df.items()}
        self.norms = [math.sqrt(sum((v*self.idf[t])**2 for t,v in c.items())) for c in self.counts]

    def search(self, query, method):
        q = Counter(self.analyzer(query)); output = []
        qnorm = math.sqrt(sum((v*self.idf[t])**2 for t,v in q.items() if t in self.idf))
        for index, counts in enumerate(self.counts):
            if method == 'bm25':
                value = sum(v*math.log(1+(len(self.chunks)-self.df[t]+.5)/(self.df[t]+.5))*counts[t]*2.2 /
                    (counts[t]+1.2*(.25+.75*self.lengths[index]/self.average))
                    for t,v in q.items() if counts[t]) if self.average else 0
            else:
                require(method == 'tfidf', 'Unknown lexical method')
                value = sum(v*counts[t]*self.idf[t]**2 for t,v in q.items() if counts[t])
                value = value/(qnorm*self.norms[index]) if qnorm and self.norms[index] else 0
            output.append(value)
        return output


def ranked(chunks, scores, k):
    require(len(chunks) == len(scores) and all(math.isfinite(float(x)) for x in scores), 'Invalid ranking scores')
    return [{**chunks[i], 'score':float(scores[i])} for i in sorted(range(len(chunks)),
                key=lambda i:(-float(scores[i]),chunks[i]['chunk_id']))[:k]]


def lexical_ranked(chunks, scores, k):
    # Zero lexical matches produce an empty result, not arbitrary tied documents.
    return [r for r in ranked(chunks,scores,k) if r['score'] > 0]


def fusion(lexical, dense, *, kind='rrf', alpha=.5, extra_dense=(), k=50):
    c = {'fusion':kind,'rrf_k':60,'lexical_alpha':alpha,'fused_top_k':k}
    branches = [({'name':'bm25','family':'lexical'},lexical),({'name':'bge','family':'dense'},dense)]
    branches.extend(({'name':f'facet_{i}','family':'dense','facet_id':f'facet_{i}'},rows) for i,rows in enumerate(extra_dense))
    merged = fuse(branches,c)
    evidence = {r['chunk_id']:r for _,rows in branches for r in rows}
    return [{**evidence[r['chunk_id']],**r,'score':r['fused_score']} for r in merged]


def prediction(query_id, candidates, limits, *, deduplicate=True):
    if deduplicate:
        result,_ = select(candidates,[r['score'] for r in candidates],limits)
    else:
        docs = list(dict.fromkeys(c['doc_id'] for c in candidates))[:limits['max_docs']]
        rows,counts = [],Counter()
        for r in candidates:
            if r['doc_id'] in docs and counts[r['doc_id']] < limits['max_chunks_per_doc']:
                rows.append(r);counts[r['doc_id']] += 1
                if len(rows) == limits['max_chunks']: break
        result = {'relevant_docs':docs,'relevant_chunks':[{'doc_id':r['doc_id'],'chunk_text':r['chunk_text'],'chunk_order':i} for i,r in enumerate(rows)]}
    return {'id':query_id,**result}


def cached_encode(encoder, texts, db, *, semantic=False):
    import numpy as np
    registry = encoder.registry
    policy = {k:registry.get(k) for k in ('model_id','revision','pooling','compute_dtype','window_policy')}
    keys = [text_hash(canonical_json([policy,{'semantic_atoms':semantic},text])) for text in texts]
    values, missing = {}, {}
    for key,text in zip(keys,texts):
        if key in values or key in missing: continue
        row = db.execute('SELECT vector FROM vectors WHERE key=?',(key,)).fetchone()
        if row:
            vector = np.frombuffer(row[0],dtype='float32').copy()
            require(len(vector) == encoder.spec['dimension'] and np.isfinite(vector).all() and abs(float(np.linalg.norm(vector))-1)<1e-4,
                    'Cached vector is corrupt')
            values[key] = vector
        else: missing[key] = text
    pending = list(missing)
    for start in range(0,len(pending),16):
        wave = pending[start:start+16]
        method = encoder.encode_sentences if semantic else encoder.encode
        vectors,_ = method([missing[key] for key in wave])
        require(len(vectors) == len(wave), 'Encoder omitted texts')
        for key,vector in zip(wave,vectors):
            vector = np.asarray(vector,dtype='float32')
            require(vector.shape == (encoder.spec['dimension'],) and np.isfinite(vector).all() and abs(float(np.linalg.norm(vector))-1)<1e-4,
                    'Encoder returned invalid vectors')
            values[key] = vector; db.execute('INSERT INTO vectors VALUES (?,?)',(key,vector.tobytes()))
        db.commit()
    return np.asarray([values[key] for key in keys],dtype='float32')


def source_validate(corpus, predictions):
    docs = {p['doc_id']:p for p in corpus['documents']}
    for row in predictions:
        require(type(row['id']) is int and len(row['relevant_docs']) == len(set(row['relevant_docs'])), 'Invalid result IDs')
        require(all(type(i) is int and i in docs for i in row['relevant_docs']), 'Unknown result document')
        for chunk in row['relevant_chunks']:
            require(chunk['doc_id'] in row['relevant_docs'] and isinstance(chunk['chunk_text'],str) and chunk['chunk_text'].strip(), 'Invalid evidence/parent')
            require(any(chunk['chunk_text'] in r['text'] for r in docs[chunk['doc_id']]['regions']), 'Fabricated or non-contiguous evidence')


def pareto(rows):
    """Both metrics remain separate; no invented official combined score."""
    return [name for name,value in rows.items() if not any(
        other['document']['f2'] >= value['document']['f2'] and other['chunk']['f2'] >= value['chunk']['f2'] and
        (other['document']['f2'] > value['document']['f2'] or other['chunk']['f2'] > value['chunk']['f2'])
        for other in rows.values())]


def validate(root):
    root = Path(root); corpus = verify_value(read_json(root/'CORPUS.json')); benchmark = verify_value(read_json(root/'benchmark.json'))
    manifest = read_json(root/'experiments'/'manifest.json')
    require(manifest['state'] == 'complete_silver_pilot', 'S10 pilot is not complete')
    require(manifest['signature'] == text_hash(canonical_json(manifest['identity'])) and
            manifest['identity']['corpus_sha256'] == file_hash(root/'CORPUS.json') and
            manifest['identity']['benchmark_sha256'] == file_hash(root/'benchmark.json'), 'S10 inputs changed')
    require(all(file_hash(Path(__file__).with_name(n)) == sha for n,sha in manifest['identity']['code'].items()),
            'S10 inference code changed after execution')
    require(benchmark['label_tier'] == 'silver' and benchmark['human_reviewed'] is False, 'Silver and human benchmark paths must stay separate')
    ids = [q['id'] for q in benchmark['queries']]
    for name,sha in manifest['outputs'].items(): require(file_hash(root/'experiments'/name) == sha, 'S10 output changed: '+name)
    for run in manifest['runs']:
        results = read_json(root/'experiments'/'runs'/f'{run}.json')
        require([r['id'] for r in results] == ids, 'Run query coverage differs')
        source_validate(corpus,results)
    report = {'validation':'passed','scope':'bounded silver pilot only','human_reviewed':False,
              'queries':len(ids),'documents':len(corpus['documents']),'runs':len(manifest['runs']),
              'source_evidence_verified':True,'manifest_sha256':file_hash(root/'experiments'/'manifest.json'),
              'production_configuration_changed':False,'human_acceptance':'pending'}
    write_json(root/'experiments'/'VALIDATION.json',report)
    return report


def run(config_path='configs/experiments_stage10.json', *, accept_silver=False):
    import numpy as np
    from .stage10_models import CPUEncoder, CPUReranker
    require(accept_silver is True, 'Explicit --accept-silver required; these labels are not human-reviewed gold')
    c = read_json(config_path); root = Path(c['root']); output = root/'experiments'
    require(c.get('schema') == 'vibiomir-stage10-pilot-v1' and type(c['threads']) is int and 1<=c['threads']<=8,
            'Invalid pilot config/threads')
    require(type(c['candidate_k']) is int and type(c['rerank_k']) is int and 0<c['rerank_k']<=c['candidate_k'], 'Invalid candidate budgets')
    corpus = verify_value(read_json(root/'CORPUS.json')); benchmark = verify_value(read_json(root/'benchmark.json'))
    require(benchmark['corpus_signature'] == corpus['signature'] and benchmark['human_reviewed'] is False and benchmark['label_tier']=='silver', 'Benchmark/corpus/tier differs')
    require(len(corpus['documents']) <= 100 and len(benchmark['queries']) <= 30, 'CPU pilot cap: at most 100 documents and 30 queries')
    distinct(output,c['model_cache'],c['tokenizer_dir'])
    names = ['stage10_experiments.py','stage10_models.py','stage10_benchmark.py','stage9_core.py','stage9_reranker.py',
             'retrieval_evaluation.py','chunking.py','dense_models.py','dense_tokenizer_compat.py','icd.py','lexical_stage7.py']
    identity = {'operation':'stage10-silver-cpu-pilot','config':c,'corpus_sha256':file_hash(root/'CORPUS.json'),
                'benchmark_sha256':file_hash(root/'benchmark.json'),'code':{n:file_hash(Path(__file__).with_name(n)) for n in names},
                'chunk_config_sha256':file_hash(c['chunk_config']),'icd_config_sha256':file_hash(c['icd_config']),
                'taxonomy_sha256':file_hash(read_json(c['icd_config'])['taxonomy_path']),
                'runtime':{name:importlib.metadata.version(name) for name in ('torch','transformers','tokenizers','numpy','faiss-cpu')}}
    state = open_run(output,identity)
    if state['state']=='complete_silver_pilot': return validate(root)
    if c.get('feature_cache_import') and not (output/'features.sqlite3').exists():
        source=c['feature_cache_import'];distinct(output,source['path'])
        require(file_hash(source['path'])==source['sha256'] and identity['runtime']==source['runtime'],
                'Imported cache checksum/runtime differs; use the original cache environment')
        shutil.copy2(source['path'],output/'features.sqlite3')
    with writer_lock(output), sqlite3.connect(output/'features.sqlite3') as cache:
        started = time.perf_counter()
        cache.execute('CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY,vector BLOB NOT NULL)')
        cache.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        (output/'runs').mkdir(exist_ok=True); (output/'candidates').mkdir(exist_ok=True)
        questions = benchmark['queries']; packets = corpus['documents']; settings = chunk_settings(read_json(c['chunk_config']))
        tokenizer,_ = load_tokenizer(c['tokenizer_dir']); stores = {}; timings = {}; registries = {}
        bge = CPUEncoder('bge_m3',c['model_cache'],threads=c['threads'])
        registries['bge_m3'] = bge.registry
        def encode(texts): return cached_encode(bge,texts,cache)
        def semantic_encode(texts): return cached_encode(bge,texts,cache,semantic=True)
        configs = {'structure':('structure',settings),'fixed':('fixed',settings),'semantic':('semantic',settings),
                   'target256':('structure',{**settings,'target_tokens':256}),
                   'overlap0':('structure',{**settings,'overlap_tokens':0})}
        for name,(strategy,config) in configs.items():
            tick = time.perf_counter(); path = output/f'chunks_{name}.json'
            if path.exists():
                saved = verify_value(read_json(path));require(saved['run_signature']==state['signature'],'Chunk checkpoint belongs to another run')
                chunks = saved['chunks']
            else:
                chunks = []
                for index,p in enumerate(packets):
                    row = chunk_document(p,tokenizer,config,strategy,text_hash(canonical_json([state['signature'],name])),
                                         encoder=semantic_encode if strategy=='semantic' else None)
                    chunks.extend({**x,'source_batch':0,'source_row':index,'chunk_order':i,
                                   'retrieval_text_sha256':text_hash(x['retrieval_text'])} for i,x in enumerate(row['chunks']))
                write_json(path,signed({'run_signature':state['signature'],'chunks':chunks}))
            require(chunks and {x['doc_id'] for x in chunks}=={p['doc_id'] for p in packets},'Some pilot documents have no chunks')
            stores[name]=chunks;timings['chunk_'+name]=time.perf_counter()-tick
            print(json.dumps({'phase':'chunks','strategy':name,'chunks':len(chunks)}),flush=True)
        # Dense retrieval and ICD use the same public BGE model, loaded only once.
        base = stores['structure'];tick=time.perf_counter()
        doc_vectors=encode([x['retrieval_text'] for x in base]);query_vectors=encode([q['query'] for q in questions])
        dense_matrix=query_vectors@doc_vectors.T;timings['bge_encoding_and_exact_search']=time.perf_counter()-tick
        facet_lists={};facet_audit={}
        for qi,q in enumerate(questions):
            marker=q['query'].find(' và ')
            if marker>=0:
                parts=[(0,marker),(marker+4,len(q['query']))]
                facet_audit[str(q['id'])]=[{'start':a,'end':b,'text':q['query'][a:b]} for a,b in parts]
                facet_lists[q['id']]=[ranked(base,v@doc_vectors.T,c['candidate_k']) for v in encode([q['query'][a:b] for a,b in parts])]
        ic=icd_settings(c['icd_config']);taxonomy=load_taxonomy(ic['taxonomy_path']);tagger=RuleTagger(taxonomy,ic)
        proto=prototypes(taxonomy);pv=encode([x[2] for x in proto]);annotations={};qannotations={}
        tick=time.perf_counter()
        def topic(p):
            segments=bounded_segments(p,tokenizer,ic)
            semantic=semantic_candidates(segments,encode([s['text'] for s in segments]),proto,pv) if segments else []
            return annotate(p,tagger.tag(p),semantic,ic,'combined',state['signature'])
        for p in packets: annotations[p['doc_id']]=topic(p)
        for q in questions:
            p={'doc_id':q['id'],'title':q['query'],'regions':[],'derived_version_id':text_hash(q['query'])}
            qannotations[q['id']]=topic(p)
        timings['icd_annotation']=time.perf_counter()-tick
        write_json(output/'icd_annotations.json',{'documents':annotations,'queries':qannotations,'scope':'uncalibrated pilot topics; no human ICD gold'})
        write_json(output/'facet_spans.json',{'queries':facet_audit,'clinical_intent_verified':False,'original_query_always_retained':True})
        # Bounded ANN probe over the same pilot vectors; never mislabel it full-index ANN recall.
        import faiss
        faiss.omp_set_num_threads(c['threads'])
        nlist=max(1,min(8,len(base)//40));ann=faiss.IndexIVFFlat(faiss.IndexFlatIP(doc_vectors.shape[1]),doc_vectors.shape[1],nlist,faiss.METRIC_INNER_PRODUCT)
        ann.cp.seed=c['seed'];ann.train(doc_vectors);ann.add(doc_vectors)
        k=min(200,len(base));exact_ids=np.argsort(-dense_matrix,axis=1,kind='stable')[:,:k]
        probes={}
        for nprobe in sorted({1,nlist}):
            ann.nprobe=nprobe;tick=time.perf_counter();_,ids=ann.search(query_vectors,k)
            probes[str(nprobe)]={'recall_at_k':sum(len(set(a)&set(b))/k for a,b in zip(exact_ids,ids))/len(questions),'seconds':time.perf_counter()-tick}
        parallel={}
        def search_one(vector):return ranked(base,vector@doc_vectors.T,c['candidate_k'])
        reference=None
        for workers in (1,2,3):
            tick=time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:results=list(pool.map(search_one,query_vectors))
            ids=[[r['chunk_id'] for r in row] for row in results]
            if reference is None:reference=ids
            require(ids==reference,'Parallel exact search changed ranking')
            parallel[str(workers)]={'seconds':time.perf_counter()-tick,'same_order_as_one_worker':True}
        write_json(output/'operations.json',{'ann':{'scope':'60-document pilot, IVF-Flat; not production IVF-PQ','k':k,'nlist':nlist,'nprobe':probes},
                   'parallel_search':parallel,'seed':c['seed'],'restart':'immutable chunk checkpoints + model/text/policy vector cache + signed outputs'})
        del bge;gc.collect()
        tick=time.perf_counter();mbert=CPUEncoder('mbert',c['model_cache'],threads=c['threads']);registries['mbert']=mbert.registry
        mv=cached_encode(mbert,[x['retrieval_text'] for x in base],cache)
        mq=cached_encode(mbert,[q['query'] for q in questions],cache);mbert_scores=mq@mv.T
        timings['mbert_encoding_and_exact_search']=time.perf_counter()-tick
        del mbert;gc.collect()
        analyzer=Analyzer('pyvi_jieba_v1');lex={name:Lexical(rows,analyzer) for name,rows in stores.items()}
        candidates={};run_candidates=defaultdict(list);latencies=defaultdict(list);runs=defaultdict(list)
        limits=c['selection']
        def save(name,q,rows,*,caps=None,deduplicate=True):
            tick=time.perf_counter();runs[name].append(prediction(q['id'],rows,caps or limits,deduplicate=deduplicate))
            run_candidates[name].append({'id':q['id'],'doc_ids':list(dict.fromkeys(x['doc_id'] for x in rows)),
                                         'chunks':[{'doc_id':x['doc_id'],'chunk_text':x['chunk_text']} for x in rows]})
            latencies[name].append(time.perf_counter()-tick)
        for qi,q in enumerate(questions):
            bm=lexical_ranked(base,lex['structure'].search(q['query'],'bm25'),c['candidate_k'])
            tf=lexical_ranked(base,lex['structure'].search(q['query'],'tfidf'),c['candidate_k'])
            dense=ranked(base,dense_matrix[qi],c['candidate_k'])
            mb=ranked(base,mbert_scores[qi],c['candidate_k'])
            rrf=fusion(bm,dense,k=c['candidate_k']);candidates[q['id']]=rrf
            for name,rows in [('E01_tfidf',tf),('E02_bm25',bm),('E03_mbert',mb),('E04_bge',dense),('E05_rrf',rrf)]:save(name,q,rows)
            for alpha in (.25,.5,.75):save(f'E06_weighted_{alpha}',q,fusion(bm,dense,kind='weighted',alpha=alpha,k=c['candidate_k']))
            for strategy in ('fixed','semantic'):
                save('E08_bm25_'+strategy,q,lexical_ranked(stores[strategy],lex[strategy].search(q['query'],'bm25'),c['candidate_k']))
            for method in ('rules','semantic','combined'):
                qlabels=set(qannotations[q['id']]['annotations'][method]['labels'])
                boosted=[{**x,'score':x['score']*(1+.1*bool(qlabels & set(annotations[x['doc_id']]['annotations'][method]['labels'])))} for x in rrf]
                boosted.sort(key=lambda x:(-x['score'],x['chunk_id']));save('E09_icd_'+method,q,boosted)
            save('E10_facets',q,fusion(bm,dense,extra_dense=facet_lists.get(q['id'],()),k=c['candidate_k']))
            save('E10_no_dedup',q,rrf,deduplicate=False)
            for cap in (1,10):save(f'E11_doccap_{cap}',q,rrf,caps={**limits,'max_docs':cap})
            for cap in (5,20):save(f'E11_chunkcap_{cap}',q,rrf,caps={**limits,'max_chunks':cap})
            for k in (10,20):save(f'E11_pool_{k}',q,rrf[:k])
            for variant in ('target256','overlap0'):
                save('E11_bm25_'+variant,q,lexical_ranked(stores[variant],lex[variant].search(q['query'],'bm25'),c['candidate_k']))
        # Score all candidates in the declared small pool; no labels influence inference.
        reranker_policy={**PINNED_RERANKER,'max_pair_tokens':512,'query_window_tokens':192,'window_overlap':32,
                         'aggregation':'mean_query_windows_of_max_passage_logit'}
        tick=time.perf_counter();reranker=CPUReranker(reranker_policy,c['model_cache'],threads=c['threads'])
        registries['reranker']=reranker.registry
        for q in questions:
            rows=candidates[q['id']][:c['rerank_k']]
            scores=reranker.score(q['query'],[x['retrieval_text'] for x in rows],cache)
            rescored=[{**x,'score':s['score']} for x,s in zip(rows,scores)]
            rescored.sort(key=lambda x:(-x['score'],x['chunk_id']))
            save('E07_reranker',q,rescored)
            save('E07_same_pool_rrf',q,rows)
            print(json.dumps({'phase':'rerank','query_id':q['id'],'pairs':len(rows)}),flush=True)
        timings['reranking']=time.perf_counter()-tick
        del reranker;gc.collect()
        for name,results in runs.items():
            source_validate(corpus,results);write_json(output/'runs'/f'{name}.json',results)
            write_json(output/'candidates'/f'{name}.json',run_candidates[name])
        write_json(output/'models.json',registries)
        # Only dev silver labels choose the provisional frontier. Holdout is reported after this freeze.
        def evaluate(name,split,profile):
            ids={q['id'] for q in questions if q['split']==split}
            result=score(benchmark,[q for q in runs[name] if q['id'] in ids],split=split,profile=profile,allow_unjudged_holdout=True)
            result.update(label_tier='silver',human_reviewed=False,
                precision_interpretation='proxy against proposed positives; all non-positive retrieved documents remain unjudged',
                recall_interpretation='coverage of proposed source-conditioned positives only')
            return result
        dev={name:evaluate(name,'dev',PROFILES[0]) for name in runs}
        frontier=pareto({name:r['macro'] for name,r in dev.items()})
        frozen=signed({'split':'dev','label_tier':'silver','benchmark_sha256':file_hash(root/'benchmark.json'),
            'frontier':frontier,'all_run_names':sorted(runs),'production_selection':'unchanged',
            'selection_policy':'both F2 metrics kept separate; silver frontier is diagnostic, not a release decision'})
        freeze_path=output/'SILVER_DEV_FREEZE.json'
        if freeze_path.exists():require(read_json(freeze_path)==frozen,'Dev freeze changed; use a new version')
        write_json(freeze_path,frozen)
        reports={};summary=[]
        for split in ('train','dev','holdout'):
            reports[split]={}
            for profile in PROFILES:
                evaluated=dev if split=='dev' and profile==PROFILES[0] else {name:evaluate(name,split,profile) for name in runs}
                reports[split][profile]=evaluated
                for name,r in evaluated.items():
                    summary.append({'run':name,'split':split,'profile':profile,**{f'{level}_{metric}':value for level,m in r['macro'].items() for metric,value in m.items()},
                        'unjudged_documents':r['unjudged_documents_retrieved'],'label_tier':'silver','human_reviewed':False})
        paired={name:paired_bootstrap(dev['E05_rrf'],r,repetitions=c['bootstrap'],seed=c['seed']) for name,r in dev.items() if name!='E05_rrf'}
        errors=[]
        for name in runs:
            by_id={row['id']:row for row in run_candidates[name]}
            for q in questions:
                pool=by_id[q['id']];prediction_row=next(x for x in runs[name] if x['id']==q['id'])
                positive=q['relevant_docs'][0]
                reason='known_positive_retained' if positive in prediction_row['relevant_docs'] else 'selection_loss' if positive in pool['doc_ids'] else 'candidate_loss'
                errors.append({'run':name,'query_id':q['id'],'language':q['gold_languages'][0],'category':reason,
                               'human_review_status':'pending','missing_acquisition_in_production':q['gold_languages']==['en']})
        write_json(output/'SCORES.json',{'label_tier':'silver','human_reviewed':False,'scores':reports,'dev_paired_bootstrap':paired,
                   'holdout_freeze_sha256':file_hash(freeze_path),'official_scorer':False})
        write_json(output/'ERRORS.json',errors)
        fields=['run','split','profile','document_precision','document_recall','document_f2','chunk_precision','chunk_recall','chunk_f2','unjudged_documents','label_tier','human_reviewed']
        write_csv(output/'SUMMARY.csv',fields,summary)
        elapsed=time.perf_counter()-started
        write_json(output/'COST.json',{'seconds_this_invocation':elapsed,'phase_seconds':timings,
                   'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'gpu_hours':0,'device':'cpu',
                   'threads':c['threads'],'selection_only_latency_seconds':{n:{'p50':float(np.quantile(v,.5)),'p95':float(np.quantile(v,.95))} for n,v in latencies.items()},
                   'latency_scope':'selection-only latencies exclude encoding/search; model/index phase costs reported separately',
                   'raw_chunk_counts':{n:len(v) for n,v in stores.items()},'production_scope':'not measured here'})
        closure={'state':'complete_silver_pilot','experiments':{
            'E01':'TF-IDF','E02':'BM25','E03':'pinned mBERT exact dense','E04':'pinned BGE-M3 exact dense',
            'E05':'RRF','E06':'weighted alpha 0.25/0.5/0.75','E07':'real pinned CPU reranker plus same-pool control',
            'E08':'fixed/structure/semantic with same corpus','E09':'ICD off/rules/semantic/combined soft boost',
            'E10':'original/additional source slices and dedup off/on','E11':'target/overlap/candidate/output cap pilots',
            'E12':'pilot exact/IVF-Flat + 1/2/3 CPU search threads + reusable caches'},
            'scope':'30 assistant silver questions; 60 selected documents; exact counts in COST.json',
            'human_reviewed':False,'clinical_qa':'pending human review','icd_300_document_audit':'pending human review',
            'production_ann_recall':'not established by this small IVF-Flat probe',
            'full_corpus_quality':'not established','competition_training_eligible':False,
            'production_configuration_changed':False,'official_submission_ready':False,
            'silver_dev_frontier':frontier,'next':'replace with reviewed labels; rerun comparisons before production selection/S11 release'}
        write_json(output/'CLOSEOUT.json',closure)
        outputs={p.relative_to(output).as_posix():file_hash(p) for p in output.rglob('*') if p.is_file() and p.suffix in {'.json','.csv'} and p.name not in {'manifest.json','VALIDATION.json'}}
        state.update(state='complete_silver_pilot',runs=sorted(runs),queries=len(questions),documents=len(packets),outputs=outputs,
                     human_qa='pending',selection_is_final=False,production_configuration_changed=False)
        write_json(output/'manifest.json',state)
    return validate(root)


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('prepare');p.add_argument('--root',default='artifacts/stage10_silver_v1')
    p.add_argument('--seeds',default='configs/stage10_silver_questions.json')
    p=commands.add_parser('run');p.add_argument('--config',default='configs/experiments_stage10.json');p.add_argument('--accept-silver',action='store_true')
    p=commands.add_parser('validate');p.add_argument('--root',default='artifacts/stage10_silver_v1')
    args=parser.parse_args()
    try:
        result=prepare(args.root,args.seeds) if args.command=='prepare' else run(args.config,accept_silver=args.accept_silver) if args.command=='run' else validate(args.root)
        print(json.dumps(result,indent=2))
    except KeyboardInterrupt:parser.exit(130,'Interrupted; repeat the same command to reuse source/model caches and chunks.\n')
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,ImportError) as exc:parser.exit(2,'S10: '+str(exc)+'\n')


if __name__=='__main__':main()
