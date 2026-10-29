"""Source-sealed silver analysis and review queues, separate from frozen inference."""
import argparse
from collections import Counter
from pathlib import Path

from .chunk_batches import read_json
from .io import canonical_json, file_hash, text_hash, write_json
from .retrieval_contracts import require, signed
from .retrieval_evaluation import score_query
from .review_common import write_csv
from .stage10_experiments import validate


def coverage(query, pool, selected, profile):
    predicted_pool={'relevant_docs':pool['doc_ids'],'relevant_chunks':[
        {'doc_id':x['doc_id'],'chunk_text':x['chunk_text']} for x in pool['chunks']]}
    before=score_query(query,predicted_pool,profile)
    after=score_query(query,selected,profile)
    if before['document']['recall']==0:category='candidate_document_loss'
    elif before['chunk']['recall']==0:category='candidate_evidence_context_loss'
    elif after['document']['recall']==0:category='document_selection_loss'
    elif after['chunk']['recall']==0:category='chunk_selection_loss'
    else:category='known_silver_evidence_retained'
    return {'query_id':query['id'],'split':query['split'],'language':query['gold_languages'][0],
            'candidate_document_recall':before['document']['recall'],
            'candidate_chunk_recall':before['chunk']['recall'],
            'selected_document_recall':after['document']['recall'],
            'selected_chunk_recall':after['chunk']['recall'],'category':category,
            'candidate_judged_at_output':before['judged_at_output'],
            'all_labels':'silver proposals; none independently human-judged'}


def analyze(root='artifacts/stage10_silver_v1', output_dir='reports/stage10_silver_20261029'):
    root,output=Path(root),Path(output_dir);validation=validate(root)
    run_root=root/'experiments';manifest=read_json(run_root/'manifest.json')
    require(all(file_hash(Path(__file__).with_name(name))==sha for name,sha in manifest['identity']['code'].items()),
            'Inference code changed after the frozen run; retain the matching source version')
    benchmark=read_json(root/'benchmark.json');queries=benchmark['queries'];by_id={q['id']:q for q in queries}
    all_coverage={};review_pairs=set();proposed={}
    for q in queries:
        for doc in q['relevant_docs']:review_pairs.add((q['id'],doc));proposed[q['id'],doc]=True
    for name in manifest['runs']:
        pools=read_json(run_root/'candidates'/f'{name}.json');selected=read_json(run_root/'runs'/f'{name}.json')
        require([r['id'] for r in pools]==[r['id'] for r in selected]==[q['id'] for q in queries], 'Pool/result coverage differs')
        profiles={}
        for profile in ('whitespace_proxy_v1','cjk_character_sensitivity_v1'):
            profiles[profile]=[coverage(by_id[p['id']],p,s,profile) for p,s in zip(pools,selected)]
        all_coverage[name]=profiles
        for row in pools:review_pairs.update((row['id'],doc) for doc in row['doc_ids'])
    score_reports=read_json(run_root/'SCORES.json')['scores']
    slice_metrics={}
    dimensions={
        'query_characters':{q['id']:'<=128' if len(q['query'])<=128 else '129..256' if len(q['query'])<=256 else '>256' for q in queries},
        'source_declared_multi_intent':{q['id']:'multiple' if len(q.get('intent',[]))>1 else 'single' for q in queries}}
    for split,profiles in score_reports.items():
        slice_metrics[split]={}
        for profile,runs in profiles.items():
            slice_metrics[split][profile]={}
            for name,scores in runs.items():
                sliced={}
                for dimension,labels in dimensions.items():
                    groups={}
                    for row in scores['per_query']:groups.setdefault(labels[row['id']],[]).append(row)
                    sliced[dimension]={label:{'queries':len(rows),'macro':{
                        level:{metric:sum(r[level][metric] for r in rows)/len(rows) for metric in ('precision','recall','f2')}
                        for level in ('document','chunk')}} for label,rows in groups.items()}
                slice_metrics[split][profile][name]=sliced
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':'vibiomir-stage10-silver-analysis-v1','label_tier':'silver','human_reviewed':False,
        'scope':'30 source-conditioned assistant questions / 60 selected documents, including 20 S10-only English abstracts',
        'inference_validation':validation,'benchmark_sha256':file_hash(root/'benchmark.json'),
        'inference_manifest_sha256':file_hash(run_root/'manifest.json'),'analysis_code_sha256':file_hash(__file__),
        'queries':len(queries),'documents':validation['documents'],'runs':len(manifest['runs']),
        'question_languages':dict(Counter(q['gold_languages'][0] for q in queries)),
        'splits':dict(Counter(q['split'] for q in queries)),
        'coverage':all_coverage,'additional_slices':slice_metrics,
        'slice_policy':'Unicode character-count bins; multi-intent inferred only from assistant-declared intent arrays, not clinical review',
        'cost':read_json(run_root/'COST.json'),'operations':read_json(run_root/'operations.json'),
        'closeout':read_json(run_root/'CLOSEOUT.json'),
        'limitations':['source-conditioned silver positives, not exhaustive relevance judgments',
            'unjudged output documents are not established negatives; precision/F2 are proxy values',
            '60-document extended corpus differs from the existing production corpus; results are not full-index quality',
            'official tokenizer/scorer parity is unresolved; both proxy profiles reported',
            'clinical QA, ICD 300-document audit and independently reviewed holdout remain pending',
            'CPU FP32 pilot is not a claim of CUDA FP16 numerical equivalence',
            'assistant-generated labels are excluded from competition training and production configuration selection'],
        'production_configuration_changed':False,'human_qa_passed':False,'official_submission_ready':False}
    target=output/'RESULTS.json'
    if target.exists():require(read_json(target)==report,'Analysis differs; version the report rather than replacing it')
    write_json(target,report)
    write_json(output/'COVERAGE.json',all_coverage)
    summary=[]
    for name,profiles in all_coverage.items():
        for profile,rows in profiles.items():
            for split in ('train','dev','holdout'):
                chosen=[r for r in rows if r['split']==split]
                summary.append({'run':name,'profile':profile,'split':split,'queries':len(chosen),
                    **{key:sum(r[key] for r in chosen)/len(chosen) for key in ('candidate_document_recall','candidate_chunk_recall','selected_document_recall','selected_chunk_recall')}})
    write_csv(output/'COVERAGE_SUMMARY.csv',list(summary[0]),summary)
    reviews=root/'human_review';reviews.mkdir(exist_ok=True)
    doc_fields=['query_id','doc_id','proposed_positive','judged','relevant','annotator','reviewer','reason']
    p=reviews/'pooled_documents_template.csv'
    if not p.exists():write_csv(p,doc_fields,({'query_id':q,'doc_id':d,'proposed_positive':'yes' if proposed.get((q,d)) else '',
        'judged':'no','relevant':'','annotator':'','reviewer':'','reason':''} for q,d in sorted(review_pairs)))
    p=reviews/'proposed_spans.json'
    if not p.exists():write_json(p,[{'query_id':q['id'],'query':q['query'],'source_url':q['source_url'],
        'proposed_spans':q['relevant_chunks'],'human_review_status':'pending'} for q in queries])
    p=reviews/'REVIEW_MANIFEST.json'
    review=signed({'benchmark_sha256':file_hash(root/'benchmark.json'),'candidate_pool_manifest_sha256':file_hash(run_root/'manifest.json'),
        'document_pairs':len(review_pairs),'independent_review_minimum_fraction':.2,'human_reviewed':False,
        'instructions':'Copy templates to working files; do not replace proposed labels with human approval automatically. Preserve assistant origin. New human labels need their own version.'})
    if p.exists():require(read_json(p)==review,'Review pool changed; use a new review directory')
    write_json(p,review)
    return {'state':'complete_silver_pilot','validation':validation['validation'],'queries':len(queries),
            'runs':len(manifest['runs']),'review_pairs':len(review_pairs),'human_reviewed':False,'report':str(target)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='artifacts/stage10_silver_v1')
    parser.add_argument('--output-dir',default='reports/stage10_silver_20261029')
    args=parser.parse_args()
    try:
        import json
        print(json.dumps(analyze(args.root,args.output_dir),indent=2))
    except (ValueError,OSError,KeyError,TypeError) as exc:parser.exit(2,str(exc)+'\n')
