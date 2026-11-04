"""Freeze partial retrieval, rerank with baseline evidence, export query-level fallback."""
from collections import defaultdict
import json
import math
from pathlib import Path
import time
import zipfile

try:
    from . import corpus_multiretrieval as corpus
except ImportError:
    import corpus_multiretrieval as corpus

core, multi, recovery = corpus.core, corpus.multi, corpus.recovery


def load_baseline(archive, query_file):
    import pyarrow.parquet as pq
    core.require(core.file_hash(query_file) == core.EXPECTED_QUERY_SHA256, 'Wrong official query file')
    with zipfile.ZipFile(archive) as z:
        core.require(z.namelist() == ['submission.json'], 'Expected one root submission.json')
        raw = z.read('submission.json')
    rows = json.loads(raw)
    queries = pq.read_table(query_file, columns=['id', 'query']).to_pylist()
    core.validate_input_rows(rows, queries, 1200)
    inputs = dict(baseline_zip_sha256=core.file_hash(archive), baseline_json_sha256=core.digest(raw),
                  query_sha256=core.file_hash(query_file))
    return rows, queries, inputs, raw


def read_signed_bytes(path):
    raw = Path(path).read_bytes()
    return core.checked(json.loads(raw)), raw


def snapshot_retrieval(old_run, destination, queries, roles=('qwen', 'bge', 'e5', 'bm25')):
    """Import signed data, never call the old producer's check_plan or alter it."""
    old_run, destination = Path(old_run), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination/'SNAPSHOT.json'
    if manifest_path.exists():
        manifest, _ = read_signed_bytes(manifest_path)
        core.require(manifest['old_run'] == str(old_run) and manifest['roles'] == list(roles), 'Use a new snapshot directory')
        core.require(manifest['official_queries_sha256'] == core.signature(queries), 'Query set changed')
        return manifest
    plan, plan_raw = read_signed_bytes(old_run/'PLAN.json')
    core.require(plan['operation'] == 'corpus-multiretrieval-v1' and plan['source'] == corpus.SOURCE
        and plan['query_sha256'] == core.EXPECTED_QUERY_SHA256, 'Unsupported source/query producer')
    official = {q['id']: q['query'] for q in queries}
    core.require(len({q['id'] for q in plan['queries']}) == len(plan['queries']) and
        all(official.get(q['id']) == q['query'] for q in plan['queries']), 'Producer query text differs')
    core.require(plan['batch_indices'] == list(range(len(plan['batch_indices']))) and
        0 < len(plan['batch_indices']) <= corpus.SOURCE['batches'], 'Unsupported source coverage')
    (destination/'PRODUCER_PLAN.json').write_bytes(plan_raw)
    files, coverage, missing = [], {}, []
    for role in roles:
        core.require(role in corpus.BRANCHES, 'Unsupported retrieval role')
        seen, partitions, accepted = set(), set(), 0
        for directory in sorted((old_run/'retrieval'/role).glob('part-*')):
            options = []
            for name in ('checkpoint.json', 'result.json'):
                path = directory/name
                if path.is_file():
                    value, raw = read_signed_bytes(path)
                    options.append((len(value['completed_batches']), value, raw, path))
            if not options:
                continue
            _, value, raw, source_path = max(options, key=lambda option: option[0])
            identity = value['identity']
            shard, shards = identity['shard'], identity['shards']
            core.require(identity['plan'] == plan['signature'] and identity['role'] == role and
                directory.name == f'part-{shard}-of-{shards}' and 0 <= shard < shards <= 32, 'Checkpoint identity differs')
            expected = [i for i in plan['batch_indices'] if i % shards == shard]
            completed = value['completed_batches']
            core.require(completed == expected[:len(completed)] and not seen.intersection(completed),
                         'Invalid/duplicate checkpoint batch coverage')
            partitions.add(shards)
            core.require(len(partitions) == 1, 'Mixed partition counts in one branch')
            if not completed or not value['hits']:
                continue
            core.require(len(value['hits']) == len(plan['queries']), 'Checkpoint query count differs')
            core.require(all(len(hits) <= plan['top_k'] and all(isinstance(k, str) and type(s) in (int, float)
                and math.isfinite(s) for k, s in hits.items()) for hits in value['hits']), 'Invalid candidate scores')
            seen.update(completed)
            target = destination/f'{role}-{shard}-of-{shards}.json'
            target.write_bytes(raw)
            files.append(dict(role=role, name=target.name, sha256=core.digest(raw),
                source_path=str(source_path), completed_batches=completed))
            accepted += 1
        coverage[role] = len(seen)
        if not accepted:
            missing.append(role)
    manifest = core.signed(dict(operation='partial-retrieval-snapshot-v1', old_run=str(old_run),
        producer_plan_sha256=core.digest(plan_raw), roles=list(roles), files=files,
        coverage_batches=coverage, unavailable_roles=missing, official_queries_sha256=core.signature(queries),
        total_source_batches=corpus.SOURCE['batches'], snapshot_is_full_corpus=bool(files) and
        all(coverage.get(role) == corpus.SOURCE['batches'] for role in roles)))
    core.write(manifest_path, manifest)
    return manifest


def frozen_hits(snapshot_dir):
    snapshot_dir = Path(snapshot_dir)
    manifest, _ = read_signed_bytes(snapshot_dir/'SNAPSHOT.json')
    plan_path = snapshot_dir/'PRODUCER_PLAN.json'
    core.require(core.file_hash(plan_path) == manifest['producer_plan_sha256'], 'Frozen producer plan changed')
    plan, _ = read_signed_bytes(plan_path)
    roles = {role: [dict() for _ in plan['queries']] for role in manifest['roles']}
    batches = set()
    for receipt in manifest['files']:
        path = snapshot_dir/receipt['name']
        value, raw = read_signed_bytes(path)
        core.require(core.digest(raw) == receipt['sha256'], 'Frozen retrieval changed')
        batches.update(receipt['completed_batches'])
        role = receipt['role']
        for index, hits in enumerate(value['hits']):
            for key, score in hits.items():
                roles[role][index][key] = max(score, roles[role][index].get(key, -float('inf')))
            roles[role][index] = corpus.trim(roles[role][index], plan['top_k'])
    return manifest, plan['queries'], roles, sorted(batches)


def new_plan(inputs, queries, snapshot, max_new=20, model='bge'):
    core.require(type(max_new) is int and 0 < max_new <= 200 and model in ('bge', 'qwen'), 'Invalid fallback settings')
    core.require(snapshot['official_queries_sha256'] == core.signature(queries), 'Snapshot belongs to another query set')
    return core.signed(dict(operation='partial-candidates-baseline-fallback-v1', inputs=inputs,
        query_identity=core.signature(queries), snapshot_signature=snapshot['signature'], source=corpus.SOURCE,
        max_new_candidates=max_new, model=model, policy=multi.policies()[model],
        model_input='chunk_text_for_both_baseline_and_new', selection=dict(max_docs=120, max_chunks=120, per_doc=6),
        code={name: core.file_hash(path) for name, path in dict(fallback=__file__, corpus=corpus.__file__,
            core=core.__file__, multi=multi.__file__, recovery=recovery.__file__).items()}))


def validate_run(plan, rows, queries, inputs):
    core.checked(plan)
    core.require(plan['inputs'] == inputs and plan['query_identity'] == core.signature(queries), 'Baseline/query identity differs')
    core.validate_input_rows(rows, queries, len(queries))
    for name, path in dict(fallback=__file__, corpus=corpus.__file__, core=core.__file__,
                           multi=multi.__file__, recovery=recovery.__file__).items():
        core.require(plan['code'][name] == core.file_hash(path), 'Fallback support code changed')


def prepare(plan, run, rows, queries, inputs, snapshot_dir, bundles, local_db):
    """Hydrate only new top candidates from batches covered by the frozen snapshot."""
    import sqlite3
    validate_run(plan, rows, queries, inputs)
    snapshot, producer_queries, roles, batches = frozen_hits(snapshot_dir)
    core.require(snapshot['signature'] == plan['snapshot_signature'], 'Wrong snapshot')
    source_plan, members, _ = recovery.inspect_bundles(bundles, plan['source'])
    pools = {q['id']: corpus.fuse_candidates({role: hits[index] for role, hits in roles.items()},
        plan['max_new_candidates']) for index, q in enumerate(producer_queries)}
    wanted = {c['chunk_id'] for pool in pools.values() for c in pool}
    local_db = Path(local_db)
    local_db.parent.mkdir(parents=True, exist_ok=True)
    output = Path(run)/'prepared'
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(local_db) as db:
        db.execute('CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('DELETE FROM evidence')
        for number, index in enumerate(batches, 1):
            for chunk in corpus.source_batch(source_plan, members, index):
                if chunk['chunk_id'] in wanted:
                    db.execute('INSERT INTO evidence VALUES(?,?)', (chunk['chunk_id'], core.canonical(chunk)))
            if number % 20 == 0 or number == len(batches):
                print(f'Read original evidence: {number}/{len(batches)} covered batches', flush=True)
        db.commit()
        core.require(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == len(wanted), 'Checkpoint candidate absent from verified source')
        files, diagnostics = {}, []
        for row, query in zip(rows, queries):
            candidates, seen = [], set()
            for index, c in enumerate(row['relevant_chunks']):
                key = (c['doc_id'], c['chunk_text'])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(dict(candidate_id=core.signature([row['id'], *key]), doc_id=c['doc_id'],
                    chunk_text=c['chunk_text'], origin='baseline', baseline_index=index, source_verified=False))
            additions = 0
            for hit in pools.get(query['id'], []):
                raw = db.execute('SELECT payload FROM evidence WHERE id=?', (hit['chunk_id'],)).fetchone()
                source = json.loads(raw[0])
                key = (source['doc_id'], source['chunk_text'])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({**source, **hit, 'candidate_id':core.signature([row['id'], *key]),
                                   'origin':'partial_retrieval', 'source_verified':True})
                additions += 1
            if additions:
                record = core.signed(dict(plan=plan['signature'], **query, candidates=candidates,
                    baseline_row_sha256=core.signature(row), additions=additions))
                path = output/f'query-{query["id"]}.json'
                core.write(path, record)
                files[str(query['id'])] = core.file_hash(path)
            diagnostics.append(dict(id=query['id'], new_candidates=additions,
                new_document_candidates=sum(c['origin'] == 'partial_retrieval' and c['doc_id'] not in row['relevant_docs'] for c in candidates)))
    report = core.signed(dict(plan=plan['signature'], state='complete', files=files, diagnostics=diagnostics,
        eligible_queries=len(files), processed_source_batches=len(batches), baseline_source_verified=False,
        new_candidate_source_verified=True))
    core.write(output/'manifest.json', report)
    return report


def load_packet(plan, run, query, manifest):
    core.require(manifest['plan'] == plan['signature'], 'Prepared manifest belongs to another run')
    path = Path(run)/'prepared'/f'query-{query["id"]}.json'
    core.require(core.file_hash(path) == manifest['files'][str(query['id'])], 'Prepared packet changed')
    value, _ = read_signed_bytes(path)
    core.require(value['plan'] == plan['signature'] and value['id'] == query['id'] and value['query'] == query['query'], 'Packet query differs')
    return value


def valid_scores(plan, item, record):
    core.checked(record)
    core.require(record['plan'] == plan['signature'] and record['packet_signature'] == item['signature']
        and record['id'] == item['id'] and record['policy'] == plan['policy'], 'Score identity differs')
    scores = record['scores']
    core.require(len(scores) == len(item['candidates']) and all(type(s) in (int, float) and math.isfinite(s) for s in scores), 'Invalid score array')
    return scores


def score(plan, run, queries, stop_after_queries=5, time_budget_minutes=60, shard=0, shards=1,
          batch_size=8, padded_tokens=8192, scorer_factory=multi.ModelScorer):
    core.require(0 <= shard < shards <= 32 and time_budget_minutes > 0, 'Invalid shard/time budget')
    manifest, _ = read_signed_bytes(Path(run)/'prepared/manifest.json')
    output = Path(run)/'scores'
    output.mkdir(parents=True, exist_ok=True)
    scorer, new, reused, errors, started = None, 0, 0, [], time.monotonic()
    for index, query in enumerate(queries):
        if index % shards != shard or str(query['id']) not in manifest['files']:
            continue
        item = load_packet(plan, run, query, manifest)
        path = output/f'query-{query["id"]}.json'
        if path.exists():
            valid_scores(plan, item, core.read(path))
            reused += 1
            continue
        if (stop_after_queries is not None and new >= stop_after_queries) or time.monotonic()-started >= time_budget_minutes*60:
            break
        if scorer is None:
            # A missing GPU/model is a session error, not 1200 separate query errors.
            scorer = scorer_factory(plan['policy'], batch_size=batch_size, padded_tokens=padded_tokens)
        try:
            query_started = time.monotonic()
            values = scorer.score(query['query'], [c['chunk_text'] for c in item['candidates']])
            record = core.signed(dict(plan=plan['signature'], packet_signature=item['signature'], id=query['id'],
                policy=plan['policy'], scores=values, registry=getattr(scorer, 'registry', {})))
            valid_scores(plan, item, record)
            core.write(path, record)
            new += 1
            print(f'Query {query["id"]}: {len(values)} pairs, {time.monotonic()-query_started:.1f}s, saved', flush=True)
        except ValueError as exc:
            errors.append(dict(id=query['id'], error=str(exc)))
            core.write(output/f'error-{query["id"]}.json', errors[-1])
            print(f'Query {query["id"]}: baseline fallback; {exc}', flush=True)
    return dict(new_queries=new, reused_queries=reused, errors=errors, elapsed_seconds=round(time.monotonic()-started, 1))


def select(item, scores, baseline, update_documents=False):
    ranked = sorted(zip(item['candidates'], scores), key=lambda pair: (-pair[1], pair[0]['candidate_id']))
    docs = list(baseline['relevant_docs'])
    if update_documents:
        docs = list(dict.fromkeys([c['doc_id'] for c, _ in ranked]+docs))[:120]
    selected, counts, versions = [], defaultdict(int), {}
    for c, _ in ranked:
        d = c['doc_id']
        if d not in docs or counts[d] >= 6 or len(selected) >= 120:
            continue
        if c['origin'] == 'partial_retrieval' and d in versions and versions[d] != c['derived_version_id']:
            continue
        # Without a corpus mapping for baseline evidence, avoid mixing new and old spans within one doc.
        peers = [peer for peer in selected if peer['doc_id'] == d]
        if any(peer['origin'] != c['origin'] for peer in peers):
            continue
        if any(peer['chunk_text'] == c['chunk_text'] for peer in peers):
            continue
        if c['origin'] == 'partial_retrieval':
            overlap = False
            for peer in peers:
                if c.get('region_id') == peer.get('region_id') and all(k in c and k in peer for k in ('source_start', 'source_end')):
                    intersection = max(0, min(c['source_end'], peer['source_end'])-max(c['source_start'], peer['source_start']))
                    union = max(c['source_end'], peer['source_end'])-min(c['source_start'], peer['source_start'])
                    overlap |= union > 0 and intersection/union >= .8
            if overlap:
                continue
            versions[d] = c['derived_version_id']
        selected.append(c)
        counts[d] += 1
    prediction = dict(id=baseline['id'], relevant_docs=docs, relevant_chunks=[
        dict(doc_id=c['doc_id'], chunk_text=c['chunk_text'], chunk_order=i) for i, c in enumerate(selected)])
    return prediction, selected


def export(plan, run, rows, queries, inputs, baseline_raw, destination, allowed_ids=None, update_documents=False):
    validate_run(plan, rows, queries, inputs)
    core.require(core.digest(baseline_raw) == inputs['baseline_json_sha256'], 'Baseline bytes changed')
    output = Path(destination)
    core.require(not output.exists(), 'Choose a new export directory')
    manifest_path = Path(run)/'prepared/manifest.json'
    manifest = read_signed_bytes(manifest_path)[0] if manifest_path.exists() else None
    predictions, audit, provenance = [], [], []
    for baseline, query in zip(rows, queries):
        prediction, selected, reason = baseline, [], 'baseline_no_prepared_candidates'
        path = Path(run)/'scores'/f'query-{query["id"]}.json'
        if manifest and str(query['id']) in manifest['files']:
            reason = 'baseline_missing_score'
            if path.exists():
                try:
                    item = load_packet(plan, run, query, manifest)
                    core.require(item['baseline_row_sha256'] == core.signature(baseline), 'Baseline row changed')
                    values = valid_scores(plan, item, core.read(path))
                    proposed, chosen = select(item, values, baseline, update_documents)
                    core.require(proposed['relevant_chunks'], 'Empty selection')
                    core.require(allowed_ids is not None and set(proposed['relevant_docs']) <= allowed_ids,
                                 'Official ID allowlist required for changed queries')
                    prediction, selected, reason = proposed, chosen, 'reranked'
                except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
                    reason = 'baseline_invalid_result: '+str(exc)
        predictions.append(prediction)
        material_change = (prediction['relevant_docs'] != baseline['relevant_docs'] or
            {(c['doc_id'], c['chunk_text']) for c in prediction['relevant_chunks']} !=
            {(c['doc_id'], c['chunk_text']) for c in baseline['relevant_chunks']})
        audit.append(dict(id=query['id'], status=reason, material_change=material_change,
            selected_new_candidates=sum(c['origin'] == 'partial_retrieval' for c in selected)))
        if reason == 'reranked':
            provenance.append(dict(id=query['id'], selected=[{k: c.get(k) for k in (
                'candidate_id', 'doc_id', 'origin', 'source_verified', 'chunk_id', 'derived_version_id',
                'source_batch', 'source_row', 'source_chunk_order', 'region_id', 'source_start', 'source_end',
                'text_sha256', 'baseline_index', 'branches')} for c in selected]))
    core.validate_input_rows(predictions, queries, len(rows))
    if allowed_ids is not None:
        core.require(all(set(row['relevant_docs']) <= allowed_ids for row in predictions), 'Baseline contains invalid official document IDs')
    payload = baseline_raw if predictions == rows else core.canonical(predictions).encode()
    output.mkdir(parents=True)
    (output/'submission.json').write_bytes(payload)
    archive_path = output/'submission.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('submission.json', payload)
    with zipfile.ZipFile(archive_path) as z:
        core.require(z.namelist() == ['submission.json'] and z.read('submission.json') == payload, 'ZIP verification failed')
    core.write(output/'QUERY_AUDIT.json', audit)
    with (output/'EVIDENCE_PROVENANCE.jsonl').open('w', encoding='utf-8') as stream:
        for row in provenance:
            stream.write(core.canonical(row)+'\n')
    report = dict(plan_signature=plan['signature'], queries=len(rows),
        reranked_queries=sum(a['status'] == 'reranked' for a in audit),
        fallback_queries=sum(a['status'] != 'reranked' for a in audit),
        materially_changed_queries=sum(a['material_change'] for a in audit),
        selected_new_candidate_occurrences=sum(a['selected_new_candidates'] for a in audit),
        update_documents=update_documents, new_candidate_source_verified=True, baseline_source_verified=False,
        partial_retrieval=True, relevance_measured=False, official_lcs_measured=False,
        official_ids_verified=allowed_ids is not None, baseline_json_sha256=inputs['baseline_json_sha256'],
        submission_json_sha256=core.digest(payload), submission_zip_sha256=core.file_hash(archive_path))
    core.write(output/'REPORT.json', report)
    return report
