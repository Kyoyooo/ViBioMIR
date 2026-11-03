"""Parallel evidence rerankers, rank fusion and optional disagreement arbitration.

All labels are model judgments. Exact source scope is the input submission only.
"""
import math
from pathlib import Path
import sqlite3
import zipfile

try:
    from . import qwen_submission_trial as core
except ImportError:
    import qwen_submission_trial as core


def policies():
    qwen = core.model_policy()
    return dict(qwen=qwen,
        bge=dict(model_id='BAAI/bge-reranker-v2-m3',
            revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e', commit_date='2024-06-24T14:08:45Z',
            max_pair_tokens=4096, truncation='reject', scoring='sequence_classification_logit',
            dtype='float16', attention='sdpa', template_version='tokenizer-pair-v1'),
        arbiter={**qwen, 'model_id': 'Qwen/Qwen3-Reranker-4B',
            'revision': '22e683669bc0f0bd69640a1354a6d0aebcfeede5', 'commit_date': '2026-04-16T08:56:07Z'})


def build_plan(inputs, generation):
    core.validate_generation(generation)
    return core.signed(dict(schema='multimodel-evidence-v1', inputs=inputs, generation=generation,
        models=policies(), code_sha256=core.file_hash(__file__), base_code_sha256=core.file_hash(core.__file__),
        scope='input-submission-evidence-only', ground_truth=False))


def validate_plan(plan, inputs):
    core.checked(plan)
    core.require(plan == build_plan(inputs, plan['generation']), 'Input, model or support code differs from plan')


def worker_identity(plan, role, shard_index, shard_count, request=None):
    core.require(role in plan['models'] and type(shard_index) is int and type(shard_count) is int
        and 1 <= shard_count <= 16 and 0 <= shard_index < shard_count, 'Invalid role/shard assignment')
    core.require((role == 'arbiter') == (request is not None), 'Arbiter needs a verified disagreement request')
    return dict(operation='multimodel-evidence-worker', plan_signature=plan['signature'], role=role,
                shard_index=shard_index, shard_count=shard_count,
                request_signature=request['signature'] if request else None)


def worker_directory(root, identity):
    scope = identity['request_signature'] or 'all'
    return Path(root) / 'workers' / identity['role'] / scope / (
        f"part-{identity['shard_index']}-of-{identity['shard_count']}")


def bge_pair(tokenizer, query, passage, policy):
    features = dict(tokenizer(query, passage, add_special_tokens=True, truncation=False,
                              return_token_type_ids=False))
    core.require(len(features['input_ids']) <= policy['max_pair_tokens'],
                 f"BGE pair exceeds {policy['max_pair_tokens']} tokens; use a new larger-budget plan")
    return features


class ModelScorer(core.QwenScorer):
    """Same score() batching for two architectures; independent model inputs/registries."""
    def __init__(self, policy, batch_size=8, padded_tokens=8192):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer
        core.require(policy in policies().values(), 'Use a pinned ensemble model policy')
        core.require(torch.cuda.is_available(), 'Select CUDA GPU runtime')
        core.require(batch_size > 0 and padded_tokens >= policy['max_pair_tokens'], 'Invalid GPU budgets')
        self.torch, self.device, self.policy = torch, torch.device('cuda'), policy
        self.batch_size, self.padded_tokens = batch_size, padded_tokens
        self.is_bge = policy['model_id'] == policies()['bge']['model_id']
        self.tokenizer = AutoTokenizer.from_pretrained(policy['model_id'], revision=policy['revision'],
            trust_remote_code=False, padding_side='right' if self.is_bge else 'left')
        loader = AutoModelForSequenceClassification if self.is_bge else AutoModelForCausalLM
        self.model = loader.from_pretrained(policy['model_id'], revision=policy['revision'],
            torch_dtype=torch.float16, attn_implementation='sdpa', trust_remote_code=False,
            use_safetensors=True).to(self.device).eval()
        core.require(getattr(self.model.config, '_commit_hash', None) == policy['revision'], 'Loaded revision differs')
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.yes = self.tokenizer.encode('yes', add_special_tokens=False)
        self.no = self.tokenizer.encode('no', add_special_tokens=False)
        if self.is_bge:
            core.require(self.model.config.num_labels == 1, 'BGE requires one classification logit')
        else:
            core.require(len(self.yes) == len(self.no) == 1 and self.yes != self.no, 'Unexpected yes/no token IDs')
        count = sum(p.numel() for p in self.model.parameters())
        context = self.model.config.max_position_embeddings - (2 if self.is_bge else 0)
        core.require(0 < count <= 15_000_000_000 and policy['max_pair_tokens'] <= context, 'Model/context limit exceeded')
        self.registry = dict(model_id=policy['model_id'], revision=policy['revision'],
            tokenizer_revision=policy['revision'], commit_date=policy['commit_date'], parameters=count,
            dtype=policy['dtype'], scoring=policy['scoring'], torch=torch.__version__,
            transformers=transformers.__version__, gpu=torch.cuda.get_device_name(0))

    def score(self, query, passages):
        if not self.is_bge:
            return super().score(query, passages)
        features = [bge_pair(self.tokenizer, query, p, self.policy) for p in passages]
        scores, index, budget = [], 0, self.batch_size
        while index < len(features):
            n = min(budget, len(features)-index)
            while n > 1 and max(len(v['input_ids']) for v in features[index:index+n])*n > self.padded_tokens:
                n -= 1
            try:
                values = self._batch_scores(features[index:index+n])
            except self.torch.cuda.OutOfMemoryError:
                self.torch.cuda.empty_cache()
                if n == 1:
                    raise
                budget = max(1, n//2)
                print('CUDA OOM: retry BGE batch', budget, flush=True)
                continue
            scores.extend(values); index += n
        return scores

    def _batch_scores(self, features):
        if not self.is_bge:
            return super()._batch_scores(features)
        inputs = {k: v.to(self.device) for k,v in self.tokenizer.pad(features,padding=True,return_tensors='pt').items()}
        with self.torch.inference_mode():
            values = self.model(**inputs, return_dict=True).logits.reshape(-1).float().cpu().tolist()
        core.require(len(values) == len(features) and all(math.isfinite(x) for x in values), 'Invalid BGE logits')
        return values


def verify_record(record, row, query, candidates, identity, policy):
    # Compatible score envelope, but worker identity binds the model/plan/shard separately.
    return core.verify_score_record(record, row, query, candidates, core.signature(identity), policy)


def tasks_for(rows, queries, plan, identity, request=None):
    lookup = {t['id']: t for t in request['tasks']} if request else None
    tasks = []
    for index, (row, query) in enumerate(zip(rows, queries)):
        if index % identity['shard_count'] != identity['shard_index']:
            continue
        if lookup is not None and row['id'] not in lookup:
            continue
        candidates = core.make_candidates(row, plan['generation'])
        if lookup is not None:
            t = lookup[row['id']]
            core.require(t['candidates_sha256'] == core.signature(candidates), 'Arbiter candidate set differs')
            allowed = set(t['requested_candidate_ids'])
            candidates = [c for c in candidates if c['candidate_id'] in allowed]
            core.require(len(candidates) == len(allowed), 'Unknown arbitration candidate')
        if candidates:
            tasks.append((row, query, candidates))
    return tasks


def validate_request(request, plan, root):
    core.checked(request)
    core.require(request['plan_signature'] == plan['signature'] and request['schema'] == 'disagreement-request-v1',
                 'Request belongs to another plan')
    core.require(len({t['id'] for t in request['tasks']}) == len(request['tasks']), 'Duplicate request query')
    for t in request['tasks']:
        core.require(len(set(t['requested_candidate_ids'])) == len(t['requested_candidate_ids']), 'Duplicate request candidate')
        for source in t['source_records']:
            path = (Path(root) / source['path']).resolve()
            core.require(Path(root).resolve() in path.parents and core.file_hash(path) == source['sha256'],
                         'Input model scores changed since arbitration request')


def import_old_qwen(directory, row, query, candidates, plan):
    """Reuse only proven identical query/passage/policy scores from the prior notebook."""
    if directory is None:
        return None
    directory = Path(directory)
    manifest = core.checked(core.read(directory / 'manifest.json'))
    old = manifest['identity']
    core.require(old['inputs'] == plan['inputs'] and old['generation'] == plan['generation'] and
        old['model_policy'] == plan['models']['qwen'] and manifest['run_signature'] == core.signature(old),
        'Existing Qwen run differs in input, spans or scoring policy; disable reuse or select matching run')
    path = directory / f"query-{row['id']}.json"
    if not path.is_file() or row['id'] not in manifest['completed_query_ids']:
        return None
    core.require(core.file_hash(path) == manifest['score_files'][str(row['id'])], 'Old Qwen score file changed')
    record = core.read(path)
    core.verify_score_record(record,row,query,candidates,manifest['run_signature'],plan['models']['qwen'])
    return record, dict(path=str(path), sha256=core.file_hash(path), old_run_signature=manifest['run_signature'])


def score_worker(rows, queries, plan, root, role, *, shard_index=0, shard_count=1,
                 request=None, stop_after_queries=20, local_cache=None, batch_size=8,
                 padded_tokens=8192, reuse_qwen=None, scorer_factory=None):
    core.require(stop_after_queries is None or type(stop_after_queries) is int and stop_after_queries>0, 'Invalid pilot limit')
    validate_plan(plan, plan['inputs'])
    if request:
        validate_request(request,plan,root)
    identity = worker_identity(plan,role,shard_index,shard_count,request)
    output = worker_directory(root,identity)
    output.mkdir(parents=True,exist_ok=True)
    manifest_path = output/'manifest.json'
    if manifest_path.exists():
        previous = core.checked(core.read(manifest_path))
        core.require(previous['identity'] == identity,'Worker identity differs')
    else:
        core.require(not any(output.iterdir()),'Nonempty worker directory has no manifest')
    tasks = tasks_for(rows,queries,plan,identity,request)
    policy = plan['models'][role]
    done, hashes, new, scorer = [], {}, 0, None
    def save(state):
        core.write(manifest_path,core.signed(dict(identity=identity,state=state,
            expected_query_ids=[r['id'] for r,_,_ in tasks],completed_query_ids=done,
            score_files=hashes,new_queries_last_invocation=new)))
    save('running')
    local_cache = Path(local_cache or Path(root)/f'cache-{role}.sqlite3')
    local_cache.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(local_cache) as db:
        db.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,score REAL NOT NULL)')
        try:
            for row,query,candidates in tasks:
                path=output/f"query-{row['id']}.json"
                if path.is_file():
                    verify_record(core.read(path),row,query,candidates,identity,policy)
                else:
                    imported = import_old_qwen(reuse_qwen,row,query,candidates,plan) if role=='qwen' else None
                    if imported:
                        old, provenance = imported
                        values, registry, provenance = [s['score'] for s in old['scores']],old['model_registry'],provenance
                    else:
                        texts = [core.evidence_text(row,c) for c in candidates]
                        keys=[core.cache_key(query['query'],text,policy) for text in texts]
                        unique = dict(zip(keys,texts)); known={}; missing=[]
                        for key in unique:
                            hit=db.execute('SELECT score FROM scores WHERE key=?',(key,)).fetchone()
                            if hit:
                                core.require(math.isfinite(hit[0]),'Invalid cached value')
                                known[key]=hit[0]
                            else:
                                missing.append(key)
                        if scorer is None:
                            scorer=(scorer_factory or ModelScorer)(policy,batch_size=batch_size,padded_tokens=padded_tokens)
                        if missing:
                            values=scorer.score(query['query'],[unique[k] for k in missing])
                            core.require(len(values)==len(missing) and all(type(s) in (int,float) and math.isfinite(s) for s in values),
                                         'Invalid model output')
                            for key,value in zip(missing,values):
                                db.execute('INSERT INTO scores VALUES(?,?)',(key,float(value))); known[key]=float(value)
                            db.commit()
                        values=[known[k] for k in keys]; registry=scorer.registry; provenance=None
                    record=core.signed(dict(id=row['id'],run_signature=core.signature(identity),
                        query_sha256=core.digest(query['query'].encode()),candidates_sha256=core.signature(candidates),
                        model_registry=registry,scores=[dict(candidate_id=c['candidate_id'],score=s) for c,s in zip(candidates,values)],
                        imported_from=provenance))
                    verify_record(record,row,query,candidates,identity,policy)
                    core.write(path,record); new+=1
                done.append(row['id']); hashes[str(row['id'])]=core.file_hash(path); save('running')
                print(f"{role} part {shard_index}: query {row['id']} complete {len(done)}/{len(tasks)}, new={new}",flush=True)
                if stop_after_queries is not None and new>=stop_after_queries and len(done)<len(tasks):
                    save('paused'); return core.read(manifest_path)
        except BaseException:
            save('interrupted'); raise
    save('complete')
    return core.read(manifest_path)


def collect_role(rows, queries, plan, root, role, request=None):
    scope = request['signature'] if request else 'all'
    manifests = sorted((Path(root)/'workers'/role/scope).glob('*/manifest.json'))
    records, shard_counts = {},set()
    by_id={r['id']:(r,q) for r,q in zip(rows,queries)}
    if request:
        validate_request(request,plan,root)
    for path in manifests:
        m=core.checked(core.read(path)); ident=m['identity']
        core.require(ident['role']==role and ident==worker_identity(plan,role,ident['shard_index'],ident['shard_count'],request),
                     'Worker plan/model/shard differs')
        core.require(path.parent.resolve()==worker_directory(root,ident).resolve(),'Unexpected shard directory')
        shard_counts.add(ident['shard_count'])
        expected={r['id']:cs for r,_,cs in tasks_for(rows,queries,plan,ident,request)}
        core.require(m['expected_query_ids']==list(expected) and len(set(m['completed_query_ids']))==len(m['completed_query_ids']),
                     'Worker query coverage differs')
        for qid in m['completed_query_ids']:
            core.require(qid in expected and qid not in records,'Unknown or duplicated query across shards')
            row,q=by_id[qid]; score_path=path.parent/f'query-{qid}.json'
            core.require(core.file_hash(score_path)==m['score_files'][str(qid)],'Score file changed')
            values=verify_record(core.read(score_path),row,q,expected[qid],ident,plan['models'][role])
            records[qid]=dict(candidates=expected[qid],scores=values,
                source=dict(path=score_path.relative_to(root).as_posix(),sha256=core.file_hash(score_path)))
    core.require(len(shard_counts)<=1,'Mixed shard counts: use one consistent shard_count per model')
    return records


def ranks(candidates,scores):
    core.require(len(candidates)==len(scores) and len({c['candidate_id'] for c in candidates})==len(candidates)
                 and all(math.isfinite(s) for s in scores),'Invalid fusion candidates/scores')
    order=sorted(range(len(candidates)),key=lambda i:(-scores[i],candidates[i]['candidate_id']))
    return {candidates[i]['candidate_id']:rank for rank,i in enumerate(order,1)}


def fused_scores(candidates, left, right, k=60):
    core.require(type(k) is int and k>0,'Positive RRF k required')
    a,b=ranks(candidates,left),ranks(candidates,right)
    return [1/(k+a[c['candidate_id']])+1/(k+b[c['candidate_id']]) for c in candidates]


def disagreement_ids(candidates,left,right, *, top_k=20,rank_gap=30,limit=40):
    a,b=ranks(candidates,left),ranks(candidates,right)
    bad=[c['candidate_id'] for c in candidates if
         (a[c['candidate_id']]<=top_k)!=(b[c['candidate_id']]<=top_k)
         or abs(a[c['candidate_id']]-b[c['candidate_id']])>=rank_gap]
    bad.sort(key=lambda key:(-int((a[key]<=top_k)!=(b[key]<=top_k)),-abs(a[key]-b[key]),min(a[key],b[key]),key))
    return bad[:limit]


def reorder_disputed_slots(candidates,base_scores,requested_ids,arbiter_scores):
    """Arbiter compares ONLY requested candidates; never grants missing-score bonuses."""
    by_id={c['candidate_id']:c for c in candidates}
    core.require(set(requested_ids)<=set(by_id) and len(set(requested_ids))==len(requested_ids)
                 and len(requested_ids)==len(arbiter_scores) and all(math.isfinite(s) for s in arbiter_scores),
                 'Invalid arbitration score coverage')
    order=sorted(by_id,key=lambda key:(-base_scores[key],key))
    slots=[i for i,key in enumerate(order) if key in set(requested_ids)]
    judged=sorted(zip(requested_ids,arbiter_scores),key=lambda x:(-x[1],x[0]))
    for index,(key,_) in zip(slots,judged):
        order[index]=key
    positions={key:i for i,key in enumerate(order)}
    return [float(len(order)-positions[c['candidate_id']]) for c in candidates]


def merge_and_export(rows,queries,plan,root,destination,baseline_raw, *, request=None,
                     top_k=20,rank_gap=30,request_limit=40):
    validate_plan(plan,plan['inputs'])
    core.require(core.digest(baseline_raw)==plan['inputs']['submission_json_sha256'],'Baseline differs')
    core.require(type(top_k) is int and top_k>0 and type(rank_gap) is int and rank_gap>0 and
                 type(request_limit) is int and request_limit>0,'Invalid disagreement budgets')
    qwen=collect_role(rows,queries,plan,root,'qwen'); bge=collect_role(rows,queries,plan,root,'bge')
    common={r['id'] for r in rows}&set(qwen)&set(bge)
    core.require(common,'No shared completed queries: run both qwen and bge workers first')
    full=len(common)==len(rows)
    arbitration=collect_role(rows,queries,plan,root,'arbiter',request) if request else {}
    request_tasks={t['id']:t for t in request['tasks']} if request else {}
    arbiter_full=request is not None and full and set(request_tasks)<=set(arbitration) and set(request['query_ids'])==common
    destination=Path(destination)
    core.require(not destination.exists(),'Choose a new merge destination')
    destination.mkdir(parents=True)
    variants=[dict(name='Qwen_80_cap2',kind='qwen',limits=dict(max_chunks=80,per_doc=2,overlap_iou=.8)),
              dict(name='BGE_80_cap2',kind='bge',limits=dict(max_chunks=80,per_doc=2,overlap_iou=.8)),
              dict(name='RRF_120_cap6',kind='rrf',limits=dict(max_chunks=120,per_doc=6,overlap_iou=.8)),
              dict(name='RRF_80_cap2',kind='rrf',limits=dict(max_chunks=80,per_doc=2,overlap_iou=.8))]
    if arbiter_full:
        variants += [dict(name='Arbitrated_120_cap6',kind='arbiter',limits=dict(max_chunks=120,per_doc=6,overlap_iou=.8))]
    predictions={v['name']:[] for v in variants}; stats={v['name']:[] for v in variants}
    tasks=[]; review_count=0
    with (destination/'DISAGREEMENT_REVIEW.jsonl').open('w',encoding='utf-8') as review, \
         (destination/'EVIDENCE_PROVENANCE.jsonl').open('w',encoding='utf-8') as provenance:
        for row,query in zip(rows,queries):
            qid=row['id']
            if qid not in common:
                continue
            candidates=qwen[qid]['candidates']
            core.require(candidates==bge[qid]['candidates'],'Models scored different candidate pools')
            a,b=qwen[qid]['scores'],bge[qid]['scores']
            rr=fused_scores(candidates,a,b)
            requested=disagreement_ids(candidates,a,b,top_k=top_k,rank_gap=rank_gap,limit=request_limit)
            if requested:
                tasks.append(dict(id=qid,candidates_sha256=core.signature(candidates),requested_candidate_ids=requested,
                                  source_records=[qwen[qid]['source'],bge[qid]['source']]))
                ra,rb=ranks(candidates,a),ranks(candidates,b)
                by_id={c['candidate_id']:c for c in candidates}
                review.write(core.canonical(dict(id=qid,query=query['query'],model_judgments_not_gold=True,
                    disputed_count=len(requested),examples=[dict(candidate=by_id[key],
                    text=core.evidence_text(row,by_id[key]),qwen_rank=ra[key],bge_rank=rb[key]) for key in requested[:5]]))+'\n')
                review_count+=1
            third=None
            if arbiter_full and qid in arbitration:
                task=request_tasks[qid]
                core.require(task['candidates_sha256']==core.signature(candidates),'Arbiter full-pool hash differs')
                subset=arbitration[qid]
                ids=[c['candidate_id'] for c in subset['candidates']]
                third=reorder_disputed_slots(candidates,dict(zip([c['candidate_id'] for c in candidates],rr)),ids,subset['scores'])
            for v in variants:
                values={'qwen':a,'bge':b,'rrf':rr,'arbiter':third if third is not None else rr}[v['kind']]
                pred,selected,decisions=core.select_evidence(row,candidates,values,v['limits'])
                core.require(pred['relevant_docs']==row['relevant_docs'],'Document list changed')
                predictions[v['name']].append(pred)
                before={(c['doc_id'],c['chunk_text']) for c in row['relevant_chunks']}
                after={(c['doc_id'],c['chunk_text']) for c in pred['relevant_chunks']}
                stats[v['name']].append(dict(id=qid,chunks=len(selected),changed=before!=after,
                    subspans=sum(c['kind']=='subspan' for c in selected)))
                provenance.write(core.canonical(dict(variant=v['name'],id=qid,selected=selected,decisions=decisions))+'\n')
    new_request=core.signed(dict(schema='disagreement-request-v1',plan_signature=plan['signature'],
        query_ids=[r['id'] for r in rows if r['id'] in common],scope='full' if full else 'pilot',
        rules=dict(top_k=top_k,rank_gap=rank_gap,max_candidates_per_query=request_limit),tasks=tasks))
    requests_dir=Path(root)/'requests'
    requests_dir.mkdir(parents=True,exist_ok=True)
    request_path=requests_dir/f"request-{new_request['signature']}.json"
    if request_path.exists():
        core.require(core.read(request_path)==new_request,'Existing request changed')
    else:
        core.write(request_path,new_request)
    exports=[]
    if full:
        with zipfile.ZipFile(destination/'S0_baseline.zip','w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('submission.json',baseline_raw)
        for v in variants:
            payload=core.canonical(predictions[v['name']]).encode()
            path=destination/(v['name']+'.zip')
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('submission.json',payload)
            with zipfile.ZipFile(path) as z:
                core.require(z.namelist()==['submission.json'] and z.read('submission.json')==payload,'Output ZIP differs')
            exports.append(dict(variant=v,zip=path.name,zip_sha256=core.file_hash(path),
                json_sha256=core.digest(payload),queries=len(rows),
                changed_queries=sum(s['changed'] for s in stats[v['name']]),
                chunks=sum(s['chunks'] for s in stats[v['name']]),subspans=sum(s['subspans'] for s in stats[v['name']])))
    report=dict(state='full_export' if full else 'pilot_diagnostics_only',plan_signature=plan['signature'],
        total_queries=len(rows),qwen_complete_queries=len(qwen),bge_complete_queries=len(bge),
        shared_queries=len(common),queries_with_disagreements=review_count,
        requested_pairs=sum(len(t['requested_candidate_ids']) for t in tasks),request_path=str(request_path),
        request_signature=new_request['signature'],arbiter_complete=arbiter_full,
        applied_arbitration_request_signature=request['signature'] if arbiter_full else None,
        original_corpus_verified=False,relevance_measured=False,official_lcs_evaluated=False,
        model_judgments_are_ground_truth=False,source_scope='exact slices of input submission only',
        source_inputs=plan['inputs'],baseline_json_sha256=core.digest(baseline_raw),exports=exports,
        pilot_statistics=None if full else stats)
    core.write(destination/'REPORT.json',report)
    return report
