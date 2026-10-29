"""S09 CPU preparation, portable GPU workers, selection and review export.

No lexical search or embedding rebuild is launched by this module.
"""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from .chunk_batches import read_json, read_rows
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .retrieval_contracts import checked, chunk_snapshot, distinct, open_run, queries, require, signed
from .stage9_core import (MAPPING_FIELDS, PINNED_RERANKER, fuse, query_metadata, select,
                          validate_facets, validate_settings)


MODULES = ('stage9_pipeline.py', 'stage9_core.py', 'stage9_reranker.py',
           'retrieval_contracts.py', 'chunk_batches.py', 'io.py')


def code_identity():
    return {name: file_hash(Path(__file__).with_name(name)) for name in MODULES}


def verify_value(value):
    require(value.get('signature') == text_hash(canonical_json({k: v for k, v in value.items() if k != 'signature'})),
            'Signed record checksum differs')
    return value


def load_packet(root, item):
    path = Path(root) / item['path']
    require(file_hash(path) == item['sha256'], f'Prepared packet changed: {path}')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return verify_value(json.load(stream))


def save_packet(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=3) as stream:
        json.dump(signed(value), stream, ensure_ascii=False, separators=(',', ':'))
    temporary.replace(path)


def backup_copy(source, destination):
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + '.tmp')
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def prepared(root):
    state = read_json(Path(root) / 'manifest.json')
    require(state['signature'] == text_hash(canonical_json(state['identity'])) and state['state'] == 'complete',
            'S09 preparation is not complete; repeat prepare with the same inputs first')
    require(state['identity']['code'] == code_identity(), 'S09 code changed; use the matching bundle or prepare a new run')
    ids = [q['id'] for q in state['identity']['queries']]
    require(len(state['packets']) == len(ids) and [p['id'] for p in state['packets']] == ids,
            'Prepared query coverage differs')
    return state


def _inputs(c, facets_file=None, limit_queries=None):
    all_queries = queries(c['queries'])
    facets = {}
    if facets_file:
        for row in read_json(facets_file):
            require(type(row['id']) is int and row['id'] not in facets, 'Duplicate/noninteger facet query ID')
            facets[row['id']] = row['facets']
        require(set(facets) <= {q['id'] for q in all_queries}, 'Unknown facet query IDs')
    chosen = all_queries[:limit_queries] if limit_queries else all_queries
    for q in chosen:
        q['facets'] = validate_facets(q['query'], facets.get(q['id'], []))
    snapshot = chunk_snapshot(c['chunk_dir'])
    branch_meta = []
    for branch in c['branches']:
        root = Path(branch['candidate_dir'])
        state = read_json(root / 'manifest.json')
        require(state['signature'] == text_hash(canonical_json(state['identity'])) and state['state'] == 'complete' and
                state['identity']['source'] == snapshot, f'Incomplete or incompatible candidate branch: {root}')
        report = read_json(root / 'VALIDATION.json')
        require(report['validation'] == 'passed' and report['manifest_sha256'] == file_hash(root / 'manifest.json'),
                f'Validate candidate branch before S09: {root}')
        branch_queries_path = branch.get('queries', c['queries'])
        branch_queries = queries(branch_queries_path)
        require([q['id'] for q in branch_queries] == [q['id'] for q in all_queries] and
                report['query_sha256'] == file_hash(branch_queries_path), 'Candidate branch query binding differs')
        facet_id = branch.get('facet_id', 'original')
        for original, branch_q in zip(all_queries, branch_queries):
            if facet_id == 'original':
                require(original['query'] == branch_q['query'], 'Original-query branch must retain all original text')
            else:
                mapped = validate_facets(original['query'], facets.get(original['id'], []))
                require(any(f['id'] == facet_id and f['text'] == branch_q['query'] for f in mapped),
                        'Facet branch query must match its explicit source span')
        paths = {str(q['id']): file_hash(root / f"query-{q['id']}.json") for q in chosen}
        branch_meta.append({'name': branch['name'], 'family': branch['family'], 'facet_id': facet_id,
                            'run_signature': state['signature'], 'manifest_sha256': file_hash(root / 'manifest.json'),
                            'query_sha256': report['query_sha256'], 'query_files': paths})
    logical = {k: v for k, v in c.items() if k not in {'queries', 'chunk_dir', 'output_dir', 'branches'}}
    return chosen, {'operation': 'stage9-prepare', 'settings': logical, 'source': snapshot,
                    'query_file_sha256': file_hash(c['queries']), 'queries': chosen,
                    'branches': branch_meta, 'code': code_identity(),
                    'scope': 'query_subset' if len(chosen) < len(all_queries) else 'all_queries'}


def _parts(chunk_dir, snapshot):
    total, documents, result = 0, 0, []
    for number in range(snapshot['batches']):
        path = Path(chunk_dir) / f'batch-{number:06d}'
        info = read_json(path / 'batch.json')
        require(info['index'] == number and info['signature'] == snapshot['signature'], 'Chunk batch identity differs')
        result.append((path, info))
        total += info['chunks']; documents += info['documents']
    require(total == snapshot['chunks'] and documents == snapshot['documents'], 'Chunk batch totals differ')
    return result


def prepare(config_path='configs/retrieval_stage9.json', *, output_dir=None, facets_file=None,
            limit_queries=None, stop_after_batches=None):
    c = validate_settings(read_json(config_path))
    require(limit_queries is None or type(limit_queries) is int and limit_queries > 0, 'Positive query limit required')
    require(stop_after_batches is None or type(stop_after_batches) is int and stop_after_batches > 0,
            'Positive stop-after-batches required')
    selected_queries, identity = _inputs(c, facets_file, limit_queries)
    root = Path(output_dir or c['output_dir'])
    distinct(root, c['chunk_dir'], c['queries'], *(b['candidate_dir'] for b in c['branches']))
    state = open_run(root, identity)
    with writer_lock(root):
        if state['state'] == 'complete':
            state = prepared(root)
            for entry in state['packets']: load_packet(root, entry)
            return state
        requests, wanted = [], {}
        (root / 'requests').mkdir(exist_ok=True)
        for q in selected_queries:
            lists = []
            for branch, meta in zip(c['branches'], identity['branches']):
                path = Path(branch['candidate_dir']) / f"query-{q['id']}.json"
                require(file_hash(path) == meta['query_files'][str(q['id'])], 'Candidate query changed during preparation')
                record = checked(path)
                require(record['id'] == q['id'] and record['run_signature'] == meta['run_signature'], 'Candidate query/run mismatch')
                lists.append((branch, record['candidates']))
            fused = fuse(lists, c)
            rows = fused[:c['rerank_top_k']]
            for item in rows:
                previous = wanted.setdefault(item['chunk_id'], item)
                require(all(previous[k] == item[k] for k in MAPPING_FIELDS), 'Cross-query source mapping differs')
            request = {'id': q['id'], 'query': q['query'], 'facets': q['facets'],
                       'query_metadata': query_metadata(q['query']), 'candidates': rows,
                       'counts': {'branch_candidates': sum(len(items) for _, items in lists),
                                  'fused_candidates': len(fused), 'rerank_candidates': len(rows)}}
            requests.append(request)
            write_json(root / 'requests' / f"query-{q['id']}.json", signed(request))
        needed_batches = {x['source_batch'] for x in wanted.values()}
        parts = _parts(c['chunk_dir'], identity['source'])
        require(needed_batches <= set(range(len(parts))), 'Candidate source batch outside the snapshot')
        with closing(sqlite3.connect(root / 'evidence.sqlite3')) as db:
            db.execute('PRAGMA cache_size=-65536')
            db.execute('CREATE TABLE IF NOT EXISTS evidence(chunk_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs(batch INTEGER PRIMARY KEY,source_sha TEXT NOT NULL)')
            processed = 0
            for path, info in parts:
                number = info['index']
                if number not in needed_batches: continue
                require(file_hash(path / 'documents.jsonl.gz') == info['sha256'], f'Chunk source payload changed: {path}')
                job = db.execute('SELECT source_sha FROM jobs WHERE batch=?', (number,)).fetchone()
                if job:
                    require(job[0] == info['sha256'], 'Prepared evidence source changed')
                    continue
                chunks_count, doc_count = 0, 0
                with db:
                    for row_number, document in enumerate(read_rows(path)):
                        doc_count += 1
                        for order, chunk in enumerate(document['chunks']):
                            chunks_count += 1
                            expected = wanted.get(chunk['chunk_id'])
                            if expected is None: continue
                            mapping = {**chunk, 'source_batch': number, 'source_row': row_number, 'chunk_order': order,
                                       'retrieval_text_sha256': text_hash(chunk['retrieval_text'])}
                            require(all(mapping[k] == expected[k] for k in MAPPING_FIELDS) and
                                    chunk['doc_id'] == document['doc_id'] and
                                    chunk['derived_version_id'] == document['derived_version_id'], 'Candidate/source chunk mismatch')
                            require(type(chunk['doc_id']) is int and chunk['chunk_text'].strip() and
                                    text_hash(chunk['chunk_text']) == chunk['text_sha256'] and
                                    type(chunk['region_start']) is int and type(chunk['region_end']) is int and
                                    0 <= chunk['region_start'] < chunk['region_end'] and
                                    chunk['region_end'] - chunk['region_start'] == len(chunk['chunk_text']), 'Invalid original evidence span')
                            db.execute('INSERT INTO evidence VALUES(?,?)',
                                       (chunk['chunk_id'], canonical_json(signed(mapping))))
                    require((chunks_count, doc_count) == (info['chunks'], info['documents']), 'Source batch accounting differs')
                    db.execute('INSERT INTO jobs VALUES(?,?)', (number, info['sha256']))
                processed += 1
                completed_batches = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
                if processed == 1 or completed_batches % 25 == 0 or completed_batches == len(needed_batches):
                    print(json.dumps({'stage9': 'extract_evidence', 'source_batch': number,
                                      'completed_batches': completed_batches,
                                      'needed_batches': len(needed_batches)}), flush=True)
                if stop_after_batches and processed >= stop_after_batches:
                    state.update(state='paused', extraction_batches=db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0])
                    write_json(root / 'manifest.json', state)
                    return state
            require(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == len(wanted), 'Missing requested source chunks')
            packets, total = [], 0
            for index, request in enumerate(requests):
                candidates = []
                for item in request['candidates']:
                    found = db.execute('SELECT payload FROM evidence WHERE chunk_id=?', (item['chunk_id'],)).fetchone()
                    require(found is not None, 'Source evidence missing')
                    evidence = verify_value(json.loads(found[0]))
                    require(all(item[k] == evidence[k] for k in MAPPING_FIELDS), 'Evidence cache mapping differs')
                    candidates.append({**{k: v for k, v in evidence.items() if k != 'signature'}, **item})
                packet_path = root / 'packets' / f"query-{request['id']}.json.gz"
                save_packet(packet_path, {**request, 'candidates': candidates, 'run_signature': state['signature']})
                packets.append({'id': request['id'], 'worker_id': index % c['workers'],
                                'path': str(packet_path.relative_to(root)), 'sha256': file_hash(packet_path),
                                'candidates': len(candidates)})
                total += len(candidates)
        state.update(state='complete', packets=packets, queries=len(packets), candidates=total,
                     unique_evidence_chunks=len(wanted), extraction_batches=len(needed_batches))
        write_json(root / 'manifest.json', state)
    return state


def _worker_identity(state, worker_id):
    require(type(worker_id) is int and 0 <= worker_id < state['identity']['settings']['workers'], 'Worker ID outside prepared plan')
    return {'operation': 'stage9-rerank', 'prepared_signature': state['signature'],
            'worker_id': worker_id, 'reranker': state['identity']['settings']['reranker']}


def verify_scores(record, packet, packet_info, state, run_signature):
    verify_value(record)
    require(record['id'] == packet['id'] and record['run_signature'] == run_signature and
            record['prepared_signature'] == state['signature'] and record['packet_sha256'] == packet_info['sha256'],
            'Rerank query/input identity differs')
    rows = record['scores']
    require([r['chunk_id'] for r in rows] == [r['chunk_id'] for r in packet['candidates']] and
            all(type(r['score']) in (int, float) and math.isfinite(r['score']) and
                type(r['windows']) is int and r['windows'] > 0 for r in rows),
            'Reranker added/dropped candidates or produced invalid scores')
    registry = record['model_registry']
    require(all(registry.get(k) == v for k, v in PINNED_RERANKER.items()) and
            type(registry.get('parameters')) is int and 0 < registry['parameters'] <= 15000000000,
            'Reranker registry/model size differs')
    require(not registry.get('test_fixture'), 'Test-fixture scores are not production results')
    require(registry.get('window_policy') == state['identity']['settings']['reranker'], 'Reranker window policy differs')
    return rows


def rerank_worker(prepared_dir, output_dir, worker_id, *, backup_dir=None, batch_size=8,
                  padded_tokens=8192, stop_after_queries=None, scorer_factory=None):
    from .stage9_reranker import Reranker
    require(type(batch_size) is int and batch_size > 0 and type(padded_tokens) is int and padded_tokens > 0,
            'Positive GPU batch/token budgets required')
    require(stop_after_queries is None or type(stop_after_queries) is int and stop_after_queries > 0,
            'Positive stop-after-queries required')
    state = prepared(prepared_dir)
    root = Path(output_dir)
    distinct(root, prepared_dir)
    if backup_dir:
        distinct(root, backup_dir); distinct(backup_dir, prepared_dir)
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
    manifest = open_run(root, _worker_identity(state, worker_id))
    factory = scorer_factory or Reranker
    scorer, completed, new = None, [], 0
    entries = [x for x in state['packets'] if x['worker_id'] == worker_id]
    with writer_lock(root), closing(sqlite3.connect(root / 'score_cache.sqlite3')) as cache:
        cache.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        for entry in entries:
            packet = load_packet(prepared_dir, entry)
            output = root / f"query-{entry['id']}.json"
            backup = Path(backup_dir) / output.name if backup_dir else None
            if not output.exists() and backup and backup.exists():
                record = checked(backup)
                verify_scores(record, packet, entry, state, manifest['signature'])
                shutil.copy2(backup, output)
            if output.exists():
                record = checked(output)
                verify_scores(record, packet, entry, state, manifest['signature'])
            else:
                if scorer is None:
                    scorer = factory(state['identity']['settings']['reranker'], batch_size=batch_size, padded_tokens=padded_tokens)
                scores = scorer.score(packet['query'], [x['retrieval_text'] for x in packet['candidates']], cache)
                require(len(scores) == len(packet['candidates']), 'Reranker returned missing/extra scores')
                record = signed({'id': packet['id'], 'run_signature': manifest['signature'],
                                 'prepared_signature': state['signature'], 'packet_sha256': entry['sha256'],
                                 'scores': [{'chunk_id': x['chunk_id'], **s} for x, s in zip(packet['candidates'], scores)],
                                 'model_registry': scorer.registry})
                verify_scores(record, packet, entry, state, manifest['signature'])
                write_json(output, record)
                new += 1
            if backup: backup_copy(output, backup)
            completed.append(entry['id'])
            manifest.update(state='building', completed_query_ids=completed, queries=len(completed))
            write_json(root / 'manifest.json', manifest)
            if backup_dir: backup_copy(root / 'manifest.json', Path(backup_dir) / 'manifest.json')
            print(json.dumps({'worker': worker_id, 'query_id': entry['id'], 'complete': len(completed),
                              'assigned': len(entries), 'new_this_invocation': new}), flush=True)
            if stop_after_queries and new >= stop_after_queries: break
        manifest.update(state='complete' if len(completed) == len(entries) else 'paused',
                        completed_query_ids=completed, queries=len(completed))
        write_json(root / 'manifest.json', manifest)
        if backup_dir: backup_copy(root / 'manifest.json', Path(backup_dir) / 'manifest.json')
    return manifest


def export(prepared_dir, output_dir, *, mode='baseline', workers_dir=None):
    require(mode in {'baseline', 'reranked'}, 'Choose baseline or reranked')
    state = prepared(prepared_dir)
    root = Path(output_dir)
    distinct(root, prepared_dir)
    if mode == 'reranked':
        require(workers_dir is not None, 'Supply --workers-dir for reranked results')
        distinct(root, workers_dir)
    worker_states, worker_inputs = {}, {}
    if mode == 'reranked':
        for worker in range(state['identity']['settings']['workers']):
            path = Path(workers_dir) / f'worker_{worker}' / 'manifest.json'
            require(path.is_file(), f'Missing rerank worker {worker}: finish/download the GPU output or use --mode baseline')
            value = read_json(path)
            identity = _worker_identity(state, worker)
            require(value['identity'] == identity and value['signature'] == text_hash(canonical_json(identity)) and
                    value['state'] == 'complete', f'Finish rerank worker {worker} first')
            worker_states[worker] = value
            worker_inputs[str(worker)] = file_hash(path)
        worker_inputs['query_files'] = {
            str(entry['id']): file_hash(Path(workers_dir) / f"worker_{entry['worker_id']}" / f"query-{entry['id']}.json")
            for entry in state['packets']}
    output_identity = {'operation': 'stage9-results', 'prepared_manifest_sha256': file_hash(Path(prepared_dir) / 'manifest.json'),
                       'mode': mode, 'workers': worker_inputs,
                       'selection': state['identity']['settings']['selection'], 'code': code_identity()}
    manifest = open_run(root, output_identity)
    results, review_index = [], []
    with writer_lock(root):
        (root / 'review').mkdir(exist_ok=True)
        for entry in state['packets']:
            packet = load_packet(prepared_dir, entry)
            rows = packet['candidates']
            if mode == 'reranked':
                path = Path(workers_dir) / f"worker_{entry['worker_id']}" / f"query-{entry['id']}.json"
                scores = [x['score'] for x in verify_scores(checked(path), packet, entry, state,
                                                           worker_states[entry['worker_id']]['signature'])]
            else:
                scores = [x['fused_score'] for x in rows]
            prediction, review = select(rows, scores, state['identity']['settings']['selection'])
            results.append({'id': packet['id'], **prediction})
            review_path = root / 'review' / f"query-{packet['id']}.json"
            write_json(review_path, signed({'id': packet['id'], 'query': packet['query'], 'mode': mode,
                       'run_signature': manifest['signature'], 'query_metadata': packet['query_metadata'],
                       'facets': packet['facets'], 'candidate_counts': packet['counts'], **review}))
            review_index.append({'id': packet['id'], 'path': str(review_path.relative_to(root)), 'sha256': file_hash(review_path)})
        write_json(root / 'predictions.json', results)
        manifest.update(state='complete', queries=len(results), review_files=review_index,
                        predictions_sha256=file_hash(root / 'predictions.json'),
                        source_integrity='selected original stage-5 chunks; no text generation',
                        official_eligibility='pending S11', quality_measured=False)
        write_json(root / 'manifest.json', manifest)
    return manifest


def validate_worker(prepared_dir, output_dir, worker_id):
    state, root = prepared(prepared_dir), Path(output_dir)
    manifest = read_json(root / 'manifest.json')
    identity = _worker_identity(state, worker_id)
    entries = [x for x in state['packets'] if x['worker_id'] == worker_id]
    require(manifest['identity'] == identity and manifest['signature'] == text_hash(canonical_json(identity)) and
            manifest['state'] == 'complete' and manifest['completed_query_ids'] == [x['id'] for x in entries],
            'Worker is not complete or query coverage differs')
    total = 0
    for entry in entries:
        packet = load_packet(prepared_dir, entry)
        rows = verify_scores(checked(root / f"query-{entry['id']}.json"), packet, entry, state, manifest['signature'])
        total += len(rows)
    report = {'validation': 'passed', 'worker_id': worker_id, 'queries': len(entries),
              'candidates': total, 'manifest_sha256': file_hash(root / 'manifest.json'), 'quality_measured': False}
    write_json(root / 'VALIDATION.json', report)
    return report


def archive_worker(prepared_dir, output_dir, worker_id, archive_path):
    """One small portable ZIP per validated worker, without model/cache files."""
    report = validate_worker(prepared_dir, output_dir, worker_id)
    state, root, target = prepared(prepared_dir), Path(output_dir), Path(archive_path)
    distinct(target, prepared_dir, output_dir)
    names = ['manifest.json', 'VALIDATION.json'] + [f"query-{p['id']}.json" for p in state['packets'] if p['worker_id'] == worker_id]
    meta = signed({'operation': 'stage9-worker-archive', 'prepared_signature': state['signature'],
                   'worker_id': worker_id, 'files': {name: file_hash(root / name) for name in names}})
    if target.exists():
        with zipfile.ZipFile(target) as archive:
            require(json.loads(archive.read('WORKER_ARCHIVE.json')) == meta, 'Worker archive exists with different inputs; choose a new filename')
            require(set(archive.namelist()) == set(names) | {'WORKER_ARCHIVE.json'}, 'Worker archive file set differs')
            for name, digest in meta['files'].items():
                require(hashlib.sha256(archive.read(name)).hexdigest() == digest, 'Existing worker archive changed')
        return {'state': 'reused', 'archive': str(target), **report}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for name in names: archive.write(root / name, name)
        archive.writestr('WORKER_ARCHIVE.json', canonical_json(meta))
    temporary.replace(target)
    return {'state': 'complete', 'archive': str(target), **report}


def import_worker(prepared_dir, archive_path, workers_dir):
    """Verify the entire official worker archive before atomically restoring it."""
    state, root = prepared(prepared_dir), Path(workers_dir)
    distinct(root, prepared_dir)
    with zipfile.ZipFile(archive_path) as archive:
        meta = verify_value(json.loads(archive.read('WORKER_ARCHIVE.json')))
        worker = meta['worker_id']
        _worker_identity(state, worker)
        require(meta['operation'] == 'stage9-worker-archive' and meta['prepared_signature'] == state['signature'],
                'Worker archive belongs to a different prepared run')
        entries = [p for p in state['packets'] if p['worker_id'] == worker]
        expected = {'manifest.json', 'VALIDATION.json'} | {f"query-{p['id']}.json" for p in entries}
        require(set(meta['files']) == expected and set(archive.namelist()) == expected | {'WORKER_ARCHIVE.json'} and
                len(archive.namelist()) == len(expected) + 1, 'Unexpected/missing worker archive files')
        payloads = {}
        for name, digest in meta['files'].items():
            require(Path(name).name == name, 'Worker archive contains an unsafe path')
            data = archive.read(name)
            require(hashlib.sha256(data).hexdigest() == digest, f'Worker archive checksum differs: {name}')
            payloads[name] = data
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f'worker_{worker}'
    with writer_lock(root):
        if destination.exists():
            for name, digest in meta['files'].items():
                require((destination / name).is_file() and file_hash(destination / name) == digest,
                        'Existing worker folder differs; keep it and restore this archive into a new workers directory')
            validate_worker(prepared_dir, destination, worker)
            return {'state': 'reused', 'worker_id': worker, 'output_dir': str(destination)}
        with tempfile.TemporaryDirectory(prefix='.pending-worker-', dir=root) as temporary:
            staging = Path(temporary)
            for name, data in payloads.items(): (staging / name).write_bytes(data)
            validate_worker(prepared_dir, staging, worker)
            staging.rename(destination)
    return {'state': 'complete', 'worker_id': worker, 'output_dir': str(destination)}


def finish(prepared_dir, workers_dir, output_dir, *, chunk_dir, queries_path):
    """One local command after downloading the per-worker ZIPs from Colab."""
    state, root = prepared(prepared_dir), Path(workers_dir)
    for worker in range(state['identity']['settings']['workers']):
        destination = root / f'worker_{worker}'
        if not destination.is_dir():
            archive = root / f'stage9_worker_{worker}.zip'
            require(archive.is_file(), f'Missing GPU worker {worker}; download {archive.name} into {root} first')
            import_worker(prepared_dir, archive, root)
        validate_worker(prepared_dir, destination, worker)
    export(prepared_dir, output_dir, mode='reranked', workers_dir=root)
    return validate_results(prepared_dir, output_dir, chunk_dir=chunk_dir, queries_path=queries_path, workers_dir=root)


def validate_results(prepared_dir, output_dir, *, chunk_dir, queries_path, workers_dir=None):
    """Recompute selection and re-extract every selected chunk from source batches."""
    state, root = prepared(prepared_dir), Path(output_dir)
    manifest = read_json(root / 'manifest.json')
    identity = manifest['identity']
    require(manifest['signature'] == text_hash(canonical_json(identity)) and manifest['state'] == 'complete' and
            identity['prepared_manifest_sha256'] == file_hash(Path(prepared_dir) / 'manifest.json') and
            identity['code'] == code_identity() and identity['selection'] == state['identity']['settings']['selection'],
            'Result manifest/source/code differs')
    expected = queries(queries_path)
    require(file_hash(queries_path) == state['identity']['query_file_sha256'], 'Official query source changed')
    if state['identity']['scope'] == 'query_subset': expected = expected[:len(state['packets'])]
    results = read_json(root / 'predictions.json')
    require(file_hash(root / 'predictions.json') == manifest['predictions_sha256'] and
            [r['id'] for r in results] == [q['id'] for q in expected] and
            manifest['queries'] == len(results) == len(manifest['review_files']) and
            [r['id'] for r in manifest['review_files']] == [q['id'] for q in expected],
            'Missing/extra/reordered prediction queries')
    require(chunk_snapshot(chunk_dir) == state['identity']['source'], 'Validation chunk source changed')
    selected = {}
    for result, entry, review_entry in zip(results, state['packets'], manifest['review_files']):
        packet = load_packet(prepared_dir, entry)
        review_path = root / review_entry['path']
        require(file_hash(review_path) == review_entry['sha256'], 'Review evidence changed')
        review = checked(review_path)
        if identity['mode'] == 'baseline':
            scores = [x['fused_score'] for x in packet['candidates']]
        else:
            require(workers_dir is not None, 'Supply --workers-dir to validate reranked results')
            worker = entry['worker_id']
            worker_path = Path(workers_dir) / f'worker_{worker}'
            require(file_hash(worker_path / 'manifest.json') == identity['workers'][str(worker)] and
                    file_hash(worker_path / f"query-{entry['id']}.json") == identity['workers']['query_files'][str(entry['id'])],
                    'Exported worker inputs changed')
            run_id = text_hash(canonical_json(_worker_identity(state, worker)))
            record = checked(Path(workers_dir) / f'worker_{worker}' / f"query-{entry['id']}.json")
            scores = [x['score'] for x in verify_scores(record, packet, entry, state, run_id)]
        predicted, details = select(packet['candidates'], scores, identity['selection'])
        require(result == {'id': packet['id'], **predicted} and review['selected_chunks'] == details['selected_chunks'] and
                review['decisions'] == details['decisions'], 'Selection or exported evidence differs')
        for item in details['selected_chunks']:
            selected[item['chunk_id']] = item
    by_batch = {}
    for item in selected.values(): by_batch.setdefault(item['source_batch'], {})[item['chunk_id']] = item
    found = set()
    for path, info in _parts(chunk_dir, state['identity']['source']):
        wanted = by_batch.get(info['index'])
        if not wanted: continue
        require(file_hash(path / 'documents.jsonl.gz') == info['sha256'], 'Original chunk payload changed')
        for row_index, document in enumerate(read_rows(path)):
            for order, chunk in enumerate(document['chunks']):
                saved = wanted.get(chunk['chunk_id'])
                if saved is None: continue
                actual = {**chunk, 'source_batch': info['index'], 'source_row': row_index,
                          'chunk_order': order, 'retrieval_text_sha256': text_hash(chunk['retrieval_text'])}
                require(all(actual[k] == saved[k] for k in MAPPING_FIELDS) and
                        all(actual[k] == saved[k] for k in ('chunk_text', 'text_sha256', 'region_id', 'region_start', 'region_end',
                                                          'source_start', 'source_end', 'source_kind', 'source_locator_json')),
                        'Exported text/span does not match original stage-5 evidence')
                found.add(chunk['chunk_id'])
    require(found == set(selected), 'Some selected source chunks are missing')
    report = {'validation': 'passed', 'queries': len(results), 'scope': state['identity']['scope'],
              'mode': identity['mode'], 'chunks': sum(len(r['relevant_chunks']) for r in results),
              'unique_selected_chunks': len(selected), 'original_evidence_verified': True,
              'manifest_sha256': file_hash(root / 'manifest.json'), 'predictions_sha256': file_hash(root / 'predictions.json'),
              'quality_measured': False, 'official_submission_ready': False, 'official_eligibility': 'pending S11'}
    write_json(root / 'VALIDATION.json', report)
    return report


def bundle(prepared_dir, output):
    state, output = prepared(prepared_dir), Path(output)
    distinct(output, prepared_dir)
    sources = {f'vibiomir/{name}': Path(__file__).with_name(name) for name in (*MODULES, '__init__.py')}
    sources['requirements-stage9-colab.txt'] = Path('requirements-stage9-colab.txt')
    sources['manifest.json'] = Path(prepared_dir) / 'manifest.json'
    for entry in state['packets']:
        load_packet(prepared_dir, entry)
        sources[entry['path']] = Path(prepared_dir) / entry['path']
    meta = signed({'operation': 'stage9-bundle', 'prepared_signature': state['signature'],
                   'files': {name: file_hash(path) for name, path in sources.items()}})
    if output.exists():
        with zipfile.ZipFile(output) as archive:
            require(json.loads(archive.read('BUNDLE.json')) == meta, 'Bundle already exists with different inputs; choose another filename')
            for name, digest in meta['files'].items():
                require(hashlib.sha256(archive.read(name)).hexdigest() == digest, 'Existing ZIP payload differs')
        return {'bundle': str(output), 'state': 'reused', 'queries': len(state['packets'])}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, path in sources.items(): archive.write(path, name)
        archive.writestr('BUNDLE.json', canonical_json(meta))
    temporary.replace(output)
    return {'bundle': str(output), 'state': 'complete', 'queries': len(state['packets']), 'workers': state['identity']['settings']['workers']}


def show(output_dir, query_id):
    state = read_json(Path(output_dir) / 'manifest.json')
    entries = [x for x in state['review_files'] if x['id'] == query_id]
    require(len(entries) == 1, 'Query ID not present in this result set')
    path = Path(output_dir) / entries[0]['path']
    require(file_hash(path) == entries[0]['sha256'], 'Review file changed')
    return checked(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='CPU: fuse bounded candidates and resolve original evidence once')
    p.add_argument('--config', default='configs/retrieval_stage9.json')
    p.add_argument('--output-dir'); p.add_argument('--facets-file')
    p.add_argument('--limit-queries', type=int); p.add_argument('--stop-after-batches', type=int)
    p = commands.add_parser('bundle', help='CPU: package prepared packets and code; no model weights')
    p.add_argument('--prepared-dir', default='artifacts/retrieval_stage9_hybrid_rrf')
    p.add_argument('--output', default='artifacts/stage9_rerank_colab.zip')
    p = commands.add_parser('rerank-worker', help='CUDA: resume only this worker query partition')
    p.add_argument('--prepared-dir', required=True); p.add_argument('--output-dir', required=True)
    p.add_argument('--worker-id', type=int, required=True); p.add_argument('--backup-dir')
    p.add_argument('--batch-size', type=int, default=8); p.add_argument('--padded-tokens', type=int, default=8192)
    p.add_argument('--stop-after-queries', type=int)
    p = commands.add_parser('validate-worker', help='CPU: verify assigned rerank scores against signed packets')
    p.add_argument('--prepared-dir', required=True); p.add_argument('--output-dir', required=True)
    p.add_argument('--worker-id', type=int, required=True)
    p = commands.add_parser('archive-worker', help='Package one complete validated GPU worker as a small ZIP')
    p.add_argument('--prepared-dir', required=True); p.add_argument('--output-dir', required=True)
    p.add_argument('--worker-id', type=int, required=True); p.add_argument('--archive', required=True)
    p = commands.add_parser('finish', help='Restore downloaded worker ZIPs, export reranked results and verify original evidence')
    p.add_argument('--prepared-dir', default='artifacts/retrieval_stage9_hybrid_rrf')
    p.add_argument('--workers-dir', default='artifacts/stage9_rerank_workers')
    p.add_argument('--output-dir', default='artifacts/results_stage9_hybrid_reranked')
    p.add_argument('--chunk-dir', default='artifacts/chunks_stage5_full_structure')
    p.add_argument('--queries', default='Data/official/ViBioMIR/query.parquet')
    for command in ('export', 'validate'):
        p = commands.add_parser(command)
        p.add_argument('--prepared-dir', default='artifacts/retrieval_stage9_hybrid_rrf')
        p.add_argument('--output-dir', required=True); p.add_argument('--workers-dir')
        if command == 'export': p.add_argument('--mode', choices=('baseline', 'reranked'), default='baseline')
        else:
            p.add_argument('--chunk-dir', default='artifacts/chunks_stage5_full_structure')
            p.add_argument('--queries', default='Data/official/ViBioMIR/query.parquet')
    p = commands.add_parser('show', help='Read the query, ranked documents, selected source chunks and decisions')
    p.add_argument('--output-dir', required=True); p.add_argument('--query-id', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare': result = prepare(args.config, output_dir=args.output_dir, facets_file=args.facets_file,
                                                       limit_queries=args.limit_queries, stop_after_batches=args.stop_after_batches)
        elif args.command == 'bundle': result = bundle(args.prepared_dir, args.output)
        elif args.command == 'rerank-worker': result = rerank_worker(args.prepared_dir, args.output_dir, args.worker_id,
                    backup_dir=args.backup_dir, batch_size=args.batch_size, padded_tokens=args.padded_tokens,
                    stop_after_queries=args.stop_after_queries)
        elif args.command == 'validate-worker': result = validate_worker(args.prepared_dir, args.output_dir, args.worker_id)
        elif args.command == 'archive-worker': result = archive_worker(args.prepared_dir, args.output_dir, args.worker_id, args.archive)
        elif args.command == 'finish': result = finish(args.prepared_dir, args.workers_dir, args.output_dir,
                                                       chunk_dir=args.chunk_dir, queries_path=args.queries)
        elif args.command == 'export': result = export(args.prepared_dir, args.output_dir, mode=args.mode, workers_dir=args.workers_dir)
        elif args.command == 'validate': result = validate_results(args.prepared_dir, args.output_dir, chunk_dir=args.chunk_dir,
                                                                   queries_path=args.queries, workers_dir=args.workers_dir)
        else: result = show(args.output_dir, args.query_id)
        if args.command in {'prepare', 'rerank-worker', 'export'}:
            summary = {k: result[k] for k in ('state', 'signature', 'queries', 'candidates',
                       'unique_evidence_chunks', 'extraction_batches') if k in result}
            summary['output_dir'] = str(args.output_dir or read_json(args.config)['output_dir']) if args.command == 'prepare' else args.output_dir
            if args.command == 'prepare': summary['scope'] = result['identity']['scope']
            if args.command == 'export': summary['mode'] = args.mode
            summary['quality_measured'] = False
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Interrupted. Repeat the same command; committed source batches/queries are reused.\n')
    except (ValueError, OSError, EOFError, KeyError, TypeError, sqlite3.Error, ImportError, zipfile.BadZipFile) as exc:
        parser.exit(2, f'S09 {args.command}: {exc}\n')


if __name__ == '__main__':
    main()
