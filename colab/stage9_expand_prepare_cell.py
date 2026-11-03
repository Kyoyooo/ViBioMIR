# Load this notebook's support code; frozen producer files are unchanged.
HELPER_SOURCE = "\"\"\"Verified score reuse and submission packaging for an expanded S09 pool.\"\"\"\nimport json\nfrom pathlib import Path\nimport sqlite3\nimport zipfile\n\nfrom vibiomir.io import canonical_json, file_hash, text_hash, write_json, writer_lock\nfrom vibiomir.retrieval_contracts import checked, open_run, require\nfrom vibiomir.stage9_core import score_key\nfrom vibiomir.stage9_pipeline import prepared, load_packet, verify_scores, _worker_identity\n\n\ndef seed_cache(old_dir, old_workers, new_dir, output, worker_id=0):\n    \"\"\"Import verified exact query/passage/policy scores; never reuse chunk-ID alone.\"\"\"\n    old, new = prepared(old_dir), prepared(new_dir)\n    policy = new['identity']['settings']['reranker']\n    require(old['identity']['settings']['reranker'] == policy, 'Reranker policy differs')\n    require(old['identity']['source'] == new['identity']['source'], 'Corpus snapshot differs')\n    require(old['identity']['code'] == new['identity']['code'], 'Frozen scoring code differs')\n    require(old['identity']['query_file_sha256'] == new['identity']['query_file_sha256'], 'Query snapshot differs')\n    old_entries = {p['id']: p for p in old['packets']}\n    root = Path(output)\n    identity = _worker_identity(new, worker_id)\n    open_run(root, identity)\n    reused, missing, files = 0, 0, {}\n    with writer_lock(root), sqlite3.connect(root / 'score_cache.sqlite3') as db:\n        db.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')\n        for entry in new['packets']:\n            if entry['worker_id'] != worker_id:\n                continue\n            packet = load_packet(new_dir, entry)\n            previous = old_entries.get(entry['id'])\n            require(previous is not None, 'Expanded run has an unknown query')\n            original = load_packet(old_dir, previous)\n            require(original['query'] == packet['query'], 'Query text changed')\n            old_worker = Path(old_workers) / f\"worker_{previous['worker_id']}\"\n            manifest = json.loads((old_worker / 'manifest.json').read_text(encoding='utf-8'))\n            expected = _worker_identity(old, previous['worker_id'])\n            require(manifest['identity'] == expected and manifest['signature'] == text_hash(canonical_json(expected))\n                    and manifest['state'] == 'complete', 'Old worker not complete or mismatched')\n            path = old_worker / f\"query-{previous['id']}.json\"\n            rows = verify_scores(checked(path), original, previous, old, manifest['signature'])\n            files[str(previous['id'])] = file_hash(path)\n            scores = {}\n            for item, value in zip(original['candidates'], rows):\n                key = score_key(original['query'], item['retrieval_text'], policy)\n                payload = {k: v for k, v in value.items() if k != 'chunk_id'}\n                require(key not in scores or scores[key] == payload, 'Conflicting old scores for identical text')\n                scores[key] = payload\n            for item in packet['candidates']:\n                key = score_key(packet['query'], item['retrieval_text'], policy)\n                if key not in scores:\n                    missing += 1\n                    continue\n                payload = canonical_json(scores[key])\n                hit = db.execute('SELECT payload FROM scores WHERE key=?', (key,)).fetchone()\n                require(hit is None or json.loads(hit[0]) == scores[key], 'Existing cache conflicts with verified score')\n                db.execute('INSERT OR IGNORE INTO scores VALUES(?,?)', (key, payload))\n                reused += 1\n            db.commit()\n    report = dict(old_signature=old['signature'], new_signature=new['signature'],\n                  reused_pairs=reused, new_pairs=missing, old_score_files=files,\n                  helper_sha256=file_hash(__file__))\n    write_json(root / 'REUSE.json', report)\n    return report\n\n\ndef package(results, query_ids, allowed_ids, destination):\n    \"\"\"Package only source-validated results with exact query and ID coverage.\"\"\"\n    results, destination = Path(results), Path(destination)\n    report = json.loads((results / 'VALIDATION.json').read_text(encoding='utf-8'))\n    require(report['validation'] == 'passed' and report['original_evidence_verified'] is True,\n            'Original source validation is required')\n    require(file_hash(results / 'manifest.json') == report['manifest_sha256'], 'Result manifest changed')\n    path = results / 'predictions.json'\n    require(file_hash(path) == report['predictions_sha256'], 'Prediction hash differs')\n    payload = path.read_bytes()\n    rows = json.loads(payload)\n    require([r['id'] for r in rows] == query_ids and len(set(query_ids)) == len(query_ids), 'Query coverage differs')\n    for row in rows:\n        docs = row['relevant_docs']\n        require(all(type(d) is int and d in allowed_ids for d in docs) and len(docs) == len(set(docs)), 'Invalid document IDs')\n        for chunk in row['relevant_chunks']:\n            require(type(chunk['doc_id']) is int and chunk['doc_id'] in docs and\n                    isinstance(chunk['chunk_text'], str) and chunk['chunk_text'].strip(), 'Invalid evidence')\n    destination.mkdir(parents=True, exist_ok=False)\n    (destination / 'submission.json').write_bytes(payload)\n    archive = destination / 'submission.zip'\n    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:\n        z.writestr('submission.json', payload)\n    with zipfile.ZipFile(archive) as z:\n        require(z.namelist() == ['submission.json'] and z.read('submission.json') == payload, 'ZIP differs')\n    write_json(destination / 'REPORT.json', dict(queries=len(rows), source_validation=report,\n               submission_sha256=file_hash(path), zip_sha256=file_hash(archive), relevance_measured=False))\n    return archive\n\r\n"
(PROJECT / 'stage9_expand_support.py').write_text(HELPER_SOURCE, encoding='utf-8')
from stage9_expand_support import seed_cache, package
from vibiomir.stage9_pipeline import prepare, bundle, export, validate_results, validate_worker
from vibiomir.io import write_json
def restore_bundle(archive_path, target):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as z:
        meta = json.loads(z.read('BUNDLE.json'))
        assert digest(canonical({k:v for k,v in meta.items() if k != 'signature'}).encode()) == meta['signature']
        assert len(z.namelist()) == len(set(z.namelist())) and set(z.namelist()) == set(meta['files']) | {'BUNDLE.json'}
        for name, expected in meta['files'].items():
            path = (target / name).resolve()
            assert target.resolve() in path.parents
            data = z.read(name)
            assert digest(data) == expected
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
import time
import threading
from contextlib import contextmanager

@contextmanager
def progress(label):
    started = time.monotonic()
    finished = threading.Event()
    def heartbeat():
        while not finished.wait(30):
            print(f'{label}: still running, {(time.monotonic()-started)/60:.1f} min', flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    print(label, flush=True)
    thread.start()
    try:
        yield
    finally:
        finished.set()
        thread.join()
        print(f'{label}: stopped after {(time.monotonic()-started)/60:.1f} min', flush=True)

def stage_store(name):
    destination = LOCAL_INPUTS / name
    is_chunks = name == 'chunks_stage5_full_structure'
    def check_snapshot(folder):
        if is_chunks:
            from vibiomir.retrieval_contracts import chunk_snapshot
            actual = chunk_snapshot(folder)
            expected = state['identity']['source']
            assert actual == expected, (f'Chunk store does not match frozen S09: {folder}\n'
                f'Actual: {canonical(actual)}\nExpected: {canonical(expected)}\n'
                'Use the original structural snapshot; equal batch counts alone are insufficient.')
    override = globals().get('CHUNK_STORE_OVERRIDE') if is_chunks else None
    if override is not None:
        override = Path(override)
        check_snapshot(override)  # Check before copying a large store.
    if (destination / '.copy_complete').is_file():
        check_snapshot(destination)
        return destination  # prepare/validation rechecks payloads
    extracted = override if override is not None else next((p for p in (ARTIFACTS_DIR / name, ROOT / name) if (p / 'manifest.json').is_file()), None)
    if extracted:
        check_snapshot(extracted)
        if extracted.resolve() == destination.resolve():
            return destination
        with progress(f'Copy {name}: {extracted} -> {destination}'):
            entries = [p for p in extracted.rglob('*') if p.is_file()]
            total = sum(p.stat().st_size for p in entries)
            assert shutil.disk_usage('/content').free > total + 8*1024**3, 'Insufficient local disk'
            copied = 0
            for number, src in enumerate(entries, 1):
                dst = destination / src.relative_to(extracted)
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Copy again after interruption; existence alone does not prove completeness.
                shutil.copy2(src, dst)
                copied += src.stat().st_size
                if number % 50 == 0 or number == len(entries):
                    print(f'{name}: {number}/{len(entries)} files, '
                          f'{copied/1024**3:.2f}/{total/1024**3:.2f} GiB', flush=True)
    else:
        assert ARTIFACTS_ZIP.is_file(), f'Missing store {name}; provide extracted store or current artifacts.zip.'
        with zipfile.ZipFile(ARTIFACTS_ZIP) as z:
            prefixes = [n[:-len('manifest.json')] for n in z.namelist() if n.endswith(name + '/manifest.json')]
            assert len(prefixes) == 1, f'Expected one {name} store in artifacts.zip, found {len(prefixes)}'
            members = [i for i in z.infolist() if i.filename.startswith(prefixes[0]) and not i.is_dir()]
            assert shutil.disk_usage('/content').free > sum(i.file_size for i in members) + 8*1024**3
            for info in members:
                path = (destination / info.filename[len(prefixes[0]):]).resolve()
                assert destination.resolve() in path.parents
                path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, path.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
    assert (destination / 'VALIDATION.json').is_file(), f'Missing validation for {name}'
    check_snapshot(destination)
    (destination / '.copy_complete').write_text('Copied; payloads are checked by the pipeline.')
    return destination
LOCAL_INPUTS.mkdir(parents=True, exist_ok=True)
chunk_dir = stage_store('chunks_stage5_full_structure')
# Keep local absolute paths stable across Colab restarts.
fixed_query = LOCAL_INPUTS / 'query.parquet'
shutil.copy2(query_path, fixed_query)
checkpoint = RUN_DRIVE / 'expanded_prepared.zip'
if checkpoint.is_file():
    restore_bundle(checkpoint, NEW_PREPARED)
    expanded = prepared(NEW_PREPARED)
else:
    config = json.loads(json.dumps(state['identity']['settings']))
    config.update(queries=str(fixed_query), chunk_dir=str(chunk_dir),
                  output_dir=str(NEW_PREPARED), rerank_top_k=RERANK_TOP_K,
                  workers=1, selection=LIMITS)
    for branch in config['branches']:
        name = Path(branch['candidate_dir']).name
        branch['candidate_dir'] = str(stage_store(name))
    config_path = LOCAL_INPUTS / 'expanded_config.json'
    write_json(config_path, config)
    with progress('Prepare expanded candidates on CPU'):
        expanded = prepare(config_path)
    local_zip = LOCAL_INPUTS / 'expanded_prepared.zip'
    bundle(NEW_PREPARED, local_zip)
    pending = checkpoint.with_suffix('.zip.tmp')
    shutil.copy2(local_zip, pending)
    assert file_hash(pending) == file_hash(local_zip)
    pending.replace(checkpoint)
assert expanded['identity']['settings']['rerank_top_k'] == RERANK_TOP_K
assert expanded['identity']['settings']['selection'] == LIMITS
assert expanded['identity']['settings']['workers'] == 1
assert expanded['identity']['query_file_sha256'] == file_hash(fixed_query)
assert len(expanded['packets']) == EXPECTED_QUERIES
# Normalize the already verified old worker layout with local symlinks.
old_layout = PROJECT / 'old_workers'
old_layout.mkdir(exist_ok=True)
for i, folder in worker_roots.items():
    link = old_layout / f'worker_{i}'
    if not link.exists(): link.symlink_to(folder.resolve(), target_is_directory=True)
with progress('Verify and reuse old scores'):
    reuse = seed_cache(PROJECT, old_layout, NEW_PREPARED, NEW_WORKERS / 'worker_0')
print({k: reuse[k] for k in ('reused_pairs', 'new_pairs')})
write_json(RUN_DRIVE / 'REUSE.json', reuse)
old_by_id = {x['id']:x for x in state['packets']}
diversity = []
for entry in expanded['packets']:
    new_packet = load_packet(NEW_PREPARED, entry)
    old_packet = load_packet(PROJECT, old_by_id[entry['id']])
    assert [x['chunk_id'] for x in new_packet['candidates'][:len(old_packet['candidates'])]] == [x['chunk_id'] for x in old_packet['candidates']], 'Pool prefix changed; this is not a pool-size-only experiment.'
    diversity.append(dict(id=entry['id'], old_documents=len({x['doc_id'] for x in old_packet['candidates']}),
                          new_documents=len({x['doc_id'] for x in new_packet['candidates']})))
write_json(RUN_DRIVE / 'POOL_DIVERSITY.json', diversity)
print('Prepared', expanded['candidates'], 'pairs. Check REUSE.json and POOL_DIVERSITY.json.')
