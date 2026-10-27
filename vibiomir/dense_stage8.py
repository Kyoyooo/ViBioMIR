"""Six independent CUDA workers, immutable embedding batches and verified merge."""
import argparse
import gzip
from itertools import islice, zip_longest
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile

from .chunk_batches import read_json
from .dense_models import DenseEncoder, MODELS, settings, tokenizer_signature, download_tokenizer
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .retrieval_contracts import (checked, signed, require, distinct, chunk_snapshot, source_parts, chunk_rows,
                                  mapping, open_run, read_jsonl_gz, queries)


def code_identity():
    return {name: file_hash(Path(__file__).with_name(name)) for name in
            ('dense_models.py', 'dense_stage8.py', 'retrieval_contracts.py', 'chunk_batches.py', 'io.py')}


def make_plan(config_path):
    c = settings(config_path)
    root = Path(c['run_dir'])
    distinct(root, c['chunk_dir'], c['tokenizer_dir'])
    snapshot = chunk_snapshot(c['chunk_dir'])
    parts = source_parts(c['chunk_dir'], snapshot)
    groups, loads = [[] for _ in range(c['workers'])], [0] * c['workers']
    weights = c.get('worker_weights', [1] * c['workers'])
    for part in sorted(parts, key=lambda p: (-p['rows'], p['index'])):
        worker = min(range(c['workers']), key=lambda i: (loads[i] / weights[i], len(groups[i]), i))
        groups[worker].append(part['index'])
        loads[worker] += part['rows']
    # Runtime paths are deliberately excluded from the portable encoding identity.
    encoding = {k: c[k] for k in ('model', 'max_tokens', 'window_overlap', 'storage_dtype')}
    encoding.update(model_spec=MODELS[c['model']], tokenizer=tokenizer_signature(c['tokenizer_dir'], c['model']),
                    aggregation='weighted mapped windows + L2; no ICD input')
    plan = signed({'schema': 'vibiomir-dense-plan-v1', 'source': snapshot, 'parts': parts, 'encoding': encoding,
                   'workers': [{'id': i, 'parts': sorted(group), 'rows': loads[i], 'weight': weights[i]} for i, group in enumerate(groups)],
                   'seed': c['seed'],
                   'code': code_identity(), 'balance': 'deterministic largest-first chunk-count proxy; not measured GPU time'})
    root.mkdir(parents=True, exist_ok=True)
    if (root / 'plan.json').exists():
        require(checked(root / 'plan.json') == plan, 'Existing dense plan differs; use a new run_dir')
    else:
        write_json(root / 'plan.json', plan)
    return plan


def read_plan(path):
    plan = checked(path)
    require(plan.get('schema') == 'vibiomir-dense-plan-v1' and plan['code'] == code_identity(), 'Plan schema/code differs; use the source revision packaged with it')
    require([w['id'] for w in plan['workers']] == list(range(len(plan['workers']))) and 1 <= len(plan['workers']) <= 6, 'Invalid worker IDs')
    assigned = [i for worker in plan['workers'] for i in worker['parts']]
    require(sorted(assigned) == list(range(len(plan['parts']))) and len(set(assigned)) == len(assigned), 'Plan has missing/duplicate batches')
    offset = 0
    for i, part in enumerate(plan['parts']):
        require(part['index'] == i and part['start'] == offset and part['rows'] >= 0, 'Plan row/batch offsets differ')
        offset += part['rows']
    require(offset == plan['source']['chunks'], 'Plan total differs')
    for worker in plan['workers']:
        require(worker['rows'] == sum(plan['parts'][i]['rows'] for i in worker['parts']), 'Worker total differs')
    return plan


def encoding_config(plan):
    return {k: plan['encoding'][k] for k in ('model', 'max_tokens', 'window_overlap', 'storage_dtype')}


def part_path(root, index):
    return Path(root) / f'batch-{index:06d}'


def check_part(root, part, plan, worker_id=None):
    info = checked(Path(root) / 'part.json')
    require(info['plan_signature'] == plan['signature'] and info['source'] == part and
            info['dimension'] == plan['encoding']['model_spec']['dimension'] and info['dtype'] == 'float16', 'Vector batch identity/shape differs')
    require(worker_id is None or info['worker_id'] == worker_id, 'Batch belongs to another worker')
    for name, digest in info['files'].items():
        require(name in {'vectors.npy', 'mapping.jsonl.gz'} and file_hash(Path(root) / name) == digest, 'Vector batch checksum differs')
    require(set(info['files']) == {'vectors.npy', 'mapping.jsonl.gz'}, 'Incomplete vector batch')
    return info


def vectors_at(path, rows):
    import numpy as np
    return np.load(Path(path) / 'vectors.npy', mmap_mode='r' if rows else None, allow_pickle=False)


def copy_part(source, destination, part, plan, worker_id):
    check_part(source, part, plan, worker_id)
    if Path(destination).exists():
        require(check_part(destination, part, plan, worker_id) == checked(Path(source) / 'part.json'), 'Saved batch differs')
        return
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pending-copy-', dir=Path(destination).parent) as temp:
        copied = Path(temp) / 'batch'
        shutil.copytree(source, copied)
        check_part(copied, part, plan, worker_id)
        copied.rename(destination)


def encode_worker(plan_path, chunk_dir, tokenizer_dir, output, worker_id, *, backup_dir=None,
                  batch_size=16, padded_tokens=8192, wave_chunks=128, stop_after_batches=None, encoder_factory=None):
    import numpy as np
    plan = read_plan(plan_path)
    require(type(worker_id) is int and 0 <= worker_id < len(plan['workers']), 'Invalid worker ID')
    require(type(wave_chunks) is int and 1 <= wave_chunks <= 1024 and batch_size > 0 and padded_tokens >= 512, 'Invalid GPU/wave budget')
    require(stop_after_batches is None or stop_after_batches > 0, 'Positive stop-after-batches required')
    require(chunk_snapshot(chunk_dir) == plan['source'], 'Worker source snapshot differs')
    require(tokenizer_signature(tokenizer_dir, plan['encoding']['model']) == plan['encoding']['tokenizer'], 'Worker tokenizer differs')
    distinct(output, chunk_dir, tokenizer_dir)
    identity = {'operation': 'dense-worker', 'plan_signature': plan['signature'], 'worker_id': worker_id}
    root = Path(output)
    manifest = open_run(root, identity)
    if backup_dir:
        distinct(backup_dir, root, chunk_dir)
        backup = Path(backup_dir)
        if (backup / 'manifest.json').exists():
            require(read_json(backup / 'manifest.json')['identity'] == identity, 'Backup belongs to another plan/worker')
        else:
            open_run(backup, identity)
    else:
        backup = None
    assignment = plan['workers'][worker_id]
    encoder, added = None, 0
    with writer_lock(root):
        if backup and (backup / 'MODEL_REGISTRY.json').exists() and not (root / 'MODEL_REGISTRY.json').exists():
            write_json(root / 'MODEL_REGISTRY.json', read_json(backup / 'MODEL_REGISTRY.json'))
        for index in assignment['parts']:
            part = plan['parts'][index]
            destination = part_path(root, index)
            if backup and part_path(backup, index).exists() and not destination.exists():
                copy_part(part_path(backup, index), destination, part, plan, worker_id)
            if destination.exists():
                check_part(destination, part, plan, worker_id)
            else:
                with tempfile.TemporaryDirectory(prefix='.pending-', dir=root) as temporary:
                    temp = Path(temporary)
                    dimension = plan['encoding']['model_spec']['dimension']
                    matrix = np.lib.format.open_memmap(temp / 'vectors.npy', mode='w+', dtype='float16', shape=(part['rows'], dimension)) if part['rows'] else None
                    if matrix is None:
                        np.save(temp / 'vectors.npy', np.empty((0, dimension), dtype='float16'), allow_pickle=False)
                    offset = 0
                    iterator = iter(chunk_rows(chunk_dir, part))
                    with gzip.open(temp / 'mapping.jsonl.gz', 'wt', encoding='utf-8', compresslevel=3) as stream:
                        while True:
                            wave = list(islice(iterator, wave_chunks))
                            if not wave:
                                break
                            if encoder is None:
                                encoder = (encoder_factory or DenseEncoder)(encoding_config(plan), tokenizer_dir, batch_size=batch_size, padded_tokens=padded_tokens)
                                write_json(root / 'MODEL_REGISTRY.json', encoder.registry)
                                manifest.setdefault('execution_history', []).append({'first_batch': index, 'batch_size': batch_size,
                                    'padded_tokens': padded_tokens, 'wave_chunks': wave_chunks, 'model': encoder.registry})
                            values, windows = encoder.encode([row['retrieval_text'] for row in wave])
                            values = np.asarray(values, dtype='float32')
                            require(values.shape == (len(wave), dimension) and len(windows) == len(wave) and np.isfinite(values).all(), 'Encoder shape/nonfinite error')
                            norms = np.linalg.norm(values, axis=1)
                            require((norms > 0).all(), 'Zero dense vector')
                            matrix[offset:offset + len(wave)] = values / norms[:, None]
                            for row, window_count in zip(wave, windows):
                                require(type(window_count) is int and window_count > 0, 'Invalid window count')
                                stream.write(canonical_json({**mapping(row), 'window_count': window_count}) + '\n')
                            offset += len(wave)
                    require(offset == part['rows'], 'Vector accounting differs')
                    if matrix is not None:
                        matrix.flush()
                        del matrix
                    info = signed({'plan_signature': plan['signature'], 'worker_id': worker_id, 'source': part,
                                   'dimension': dimension, 'dtype': 'float16',
                                   'model_registry': encoder.registry if encoder is not None else None,
                                   'files': {name: file_hash(temp / name) for name in ('vectors.npy', 'mapping.jsonl.gz')}})
                    write_json(temp / 'part.json', info)
                    temp.rename(destination)
                added += 1
            if part['rows'] and not (root / 'MODEL_REGISTRY.json').exists():
                registry = checked(destination / 'part.json').get('model_registry')
                require(registry is not None, 'Committed vector batch lacks model registry')
                write_json(root / 'MODEL_REGISTRY.json', registry)
            # A prior disconnect can leave local work ahead of Drive. Copy even
            # reused local batches before claiming backup completion.
            if backup:
                copy_part(destination, part_path(backup, index), part, plan, worker_id)
                if (root / 'MODEL_REGISTRY.json').exists():
                    write_json(backup / 'MODEL_REGISTRY.json', read_json(root / 'MODEL_REGISTRY.json'))
            manifest.update(state='paused')
            write_json(root / 'manifest.json', manifest)
            if backup:
                write_json(backup / 'manifest.json', manifest)
            print(json.dumps({'worker': worker_id, 'source_batch': index, 'rows': part['rows'], 'new_batches': added}), flush=True)
            if stop_after_batches and added >= stop_after_batches:
                return manifest
        manifest.update(state='complete', rows=assignment['rows'], batches=len(assignment['parts']))
        write_json(root / 'manifest.json', manifest)
        if backup:
            write_json(backup / 'manifest.json', manifest)
    return manifest


def validate_part(chunk_dir, directory, part, plan, worker_id=None):
    import numpy as np
    check_part(directory, part, plan, worker_id)
    matrix = vectors_at(directory, part['rows'])
    require(matrix.dtype == np.dtype('float16') and matrix.shape == (part['rows'], plan['encoding']['model_spec']['dimension']), 'Stored array shape/dtype differs')
    for start in range(0, len(matrix), 8192):
        block = np.asarray(matrix[start:start + 8192], dtype='float32')
        require(np.isfinite(block).all() and (np.abs(np.linalg.norm(block, axis=1) - 1) < 0.003).all(), 'Nonfinite/unnormalized stored vector')
    count = 0
    for expected, actual in zip_longest(chunk_rows(chunk_dir, part), read_jsonl_gz(Path(directory) / 'mapping.jsonl.gz')):
        require(expected is not None and actual is not None and
                all(actual.get(k) == v for k, v in mapping(expected).items()), 'Vector-to-chunk mapping differs')
        require(type(actual.get('window_count')) is int and actual['window_count'] > 0, 'Missing mapped-window accounting')
        count += 1
        yield actual
    require(count == part['rows'], 'Mapping count differs')


def validate_worker(plan_path, chunk_dir, output, worker_id):
    plan = read_plan(plan_path)
    require(type(worker_id) is int and 0 <= worker_id < len(plan['workers']), 'Invalid worker ID')
    manifest = read_json(Path(output) / 'manifest.json')
    require(manifest['state'] == 'complete' and manifest['identity'] == {'operation': 'dense-worker', 'plan_signature': plan['signature'], 'worker_id': worker_id}, 'Incomplete/wrong worker')
    count = 0
    for index in plan['workers'][worker_id]['parts']:
        count += sum(1 for _ in validate_part(chunk_dir, part_path(output, index), plan['parts'][index], plan, worker_id))
    require(count == plan['workers'][worker_id]['rows'], 'Worker count differs')
    if count:
        registry = read_json(Path(output) / 'MODEL_REGISTRY.json')
        spec = plan['encoding']['model_spec']
        require(all(registry[k] == spec[k] for k in ('model_id', 'revision', 'dimension', 'pooling')) and
                0 < registry['parameters'] <= 15000000000, 'Worker model registry differs')
    report = {'validation': 'passed', 'worker_id': worker_id, 'rows': count, 'plan_signature': plan['signature'], 'quality_measured': False}
    write_json(Path(output) / 'VALIDATION.json', report)
    return report


def merge(plan_path, chunk_dir, workers_dir, output):
    plan = read_plan(plan_path)
    require(chunk_snapshot(chunk_dir) == plan['source'], 'Merge source differs')
    distinct(output, chunk_dir, *(Path(workers_dir) / f"worker_{w['id']}" for w in plan['workers']))
    root = Path(output)
    manifest = open_run(root, {'operation': 'dense-merged', 'plan_signature': plan['signature'], 'source': plan['source'], 'encoding': plan['encoding']})
    with writer_lock(root), sqlite3.connect(root / 'mapping.sqlite3') as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS chunks(row_id INTEGER PRIMARY KEY,chunk_id TEXT UNIQUE,batch INTEGER,language TEXT,payload TEXT);
          CREATE INDEX IF NOT EXISTS chunks_language ON chunks(language,row_id);
          CREATE TABLE IF NOT EXISTS jobs(batch INTEGER PRIMARY KEY,part_sha TEXT,rows INTEGER);''')
        for worker in plan['workers']:
            folder = Path(workers_dir) / f"worker_{worker['id']}"
            require((folder / 'manifest.json').is_file(), f'Missing worker_{worker["id"]}; download all workers before merge')
            validate_worker(plan_path, chunk_dir, folder, worker['id'])
            for index in worker['parts']:
                part = plan['parts'][index]
                destination = part_path(root, index)
                copy_part(part_path(folder, index), destination, part, plan, worker['id'])
                digest = file_hash(destination / 'part.json')
                previous = db.execute('SELECT part_sha,rows FROM jobs WHERE batch=?', (index,)).fetchone()
                if previous:
                    require(previous == (digest, part['rows']), 'Merged batch changed')
                    continue
                with db:
                    for row in read_jsonl_gz(destination / 'mapping.jsonl.gz'):
                        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)', (row['row_id'], row['chunk_id'], index, row.get('language_hint'), canonical_json(row)))
                    db.execute('INSERT INTO jobs VALUES (?,?,?)', (index, digest, part['rows']))
        count, low, high = db.execute('SELECT COUNT(*),MIN(row_id),MAX(row_id) FROM chunks').fetchone()
        require(count == plan['source']['chunks'] and (not count or (low, high) == (0, count - 1)) and
                db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == len(plan['parts']), 'Merged coverage has gaps or duplicates')
        manifest.update(state='complete', rows=count, batches=len(plan['parts']))
        write_json(root / 'manifest.json', manifest)
        write_json(root / 'plan.json', plan)
        write_json(root / 'VALIDATION.json', {'validation': 'passed', 'rows': count, 'plan_signature': plan['signature'],
                   'mapping_checked': True, 'vectors_checked': True, 'quality_measured': False,
                   'mapping_sha256': file_hash(root / 'mapping.sqlite3'), 'manifest_sha256': file_hash(root / 'manifest.json')})
    return manifest


def encode_queries(plan_path, tokenizer_dir, query_file, output, *, batch_size=16, encoder_factory=None):
    import numpy as np
    plan = read_plan(plan_path)
    require(tokenizer_signature(tokenizer_dir, plan['encoding']['model']) == plan['encoding']['tokenizer'], 'Query tokenizer differs')
    root = Path(output)
    distinct(root, tokenizer_dir)
    identity = {'operation': 'dense-queries', 'plan_signature': plan['signature'], 'query_sha256': file_hash(query_file)}
    manifest = open_run(root, identity)
    if manifest['state'] == 'complete':
        require(file_hash(root / 'vectors.npy') == manifest['vectors_sha256'], 'Query vectors changed')
        return manifest
    rows = queries(query_file)
    with writer_lock(root):
        encoder = (encoder_factory or DenseEncoder)(encoding_config(plan), tokenizer_dir, batch_size=batch_size)
        matrix = np.empty((len(rows), plan['encoding']['model_spec']['dimension']), dtype='float16')
        windows = []
        for start in range(0, len(rows), 64):
            values, counts = encoder.encode([q['query'] for q in rows[start:start + 64]])
            values = np.asarray(values, dtype='float32')
            require(values.shape == (len(rows[start:start + 64]), plan['encoding']['model_spec']['dimension']) and
                    len(counts) == len(values) and np.isfinite(values).all(), 'Invalid query encoder output')
            norms = np.linalg.norm(values, axis=1)
            require((norms > 0).all() and all(type(n) is int and n > 0 for n in counts), 'Zero query vector or missing window accounting')
            values /= norms[:, None]
            matrix[start:start + len(values)] = values
            windows.extend(counts)
        with (root / 'vectors.npy.partial').open('wb') as stream:
            np.save(stream, matrix, allow_pickle=False)
        (root / 'vectors.npy.partial').replace(root / 'vectors.npy')
        write_json(root / 'queries.json', [{**row, 'window_count': count} for row, count in zip(rows, windows)])
        write_json(root / 'MODEL_REGISTRY.json', encoder.registry)
        manifest.update(state='complete', queries=len(rows), vectors_sha256=file_hash(root / 'vectors.npy'),
                        queries_sha256=file_hash(root / 'queries.json'))
        write_json(root / 'manifest.json', manifest)
    return manifest


def bundles(config_path, *, query_file=None):
    c = settings(config_path)
    plan_path = Path(c['run_dir']) / 'plan.json'
    plan = read_plan(plan_path)
    require(chunk_snapshot(c['chunk_dir']) == plan['source'], 'Bundle source differs')
    require(tokenizer_signature(c['tokenizer_dir'], c['model']) == plan['encoding']['tokenizer'], 'Bundle tokenizer differs')
    project = Path(__file__).resolve().parents[1]
    output = Path(c['run_dir']) / 'bundles'
    output.mkdir(exist_ok=True)
    results = []
    for worker in plan['workers']:
        target = output / f"worker_{worker['id']}.zip"
        entries = [(p, p.relative_to(project).as_posix()) for p in sorted((project / 'vibiomir').glob('*.py'))]
        entries += [(p, p.relative_to(project).as_posix()) for p in sorted((project / 'tests').glob('test_stage78*.py'))]
        for name in ('requirements-stage3.txt', 'requirements-stage4.txt', 'requirements-stage5.txt', 'requirements-stage8.txt',
                     'requirements-stage8-colab.txt', 'docs/RETRIEVAL_STAGES7_8.md', 'colab/stage8_dense_worker.ipynb'):
            entries.append((project / name, name))
        entries.append((plan_path, 'plan.json'))
        for name in ('manifest.json', 'VALIDATION.json'):
            entries.append((Path(c['chunk_dir']) / name, f'chunks/{name}'))
        for index in worker['parts']:
            part = plan['parts'][index]
            # Verification occurs before packaging; do not trust file names alone.
            source = part_path(c['chunk_dir'], index)
            require(file_hash(source / 'documents.jsonl.gz') == part['source_sha256'] and file_hash(source / 'batch.json') == part['batch_json_sha256'], 'Bundle input changed')
            for name in ('batch.json', 'documents.jsonl.gz'):
                entries.append((source / name, f'chunks/{source.name}/{name}'))
        entries += [(p, f'tokenizer/{p.name}') for p in Path(c['tokenizer_dir']).iterdir() if p.is_file()]
        if query_file:
            queries(query_file)
            entries.append((Path(query_file), 'queries' + Path(query_file).suffix))
        bundle_identity = signed({'plan_signature': plan['signature'], 'worker_id': worker['id'],
            'query_sha256': file_hash(query_file) if query_file else None,
            'source_files': {name: file_hash(path) for path, name in entries if not name.startswith('chunks/batch-')}})
        if target.exists():
            with zipfile.ZipFile(target) as archive:
                require(json.loads(archive.read('BUNDLE.json')) == bundle_identity, 'Existing bundle source/query differs; use a new run_dir or archive the old bundles')
            results.append(str(target))
            continue
        with zipfile.ZipFile(target.with_suffix('.zip.partial'), 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr('BUNDLE.json', json.dumps(bundle_identity, ensure_ascii=False))
            for path, name in entries:
                archive.write(path, name)
        target.with_suffix('.zip.partial').replace(target)
        results.append(str(target))
    return {'bundles': results, 'workers': len(results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'bundle'):
        sub = commands.add_parser(name)
        sub.add_argument('--config', default='configs/dense_stage8.json')
        if name == 'bundle':
            sub.add_argument('--queries', type=Path)
    sub = commands.add_parser('download-tokenizer')
    sub.add_argument('--model', choices=MODELS, required=True)
    sub.add_argument('--output-dir', required=True, type=Path)
    for name in ('encode-worker', 'validate-worker', 'merge', 'encode-queries'):
        sub = commands.add_parser(name)
        sub.add_argument('--plan', required=True, type=Path)
        sub.add_argument('--output-dir', required=True, type=Path)
        if name != 'encode-queries':
            sub.add_argument('--chunk-dir', required=True, type=Path)
        if name in {'encode-worker', 'validate-worker'}:
            sub.add_argument('--worker-id', type=int, required=True)
        if name in {'encode-worker', 'encode-queries'}:
            sub.add_argument('--tokenizer-dir', type=Path, required=True)
            sub.add_argument('--batch-size', type=int, default=16)
        if name == 'encode-worker':
            sub.add_argument('--backup-dir', type=Path)
            sub.add_argument('--padded-tokens', type=int, default=8192)
            sub.add_argument('--wave-chunks', type=int, default=128)
            sub.add_argument('--stop-after-batches', type=int)
        if name == 'merge':
            sub.add_argument('--workers-dir', type=Path, required=True)
        if name == 'encode-queries':
            sub.add_argument('--queries', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'plan':
            result = make_plan(args.config)
        elif args.command == 'bundle':
            result = bundles(args.config, query_file=args.queries)
        elif args.command == 'download-tokenizer':
            download_tokenizer(args.model, args.output_dir)
            result = {'state': 'tokenizer saved'}
        elif args.command == 'encode-worker':
            result = encode_worker(args.plan, args.chunk_dir, args.tokenizer_dir, args.output_dir, args.worker_id, backup_dir=args.backup_dir,
                batch_size=args.batch_size, padded_tokens=args.padded_tokens, wave_chunks=args.wave_chunks, stop_after_batches=args.stop_after_batches)
        elif args.command == 'validate-worker':
            result = validate_worker(args.plan, args.chunk_dir, args.output_dir, args.worker_id)
        elif args.command == 'merge':
            result = merge(args.plan, args.chunk_dir, args.workers_dir, args.output_dir)
        else:
            result = encode_queries(args.plan, args.tokenizer_dir, args.queries, args.output_dir, batch_size=args.batch_size)
        print(json.dumps({k: v for k, v in result.items() if k not in {'identity', 'parts', 'code'}}, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
