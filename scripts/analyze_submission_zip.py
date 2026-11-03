"""Audit a submission's structure and evidence coverage without assuming relevance."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def describe(values):
    values = sorted(values)
    if not values:
        return None
    def p(q):
        x = (len(values)-1)*q/100
        i = int(x)
        return round(values[i] + (values[min(i+1,len(values)-1)]-values[i])*(x-i), 2)
    return dict(min=values[0], mean=round(statistics.mean(values),2), median=statistics.median(values),
                p90=p(90), p95=p(95), p99=p(99), max=values[-1])


def language(text):
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    vi = bool(re.search(r'[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]', text))
    if cjk:
        return 'cjk_and_vi' if vi else 'cjk_present'
    return 'vietnamese_diacritics' if vi else 'other_unclassified'


def audit(path, expected_ids):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if z.namelist() != ['submission.json']:
            raise ValueError('Expected exactly one root submission.json')
        payload = z.read('submission.json')  # ZIP CRC is checked while reading.
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise ValueError('Expected list of query results')
    errors, summaries = [], []
    chars, units = [], []
    lang_counts, per_doc_counts = Counter(), Counter()
    lang_chars, lang_units = defaultdict(list), defaultdict(list)
    text_docs, doc_text_unique = defaultdict(set), set()
    cjk_samples, longest, duplicates = [], [], []
    length_bands = Counter()
    short_thresholds, label_only_counts = Counter(), Counter()
    generic_labels = {'tin liên quan', 'có thể bạn quan tâm', 'sức khỏe', 'sống khỏe', 'phòng mạch online'}
    generic_label_examples = []
    repeated_same_doc, repeated_cross_doc = 0, 0
    contained_chunks = 0
    for row in rows:
        query_id = row.get('id')
        if set(row) != {'id', 'relevant_docs', 'relevant_chunks'}:
            errors.append(f'{query_id}: unexpected row schema')
        docs, chunks = row['relevant_docs'], row['relevant_chunks']
        if any(type(d) is not int for d in docs) or len(set(docs)) != len(docs):
            errors.append(f'{query_id}: noninteger or duplicate doc IDs')
        docset = set(docs)
        groups, texts, seen_pairs = defaultdict(list), defaultdict(set), set()
        query_duplicates, query_contains = 0, 0
        for index, chunk in enumerate(chunks):
            if set(chunk) != {'doc_id', 'chunk_text', 'chunk_order'}:
                errors.append(f'{query_id}: unexpected chunk schema')
            d, text = chunk['doc_id'], chunk['chunk_text']
            if type(d) is not int or d not in docset or not isinstance(text, str) or not text.strip():
                errors.append(f'{query_id}: invalid chunk parent/text')
                continue
            if type(chunk['chunk_order']) is not int or chunk['chunk_order'] != index:
                errors.append(f'{query_id}: chunk_order differs from sequential output order')
            digest = sha(text.encode())
            pair = (d, digest)
            if pair in seen_pairs:
                repeated_same_doc += 1
                query_duplicates += 1
            seen_pairs.add(pair)
            texts[digest].add(d)
            text_docs[digest].add(d)
            doc_text_unique.add(pair)
            groups[d].append(text)
            c, u = len(text), len(text.split())
            label = language(text)
            chars.append(c); units.append(u)
            for limit in (10, 30, 50, 100):
                if c < limit:
                    short_thresholds[str(limit)] += 1
            if text.strip().casefold() in generic_labels:
                label_only_counts[text.strip().casefold()] += 1
                if len(generic_label_examples) < 15:
                    generic_label_examples.append(dict(query_id=query_id, doc_id=d, text=text))
            lang_counts[label] += 1
            lang_chars[label].append(c); lang_units[label].append(u)
            for name,a,b in [('1-200',1,201),('201-500',201,501),('501-1000',501,1001),
                             ('1001-2000',1001,2001),('2001+',2001,float('inf'))]:
                if a <= c < b:
                    length_bands[name] += 1
                    break
            longest.append((c,query_id,d,index,label))
            if label.startswith('cjk') and len(cjk_samples)<8:
                cjk_samples.append(dict(query_id=query_id,doc_id=d,chunk_order=index,preview=text[:180]))
        repeated_cross_doc += sum(len(ds)-1 for ds in texts.values())
        for d, items in groups.items():
            per_doc_counts[len(items)] += 1
            for i,text in enumerate(items):
                if any(len(text)<len(other) and text in other for j,other in enumerate(items) if i!=j):
                    query_contains += 1
        contained_chunks += query_contains
        if query_duplicates or query_contains:
            duplicates.append(dict(id=query_id,same_doc_exact_duplicates=query_duplicates,contained_chunks=query_contains))
        summaries.append(dict(id=query_id,documents=len(docs),chunks=len(chunks),
                              documents_with_chunks=len(groups),documents_without_chunks=len(docset-set(groups)),
                              max_chunks_per_doc=max((len(v) for v in groups.values()),default=0),
                              cjk_chunks=sum(language(c['chunk_text']).startswith('cjk') for c in chunks),
                              mean_chunk_characters=round(statistics.mean([len(c['chunk_text']) for c in chunks]),2) if chunks else 0))
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)):
        errors.append('Duplicate query IDs')
    if ids!=expected_ids:
        errors.append('Query IDs/order differ from official query file')
    dococc=sum(r['documents'] for r in summaries)
    no_evidence=sum(r['documents_without_chunks'] for r in summaries)
    summary=dict(zip_path=str(path), zip_sha256=sha(Path(path).read_bytes()),
        submission_json_sha256=sha(payload), zip_bytes=Path(path).stat().st_size,
        json_bytes=len(payload),zip_members=[dict(name=i.filename,bytes=i.file_size) for i in infos],
        queries=len(rows),query_coverage_matches=ids==expected_ids,
        technical_errors=errors,technical_validation='passed' if not errors else 'failed',
        total_document_occurrences=dococc,total_chunk_occurrences=len(chars),
        unique_documents=len({d for r in rows for d in r['relevant_docs']}),
        unique_doc_text_pairs=len(doc_text_unique),unique_chunk_texts=len(text_docs),
        documents_per_query=describe([r['documents'] for r in summaries]),
        chunks_per_query=describe([r['chunks'] for r in summaries]),
        documents_with_chunks_per_query=describe([r['documents_with_chunks'] for r in summaries]),
        documents_without_chunks_per_query=describe([r['documents_without_chunks'] for r in summaries]),
        document_occurrences_without_chunks=no_evidence,
        document_occurrences_without_chunks_pct=round(no_evidence/dococc*100,2) if dococc else 0,
        query_count_with_uncovered_documents=sum(r['documents_without_chunks']>0 for r in summaries),
        chunks_per_covered_query_document=dict(sorted(per_doc_counts.items())),
        chunk_characters=describe(chars),chunk_whitespace_units=describe(units),
        chunk_character_bands=dict(length_bands),language_heuristics=dict(lang_counts),
        short_chunk_counts_below_characters=dict(short_thresholds),
        generic_label_only_occurrences=dict(label_only_counts),generic_label_examples=generic_label_examples,
        length_by_language={k:dict(characters=describe(lang_chars[k]),whitespace_units=describe(lang_units[k])) for k in lang_counts},
        same_doc_exact_duplicate_occurrences=repeated_same_doc,
        cross_doc_extra_identical_text_occurrences_within_query=repeated_cross_doc,
        strictly_contained_chunk_occurrences_same_doc_within_query=contained_chunks,
        unique_texts_appearing_in_multiple_documents=sum(len(ds)>1 for ds in text_docs.values()),
        empty_queries=sum(r['chunks']==0 for r in summaries),
        longest_chunks=[dict(characters=c,query_id=q,doc_id=d,chunk_order=i,language=l) for c,q,d,i,l in sorted(longest,reverse=True)[:15]],
        cjk_samples=cjk_samples,
        available_chunk_fields=['doc_id','chunk_text','chunk_order'],
        limitations=['No corpus source verification or official doc-ID allowlist verification performed.',
                    'No scores, candidate pools, original source offsets or model identity in this ZIP.',
                    'chunk_order is output sequence, not original document source order.',
                    'No gold labels or official LCS evaluation; length and language are not quality labels.',
                    'Language detection is a script heuristic, and whitespace units are not model tokens.',
                    'The reported score 0.1706 cannot be independently linked to this ZIP without a scoring receipt.'])
    return summary,summaries,duplicates


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,default=Path('submission_17.zip'))
    parser.add_argument('--query',type=Path,default=Path('query/query.parquet'))
    parser.add_argument('--output',type=Path,default=Path('reports/submission_17_analysis_20261101'))
    args=parser.parse_args()
    import pyarrow.parquet as pq
    expected=pq.read_table(args.query,columns=['id']).column('id').to_pylist()
    summary,rows,duplicates=audit(args.input,expected)
    summary['query_file_sha256']=sha(args.query.read_bytes())
    args.output.mkdir(parents=True,exist_ok=True)
    for name,value in [('SUMMARY.json',summary),('QUERY_STATS.json',rows),('DUPLICATE_REVIEW.json',duplicates)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
