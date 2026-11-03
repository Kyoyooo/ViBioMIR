"""Verified score reuse and submission packaging for an expanded S09 pool."""
import json
from pathlib import Path
import sqlite3
import zipfile

from vibiomir.io import canonical_json, file_hash, text_hash, write_json, writer_lock
from vibiomir.retrieval_contracts import checked, open_run, require
from vibiomir.stage9_core import score_key
from vibiomir.stage9_pipeline import prepared, load_packet, verify_scores, _worker_identity


def seed_cache(old_dir, old_workers, new_dir, output, worker_id=0):
    """Import verified exact query/passage/policy scores; never reuse chunk-ID alone."""
    old, new = prepared(old_dir), prepared(new_dir)
    policy = new['identity']['settings']['reranker']
    require(old['identity']['settings']['reranker'] == policy, 'Reranker policy differs')
    require(old['identity']['source'] == new['identity']['source'], 'Corpus snapshot differs')
    require(old['identity']['code'] == new['identity']['code'], 'Frozen scoring code differs')
    require(old['identity']['query_file_sha256'] == new['identity']['query_file_sha256'], 'Query snapshot differs')
    old_entries = {p['id']: p for p in old['packets']}
    root = Path(output)
    identity = _worker_identity(new, worker_id)
    open_run(root, identity)
    reused, missing, files = 0, 0, {}
    with writer_lock(root), sqlite3.connect(root / 'score_cache.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        for entry in new['packets']:
            if entry['worker_id'] != worker_id:
                continue
            packet = load_packet(new_dir, entry)
            previous = old_entries.get(entry['id'])
            require(previous is not None, 'Expanded run has an unknown query')
            original = load_packet(old_dir, previous)
            require(original['query'] == packet['query'], 'Query text changed')
            old_worker = Path(old_workers) / f"worker_{previous['worker_id']}"
            manifest = json.loads((old_worker / 'manifest.json').read_text(encoding='utf-8'))
            expected = _worker_identity(old, previous['worker_id'])
            require(manifest['identity'] == expected and manifest['signature'] == text_hash(canonical_json(expected))
                    and manifest['state'] == 'complete', 'Old worker not complete or mismatched')
            path = old_worker / f"query-{previous['id']}.json"
            rows = verify_scores(checked(path), original, previous, old, manifest['signature'])
            files[str(previous['id'])] = file_hash(path)
            scores = {}
            for item, value in zip(original['candidates'], rows):
                key = score_key(original['query'], item['retrieval_text'], policy)
                payload = {k: v for k, v in value.items() if k != 'chunk_id'}
                require(key not in scores or scores[key] == payload, 'Conflicting old scores for identical text')
                scores[key] = payload
            for item in packet['candidates']:
                key = score_key(packet['query'], item['retrieval_text'], policy)
                if key not in scores:
                    missing += 1
                    continue
                payload = canonical_json(scores[key])
                hit = db.execute('SELECT payload FROM scores WHERE key=?', (key,)).fetchone()
                require(hit is None or json.loads(hit[0]) == scores[key], 'Existing cache conflicts with verified score')
                db.execute('INSERT OR IGNORE INTO scores VALUES(?,?)', (key, payload))
                reused += 1
            db.commit()
    report = dict(old_signature=old['signature'], new_signature=new['signature'],
                  reused_pairs=reused, new_pairs=missing, old_score_files=files,
                  helper_sha256=file_hash(__file__))
    write_json(root / 'REUSE.json', report)
    return report


def package(results, query_ids, allowed_ids, destination):
    """Package only source-validated results with exact query and ID coverage."""
    results, destination = Path(results), Path(destination)
    report = json.loads((results / 'VALIDATION.json').read_text(encoding='utf-8'))
    require(report['validation'] == 'passed' and report['original_evidence_verified'] is True,
            'Original source validation is required')
    require(file_hash(results / 'manifest.json') == report['manifest_sha256'], 'Result manifest changed')
    path = results / 'predictions.json'
    require(file_hash(path) == report['predictions_sha256'], 'Prediction hash differs')
    payload = path.read_bytes()
    rows = json.loads(payload)
    require([r['id'] for r in rows] == query_ids and len(set(query_ids)) == len(query_ids), 'Query coverage differs')
    for row in rows:
        docs = row['relevant_docs']
        require(all(type(d) is int and d in allowed_ids for d in docs) and len(docs) == len(set(docs)), 'Invalid document IDs')
        for chunk in row['relevant_chunks']:
            require(type(chunk['doc_id']) is int and chunk['doc_id'] in docs and
                    isinstance(chunk['chunk_text'], str) and chunk['chunk_text'].strip(), 'Invalid evidence')
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'submission.json').write_bytes(payload)
    archive = destination / 'submission.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('submission.json', payload)
    with zipfile.ZipFile(archive) as z:
        require(z.namelist() == ['submission.json'] and z.read('submission.json') == payload, 'ZIP differs')
    write_json(destination / 'REPORT.json', dict(queries=len(rows), source_validation=report,
               submission_sha256=file_hash(path), zip_sha256=file_hash(archive), relevance_measured=False))
    return archive
