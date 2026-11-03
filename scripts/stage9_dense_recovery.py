"""Recover Stage-8 source bundles and validate dense candidates for frozen S09."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import zipfile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(canonical(value), encoding='utf-8')
    temporary.replace(path)


def signed(value):
    return {**value, 'signature': digest(canonical(value).encode())}


def checked(value):
    require(value.get('signature') == signed({k: v for k, v in value.items() if k != 'signature'})['signature'],
            'Signed record checksum differs')
    return value


def inspect_bundles(directory, expected):
    archives = sorted(Path(directory).glob('worker_*.zip'))
    require(archives, f'No Stage-8 input bundles in {directory}')
    plan, members, metadata = None, {}, {}
    for archive in archives:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            require(len(names) == len(set(names)), f'Duplicate ZIP members: {archive}')
            current = checked(json.loads(z.read('plan.json')))
            require(current['source'] == expected, f'Stage-8 source differs: {archive}')
            require(plan is None or current == plan, 'Mixed Stage-8 plans')
            plan = current
            for name, key in [('manifest.json', 'manifest_sha256'), ('VALIDATION.json', 'validation_sha256')]:
                data = z.read('chunks/' + name)
                require(digest(data) == expected[key], f'Source metadata differs: {archive}/{name}')
                metadata[name] = data
            for part in plan['parts']:
                index = part['index']
                prefix = f'chunks/batch-{index:06d}/'
                if prefix + 'documents.jsonl.gz' not in names:
                    continue
                require(prefix + 'batch.json' in names, f'Missing batch metadata: {prefix}')
                require(index not in members, f'Duplicate batch across bundles: {index}')
                require(digest(z.read(prefix + 'batch.json')) == part['batch_json_sha256'], 'Batch metadata differs')
                members[index] = (archive, prefix, z.getinfo(prefix + 'documents.jsonl.gz').file_size)
        print(f'Inspected {archive.name}; total unique batches: {len(members)}', flush=True)
    require(set(members) == set(range(expected['batches'])), 'Missing or unexpected source batches')
    require(len(plan['parts']) == expected['batches'], 'Plan batch count differs')
    require(sum(p['rows'] for p in plan['parts']) == expected['chunks'] and
            sum(p['documents'] for p in plan['parts']) == expected['documents'], 'Plan totals differ')
    return plan, members, metadata


def restore_chunks(directory, destination, expected):
    plan, members, metadata = inspect_bundles(directory, expected)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in metadata.items():
        if (destination / name).exists():
            require((destination / name).read_bytes() == data, 'Destination contains another source; use a new folder')
    missing_bytes = sum(size for index, (_, _, size) in members.items()
                        if not (destination / f'batch-{index:06d}/documents.jsonl.gz').is_file())
    require(shutil.disk_usage(destination).free > missing_bytes + 8 * 1024**3,
            'Insufficient local disk for source plus 8 GiB reserve')
    # Open each ZIP once. All output names come from integer indices, never arbitrary ZIP paths.
    done = 0
    for archive in sorted({v[0] for v in members.values()}):
        with zipfile.ZipFile(archive) as z:
            for index, (owner, prefix, _) in sorted(members.items()):
                if owner != archive:
                    continue
                part = plan['parts'][index]
                require(part['index'] == index, 'Plan parts are not indexed in order')
                target = destination / f'batch-{index:06d}'
                target.mkdir(exist_ok=True)
                for name, expected_hash in [('batch.json', part['batch_json_sha256']),
                                            ('documents.jsonl.gz', part['source_sha256'])]:
                    path = target / name
                    if path.is_file() and file_hash(path) == expected_hash:
                        continue
                    temporary = path.with_suffix(path.suffix + '.partial')
                    with z.open(prefix + name) as src, temporary.open('wb') as dst:
                        shutil.copyfileobj(src, dst, length=4 * 1024**2)
                    require(file_hash(temporary) == expected_hash, f'Corrupted payload: {prefix}{name}')
                    temporary.replace(path)
                done += 1
                if done == 1 or done % 25 == 0 or done == len(members):
                    print(f'Restored and verified {done}/{len(members)} batches', flush=True)
    require({p.name for p in destination.glob('batch-*')} == {f'batch-{i:06d}' for i in members},
            'Unexpected destination batches')
    for name, data in metadata.items():
        (destination / name).write_bytes(data)
    return plan


FIELDS = ('chunk_id', 'doc_id', 'derived_version_id', 'source_batch', 'source_row',
          'chunk_order', 'retrieval_text_sha256')


def adapt_candidates(results, chunks, plan, expected, query_path, queries, output):
    """Validate every candidate against original text before issuing an adapter report."""
    results, chunks, output = Path(results), Path(chunks), Path(output)
    manifest = read(results / 'manifest.json')
    ident = manifest['identity']
    require(manifest['signature'] == digest(canonical(ident).encode()) and manifest['state'] == 'complete',
            'Dense result manifest identity/state differs')
    require(ident['operation'] in ('dense-results', 'dense-v2-cached-candidates'), 'Unsupported candidate producer')
    require(ident['plan_signature'] == plan['signature'] and plan['source'] == expected, 'Candidate plan/source differs')
    require(ident['query_sha256'] == file_hash(query_path), 'Candidate query snapshot differs')
    require(manifest.get('corpus_complete') is True, 'Candidate producer reports incomplete corpus')
    input_hashes = {str(q['id']): file_hash(results / 'candidates' / f"query-{q['id']}.json") for q in queries}
    identity = dict(operation='dense-candidate-adapter-v1', source=expected,
                    input_manifest_sha256=file_hash(results / 'manifest.json'), query_sha256=file_hash(query_path),
                    input_query_hashes=input_hashes, adapter_sha256=file_hash(__file__))
    signature = digest(canonical(identity).encode())
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'manifest.json').exists():
        require(read(output / 'manifest.json')['identity'] == identity, 'Adapter inputs changed; use a new output folder')
    write(output / 'manifest.json', dict(identity=identity, signature=signature, state='building'))
    # This index is disposable; rebuilt on every validation attempt to avoid stale verification flags.
    with sqlite3.connect(output / 'verification.sqlite3') as db:
        db.execute('DROP TABLE IF EXISTS wanted')
        db.execute('CREATE TABLE wanted(id TEXT PRIMARY KEY,batch INTEGER,payload TEXT,verified INTEGER DEFAULT 0)')
        total = 0
        for number, q in enumerate(queries, 1):
            path = results / 'candidates' / f"query-{q['id']}.json"
            raw = path.read_bytes()
            require(digest(raw) == input_hashes[str(q['id'])], 'Candidate file changed while reading')
            record = checked(json.loads(raw))
            require(record['id'] == q['id'] and record['query'] == q['query'] and
                    record['run_signature'] == manifest['signature'], 'Candidate query/run binding differs')
            seen, previous = set(), math.inf
            for item in record['candidates']:
                require(all(k in item for k in FIELDS), 'Missing candidate mapping')
                require(isinstance(item['chunk_id'], str) and item['chunk_id'] and type(item['doc_id']) is int,
                        'Invalid candidate ID')
                require(all(type(item[k]) is int and item[k] >= 0 for k in ('source_batch', 'source_row', 'chunk_order')),
                        'Invalid source coordinates')
                require(item['source_batch'] < expected['batches'], 'Unknown batch')
                score = item['score']
                require(type(score) in (int, float) and math.isfinite(score) and score <= previous,
                        'Invalid or unsorted candidate scores')
                require(item['chunk_id'] not in seen, 'Duplicate candidate in query')
                seen.add(item['chunk_id']); previous = score
                payload = canonical({k: item[k] for k in FIELDS})
                old = db.execute('SELECT payload FROM wanted WHERE id=?', (item['chunk_id'],)).fetchone()
                require(old is None or old[0] == payload, 'Conflicting candidate mapping')
                db.execute('INSERT OR IGNORE INTO wanted(id,batch,payload) VALUES(?,?,?)',
                           (item['chunk_id'], item['source_batch'], payload))
            total += len(record['candidates'])
            write(output / f"query-{q['id']}.json", signed(dict(id=q['id'], query=q['query'],
                  run_signature=signature, candidates=record['candidates'])))
            if number % 100 == 0:
                print(f'Checked candidate records: {number}/{len(queries)}', flush=True)
        db.commit()
        db.execute('CREATE INDEX wanted_batch ON wanted(batch)')
        for part in plan['parts']:
            index = part['index']
            wanted = dict(db.execute('SELECT id,payload FROM wanted WHERE batch=?', (index,)))
            if not wanted:
                continue
            path = chunks / f'batch-{index:06d}/documents.jsonl.gz'
            require(file_hash(path) == part['source_sha256'], 'Restored source payload changed')
            matched = set()
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                for row_number, line in enumerate(stream):
                    document = json.loads(line)
                    for order, chunk in enumerate(document['chunks']):
                        key = chunk['chunk_id']
                        if key not in wanted:
                            continue
                        require(key not in matched, 'Duplicate source chunk ID')
                        actual = {**chunk, 'source_batch': index, 'source_row': row_number, 'chunk_order': order,
                                  'retrieval_text_sha256': digest(chunk['retrieval_text'].encode())}
                        require(canonical({k: actual[k] for k in FIELDS}) == wanted[key], 'Candidate does not match source text')
                        require(chunk['doc_id'] == document['doc_id'] and
                                chunk['derived_version_id'] == document['derived_version_id'] and
                                digest(chunk['chunk_text'].encode()) == chunk['text_sha256'], 'Source evidence differs')
                        matched.add(key)
            require(matched == set(wanted), f'Missing evidence in batch {index}')
            db.execute('UPDATE wanted SET verified=1 WHERE batch=?', (index,))
            db.commit()
            if index % 25 == 0:
                print(f'Verified source mappings through batch {index}/{expected["batches"]}', flush=True)
        require(db.execute('SELECT COUNT(*) FROM wanted WHERE verified=0').fetchone()[0] == 0, 'Unverified candidates')
    write(output / 'manifest.json', dict(identity=identity, signature=signature, state='complete',
                                        queries=len(queries), candidates=total))
    report = dict(validation='passed', validation_scope='candidate integrity and exact source mapping; not relevance',
                  manifest_sha256=file_hash(output / 'manifest.json'), query_sha256=file_hash(query_path),
                  queries=len(queries), candidates=total, relevance_measured=False)
    write(output / 'VALIDATION.json', report)
    return report
