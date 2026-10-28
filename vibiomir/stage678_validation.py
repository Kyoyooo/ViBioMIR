"""Technical validation of the S06/S07/S08 baseline, without relevance labels."""
import argparse
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

import numpy as np

from .chunk_batches import batches, read_json
from .dense_index import ready
from .dense_stage8 import read_plan
from .icd_pipeline import code_identity, source_manifest
from .io import canonical_json, file_hash, text_hash, write_json
from .retrieval_contracts import checked, queries, require


def manifest_identity(value):
    require(value['signature'] == text_hash(canonical_json(value['identity'])), 'Manifest identity checksum differs')


def icd_snapshot(input_dir, output_dir):
    source = source_manifest(input_dir)
    root = Path(output_dir)
    manifest, report = read_json(root / 'manifest.json'), read_json(root / 'VALIDATION.json')
    manifest_identity(manifest)
    identity = manifest['identity']
    require(identity['mode'] == 'rules' and manifest['state'] == 'complete' and report['validation'] == 'passed', 'Completed validated ICD rules required')
    require(report['manifest_sha256'] == file_hash(root / 'manifest.json') and identity['code'] == code_identity('rules'), 'ICD snapshot/code changed')
    require(identity['parent_signature'] == source['signature'] and
            identity['parent_manifest_sha256'] == file_hash(Path(input_dir) / 'manifest.json'), 'ICD source snapshot differs')
    require(file_hash(identity['config']['taxonomy_path']) == identity['taxonomy_sha256'], 'ICD taxonomy changed')
    left = list(batches(input_dir, source['signature']))
    right = list(batches(root, manifest['signature']))
    require(len(left) == len(right) == manifest['batches'] == source['batches'], 'ICD batch coverage differs')
    for (_, a), (_, b) in zip(left, right):
        require(all(a[k] == b[k] for k in ('index', 'documents', 'first_doc_id', 'last_doc_id')), 'ICD batch geometry differs')
    documents = sum(info['documents'] for _, info in right)
    require(documents == manifest['documents'] == report['documents'] == source['documents'], 'ICD document totals differ')
    return {'validation': 'passed', 'documents': documents, 'batches': len(right),
            'methods': report['methods'], 'original_full_validation_sha256': file_hash(root / 'VALIDATION.json'),
            'input_and_output_payload_checksums_verified': True, 'deterministic_rules_recomputed_this_check': False,
            'previous_full_evidence_validation_reused': report['source_evidence_checked'],
            'clinical_quality_measured': False, 'calibration': 'pending'}


def query_vectors(plan_path, query_dir, query_file):
    plan = read_plan(plan_path)
    root = Path(query_dir)
    manifest = read_json(root / 'manifest.json')
    manifest_identity(manifest)
    require(manifest['state'] == 'complete' and manifest['identity']['plan_signature'] == plan['signature'] and
            manifest['identity']['query_sha256'] == file_hash(query_file), 'Query source/plan differs')
    require(file_hash(root / 'vectors.npy') == manifest['vectors_sha256'] and
            file_hash(root / 'queries.json') == manifest['queries_sha256'], 'Query payload changed')
    actual, expected = read_json(root / 'queries.json'), queries(query_file)
    require(len(actual) == len(expected) == manifest['queries'] and
            all(all(a[k] == e[k] for k in ('id', 'query')) and type(a.get('window_count')) is int and a['window_count'] > 0
                for a, e in zip(actual, expected)), 'Query coverage/text/window accounting differs')
    matrix = np.load(root / 'vectors.npy', allow_pickle=False)
    require(matrix.dtype == np.dtype('float16') and matrix.shape == (len(expected), plan['encoding']['model_spec']['dimension']), 'Query shape/dtype differs')
    values = matrix.astype('float32')
    require(np.isfinite(values).all() and (np.abs(np.linalg.norm(values, axis=1) - 1) < .003).all(), 'Nonfinite/unnormalized query vectors')
    registry = read_json(root / 'MODEL_REGISTRY.json')
    require(all(registry[k] == plan['encoding']['model_spec'][k] for k in ('model_id', 'revision', 'dimension', 'pooling')) and
            0 < registry['parameters'] <= 15000000000, 'Query model registry differs')
    return {'validation': 'passed', 'queries': len(expected), 'dimension': matrix.shape[1],
            'plan_signature': plan['signature'], 'manifest_sha256': file_hash(root / 'manifest.json'),
            'query_sha256': file_hash(query_file), 'all_vectors_finite_and_normalized': True}


def lexical_snapshot(config_path):
    from .lexical_stage7 import Analyzer, config, logical_config
    from .retrieval_contracts import chunk_snapshot
    c = config(config_path)
    root = Path(c['output_dir'])
    manifest, report = read_json(root / 'manifest.json'), read_json(root / 'VALIDATION.json')
    manifest_identity(manifest)
    require(manifest['state'] == 'complete' and report['validation'] == 'passed' and
            report['manifest_sha256'] == file_hash(root / 'manifest.json') and
            report['database_sha256'] == file_hash(root / 'index.sqlite3'), 'Lexical snapshot seal differs')
    require(manifest['identity']['config'] == logical_config(c) and
            manifest['identity']['source'] == chunk_snapshot(c['chunk_dir']), 'Lexical source/config differs')
    require(manifest['identity']['analyzer'] == Analyzer(c['analyzer']).registry, 'Lexical analyzer differs')
    require(all(file_hash(Path(__file__).with_name(n)) == h for n, h in manifest['identity']['code'].items()), 'Lexical code differs')
    with closing(sqlite3.connect((root / 'index.sqlite3').resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        terms = db.execute('SELECT COUNT(*) FROM terms').fetchone()[0]
        batches, rows, complete = db.execute('SELECT COUNT(*),SUM(n),SUM(norm_done) FROM jobs').fetchone()
    require(chunks == rows == manifest['chunks'] == report['chunks'] and terms == manifest['terms'] and
            batches == complete == manifest['identity']['source']['batches'], 'Lexical ledger/statistics differ')
    return {'validation': 'passed', 'chunks': chunks, 'terms': terms, 'batches': batches,
            'average_length': manifest['average_length'], 'database_sha256': report['database_sha256'],
            'original_full_validation_sha256': file_hash(root / 'VALIDATION.json'),
            'previous_full_source_mapping_validation_reused': True, 'database_snapshot_checksum_verified': True,
            'full_query_generation': 'deferred by owner', 'quality_measured': False}


def dense_index(vector_dir, index_dir):
    plan = ready(vector_dir)
    root = Path(index_dir)
    manifest = read_json(root / 'manifest.json')
    manifest_identity(manifest)
    require(manifest['state'] == 'complete' and manifest['identity']['plan_signature'] == plan['signature'] and
            manifest['identity']['code_sha256'] == file_hash(Path(__file__).with_name('dense_index.py')),
            'Incomplete/different dense index revision')
    total = plan['source']['chunks']
    require(manifest['rows'] == total, 'Dense index row count differs')
    if manifest['identity']['kind'] == 'ivfpq':
        import faiss
        require(file_hash(root / manifest['checkpoint']) == manifest['index_sha256'], 'Dense index checksum differs')
        index = faiss.read_index(str(root / manifest['checkpoint']))
        require(index.is_trained and index.ntotal == total and index.d == plan['encoding']['model_spec']['dimension'] and
                index.metric_type == faiss.METRIC_INNER_PRODUCT and index.nlist == manifest['effective_nlist'] and
                index.pq.M == manifest['identity']['m'] and index.pq.nbits == manifest['effective_nbits'], 'FAISS geometry/metric differs')
        seen = np.zeros(total, dtype='bool')
        for number in range(index.nlist):
            size = index.invlists.list_size(number)
            if not size:
                continue
            pointer = index.invlists.get_ids(number)
            try:
                ids = faiss.rev_swig_ptr(pointer, size)
                require((ids >= 0).all() and (ids < total).all() and len(np.unique(ids)) == size and
                        not seen[ids].any(), 'Duplicate/out-of-range FAISS IDs')
                seen[ids] = True
            finally:
                index.invlists.release_ids(number, pointer)
        require(seen.all(), 'FAISS ID coverage has gaps')
    else:
        require(manifest['identity']['kind'] == 'exact', 'Unknown dense index kind')
    return {'validation': 'passed', 'rows': total, 'batches': len(plan['parts']),
            'dimension': plan['encoding']['model_spec']['dimension'], 'plan_signature': plan['signature'],
            'kind': manifest['identity']['kind'], 'manifest_sha256': file_hash(root / 'manifest.json'),
            'unique_complete_vector_ids': True, 'source_payload_checksums_verified': True,
            'ann_quality_measured': False, 'relevance_quality_measured': False}


def candidates(query_file, candidate_dir, mapping_dir, query_dir=None):
    root, mapping_root = Path(candidate_dir), Path(mapping_dir)
    state = read_json(root / 'manifest.json')
    manifest_identity(state)
    reference = read_json(mapping_root / 'plan.json')
    require(state['state'] == 'complete' and state['identity']['source'] == reference['source'], 'Candidate snapshot incomplete/different')
    mapping_report = read_json(mapping_root / 'VALIDATION.json')
    require(mapping_report['mapping_sha256'] == file_hash(mapping_root / 'mapping.sqlite3'), 'Mapping database seal differs')
    if 'query_sha256' in state['identity']:
        require(state['identity']['query_sha256'] == file_hash(query_file), 'Candidate query source differs')
    else:
        query_root = Path(query_dir) if query_dir is not None else mapping_root.parent / 'query_vectors'
        query_manifest = read_json(query_root / 'manifest.json')
        require(file_hash(query_root / 'manifest.json') == state['identity']['query_manifest_sha256'] and
                query_manifest['identity']['query_sha256'] == file_hash(query_file), 'Dense candidate query source differs')
    expected = queries(query_file)
    expected_paths = {f"query-{q['id']}.json" for q in expected}
    require({p.name for p in root.glob('query-*.json')} == expected_paths, 'Missing/extra candidate queries')
    counts, total, empty, vocabulary = [], 0, [], Counter()
    fields = ('row_id', 'chunk_id', 'doc_id', 'derived_version_id', 'source_batch', 'source_row',
              'chunk_order', 'language_hint', 'retrieval_text_sha256')
    with closing(sqlite3.connect((mapping_root / 'mapping.sqlite3').resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.execute('PRAGMA cache_size=-131072')
        for number, query in enumerate(expected, 1):
            row = checked(root / f"query-{query['id']}.json")
            require(row['id'] == query['id'] and row['run_signature'] == state['signature'], 'Candidate query/run differs')
            items = row['candidates']
            require(len(items) <= state['identity']['top_k'], 'Too many candidates')
            require(len({c['chunk_id'] for c in items}) == len(items), 'Duplicate candidate chunks')
            previous = math.inf
            source_rows = {}
            for start in range(0, len(items), 900):
                ids = [c['row_id'] for c in items[start:start + 900]]
                require(all(type(i) is int and 0 <= i < reference['source']['chunks'] for i in ids), 'Invalid candidate row ID')
                if ids:
                    placeholders = ','.join('?' for _ in ids)
                    source_rows.update((i, json.loads(payload)) for i, payload in db.execute(
                        f'SELECT row_id,payload FROM chunks WHERE row_id IN ({placeholders})', ids))
            for item in items:
                score = item['score']
                require(type(score) in (int, float) and math.isfinite(score) and score <= previous, 'Invalid candidate score/order')
                previous = score
                original = source_rows.get(item['row_id'])
                require(original is not None and all(item.get(k) == original.get(k) for k in fields), 'Candidate source mapping differs')
                if 'window_count' in item:
                    require(item['window_count'] == original['window_count'], 'Candidate mapped-window count differs')
                require(type(item['doc_id']) is int, 'Document IDs must be integers')
                vocabulary[item.get('language_hint') or 'unknown'] += 1
            counts.append(len(items))
            total += len(items)
            if not items:
                empty.append(query['id'])
            if number % 100 == 0:
                print(json.dumps({'validation': 'candidates', 'directory': str(root), 'queries': number}), flush=True)
    return {'validation': 'passed', 'queries': len(expected), 'candidates': total,
            'minimum_candidates': min(counts), 'maximum_candidates': max(counts), 'empty_query_ids': empty,
            'language_hint_counts': dict(vocabulary), 'query_sha256': file_hash(query_file),
            'manifest_sha256': file_hash(root / 'manifest.json'), 'all_source_mappings_verified': True,
            'source': reference['source'], 'quality_measured': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    icd = commands.add_parser('icd')
    icd.add_argument('--input-dir', default='artifacts/chunk_inputs_stage5_full')
    icd.add_argument('--output-dir', default='artifacts/icd_stage6_rules')
    dense = commands.add_parser('dense-index')
    dense.add_argument('--vector-dir', default='artifacts/dense_stage8_bge_structure/merged')
    dense.add_argument('--index-dir', default='artifacts/dense_stage8_bge_structure/index_ivfpq')
    lexical = commands.add_parser('lexical-index')
    lexical.add_argument('--config', default='configs/lexical_stage7.json')
    query = commands.add_parser('queries')
    query.add_argument('--plan', default='artifacts/dense_stage8_bge_structure/plan.json')
    query.add_argument('--query-dir', default='artifacts/dense_stage8_bge_structure/query_vectors')
    query.add_argument('--queries', default='Data/official/ViBioMIR/query.parquet')
    candidate = commands.add_parser('candidates')
    candidate.add_argument('--queries', default='Data/official/ViBioMIR/query.parquet')
    candidate.add_argument('--candidate-dir', required=True)
    candidate.add_argument('--mapping-dir', default='artifacts/dense_stage8_bge_structure/merged')
    candidate.add_argument('--query-dir', default=None)
    for sub in (icd, dense, lexical, query, candidate):
        sub.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'icd':
        result = icd_snapshot(args.input_dir, args.output_dir)
    elif args.command == 'dense-index':
        result = dense_index(args.vector_dir, args.index_dir)
    elif args.command == 'queries':
        result = query_vectors(args.plan, args.query_dir, args.queries)
    elif args.command == 'lexical-index':
        result = lexical_snapshot(args.config)
    else:
        result = candidates(args.queries, args.candidate_dir, args.mapping_dir, args.query_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
