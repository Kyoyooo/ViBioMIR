"""Compare exported evidence against a baseline without inferring relevance."""
import argparse
from collections import Counter
import json
from pathlib import Path
import zipfile


def read(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read('submission.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('submission_17.zip'))
    parser.add_argument('--input', type=Path, default=Path('RRF_80_cap2.zip'))
    parser.add_argument('--output', type=Path, default=Path('reports/rrf_80_cap2_analysis_20261102'))
    args = parser.parse_args()
    old = {row['id']: row for row in read(args.baseline)}
    rows, counts = [], Counter()
    for row in read(args.input):
        previous = old[row['id']]
        original = {(c['doc_id'], c['chunk_text']) for c in previous['relevant_chunks']}
        selected = {(c['doc_id'], c['chunk_text']) for c in row['relevant_chunks']}
        exact = len(selected & original)
        spans = sum(any(d == c['doc_id'] and c['chunk_text'] in text for d, text in original)
                    for c in row['relevant_chunks'] if (c['doc_id'], c['chunk_text']) not in original)
        covered_old = {c['doc_id'] for c in previous['relevant_chunks']}
        covered_new = {c['doc_id'] for c in row['relevant_chunks']}
        same_docs = row['relevant_docs'] == previous['relevant_docs']
        item = dict(id=row['id'], document_list_identical=same_docs,
                    baseline_chunks=len(previous['relevant_chunks']), chunks=len(row['relevant_chunks']),
                    exact_baseline_chunks=exact, new_subspans=spans,
                    unmatched_chunks=len(row['relevant_chunks'])-exact-spans,
                    baseline_chunks_removed=len(original-selected),
                    documents_losing_all_evidence=len(covered_old-covered_new),
                    documents_gaining_evidence=len(covered_new-covered_old))
        rows.append(item)
        counts['queries'] += 1
        counts['identical_document_lists'] += same_docs
        for key in ('exact_baseline_chunks', 'new_subspans', 'unmatched_chunks',
                    'baseline_chunks_removed', 'documents_losing_all_evidence', 'documents_gaining_evidence'):
            counts[key] += item[key]
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('COMPARISON.json', dict(counts)), ('QUERY_COMPARISON.json', rows)]:
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(counts), indent=2))


if __name__ == '__main__':
    main()
