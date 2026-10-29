"""Compile explicitly assistant-generated silver seeds without weakening human QA gates."""
from collections import Counter
from pathlib import Path

from .chunk_batches import read_json
from .io import canonical_json, file_hash, text_hash, write_json
from .review_common import write_csv
from .retrieval_contracts import require, signed
from .retrieval_evaluation import normalize
from .stage9_pipeline import verify_value


def compile_silver(corpus, seeds, *, expected=30):
    verify_value(corpus)
    require(seeds.get('human_reviewed') is False and seeds.get('author') == 'assistant_generated_silver',
            'This compiler accepts only explicitly declared assistant silver seeds')
    rows = seeds['questions']; docs = {p['doc_id']:p for p in corpus['documents']}
    require(len(rows) == expected and len(docs) == len(corpus['documents']), 'Wrong query count or duplicate corpus IDs')
    require(all(type(i) is int for i in docs), 'Integer document IDs required')
    questions, seen_text, seen_docs = [], set(), set()
    per_language = Counter()
    for index, row in enumerate(rows):
        doc_id = row.get('doc_id')
        if doc_id is None:
            url = 'https://pubmed.ncbi.nlm.nih.gov/' + str(row['pmid']) + '/'
            matches = [p['doc_id'] for p in docs.values() if p['url'] == url]
            require(len(matches) == 1, 'PubMed seed must resolve to one official document ID')
            doc_id = matches[0]
        require(doc_id in docs and doc_id not in seen_docs, 'Use a unique available positive source per pilot query')
        seen_docs.add(doc_id)
        packet = docs[doc_id]; language = row['language']
        require(language in {'vi','en','zh'} and packet['review_language'] == language, 'Seed/source language mismatch')
        query = row['query']; normalized = normalize(query)
        require(normalized and normalized not in seen_text, 'Empty/duplicate normalized query')
        seen_text.add(normalized)
        occurrences = []
        for region in packet['regions']:
            a = region['text'].find(row['begin'])
            if a < 0: continue
            b = region['text'].find(row['end'], a + len(row['begin']))
            if b >= 0: occurrences.append((region,a,b))
        require(len(occurrences) == 1, f'Silver seed needs one exact source occurrence: {doc_id}')
        region,a,b = occurrences[0]
        while b > a and region['text'][b-1].isspace(): b -= 1
        text = region['text'][a:b]
        require(text.strip(), 'Empty source quote')
        span = {'doc_id':doc_id,'derived_version_id':packet['derived_version_id'],'region_id':region['region_id'],
                'region_start':a,'region_end':b,'source_start':region['source_start']+a,
                'source_end':region['source_start']+b,'chunk_text':text,'text_sha256':text_hash(text),
                'annotator':'assistant_generated_silver','reviewer':'','human_reviewed':False}
        lang_index = per_language[language]; per_language[language] += 1
        split = ('train','dev','holdout')[(lang_index + {'vi':0,'zh':1,'en':2}[language]) % 3]
        questions.append({'id':100001+index,'query':query,'split':split,'case_group':f'source_{doc_id}',
            'origin_doc_ids':[doc_id],'author':'assistant_generated_silver','human_reviewed':False,
            'vietnamese_confirmed':False,'source_verified':False,
            'source_integrity':'exact substring checked by code; no human relevance/clinical verification',
            'gold_languages':[language],'intent':row['intent'],'icd':[],
            'gold_domains':[packet['url'].split('/')[2]],'tags':['silver','source_conditioned'],
            'source_url':packet['url'],'relevant_docs':[doc_id],'relevant_chunks':[span],
            'judged_docs':[], 'proposed_positive_docs':[doc_id]})
    if expected == 30:
        require(dict(per_language) == {'vi':10,'zh':10,'en':10}, 'Pilot requires 10 VI / 10 EN / 10 ZH sources')
        require(Counter(q['split'] for q in questions) == {'train':10,'dev':10,'holdout':10}, 'Pilot split accounting differs')
    return signed({'format':'vibiomir-silver-benchmark-v1','label_tier':'silver','human_reviewed':False,
        'annotation_origin':'assistant-generated questions and proposed positives; no clinician/reviewer impersonation',
        'competition_training_eligible':False,'production_tuning_allowed':False,
        'corpus_signature':corpus['signature'],'queries':questions,
        'gold_scope':'one proposed source-conditioned positive per question; other documents unjudged, not negatives',
        'external_english_scope':'official-ID PubMed supplements exist only in this bounded S10 corpus',
        'human_replacement':'create a new reviewed benchmark version; preserve this silver snapshot'})


def prepare(root, seeds_path='configs/stage10_silver_questions.json'):
    root = Path(root); corpus = read_json(root / 'CORPUS.json')
    result = compile_silver(corpus, read_json(seeds_path))
    result = signed({k:v for k,v in result.items() if k != 'signature'} | {'seeds_sha256':file_hash(seeds_path)})
    path = root / 'benchmark.json'
    if path.exists(): require(read_json(path) == result, 'Silver labels changed; choose a new benchmark root/version')
    write_json(path,result)
    fields = ['query_id','doc_id','source_url','proposed_relevant','human_relevant','annotator','reviewer','status','reason']
    target = root / 'human_review_template.csv'
    if not target.exists():
        write_csv(target, fields, ({'query_id':q['id'],'doc_id':q['relevant_docs'][0],
            'source_url':q['source_url'],'proposed_relevant':'yes','human_relevant':'',
            'annotator':'','reviewer':'','status':'pending','reason':''} for q in result['queries']))
    return {'state':'complete_silver_only','queries':len(result['queries']),'human_reviewed':False,
            'signature':result['signature']}
