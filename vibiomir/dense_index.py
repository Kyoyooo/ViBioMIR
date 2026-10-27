"""CPU IVF-PQ or disk-exact search over the merged S08 float16 vector store."""
import argparse
import heapq
import json
import math
from pathlib import Path
import random
import sqlite3

from .chunk_batches import read_json
from .dense_stage8 import read_plan, part_path, check_part, vectors_at
from .io import file_hash, write_json, writer_lock
from .retrieval_contracts import require, open_run, distinct, checked, signed


def normalized(matrix):
    import numpy as np
    matrix = np.asarray(matrix, dtype='float32')
    norms = np.linalg.norm(matrix, axis=1)
    require(np.isfinite(matrix).all() and (norms > 0).all(), 'Invalid stored vector')
    return np.ascontiguousarray(matrix / norms[:, None])


def ready(root):
    root = Path(root)
    require((root / 'VALIDATION.json').is_file(), 'Merge/validate all dense workers first')
    manifest = read_json(root / 'manifest.json')
    plan = read_plan(root / 'plan.json')
    report = read_json(root / 'VALIDATION.json')
    require(manifest['state'] == 'complete' and manifest['identity']['plan_signature'] == plan['signature'] and
            report['validation'] == 'passed' and report['rows'] == plan['source']['chunks'], 'Incomplete/mismatched dense merge')
    require(report['mapping_sha256'] == file_hash(root / 'mapping.sqlite3') and report['manifest_sha256'] == file_hash(root / 'manifest.json'), 'Merged mapping/manifest changed')
    for part in plan['parts']:
        check_part(part_path(root, part['index']), part, plan)
    return plan


def sample_ids(db, size, seed):
    """Seeded language-hint strata; no query vectors or relevance labels used."""
    groups = db.execute('SELECT language,COUNT(*) FROM chunks GROUP BY language ORDER BY language').fetchall()
    size = min(size, sum(n for _, n in groups))
    allocation = [min(n, size // len(groups)) for _, n in groups] if groups else []
    remaining = size - sum(allocation)
    while remaining:
        for index, (_, count) in enumerate(groups):
            if allocation[index] < count and remaining:
                allocation[index] += 1
                remaining -= 1
    rng, output = random.Random(seed), []
    for (language, _), cap in zip(groups, allocation):
        chosen = []
        for count, (row_id,) in enumerate(db.execute('SELECT row_id FROM chunks WHERE language IS ? ORDER BY row_id', (language,)), 1):
            if len(chosen) < cap:
                chosen.append(row_id)
            elif cap:
                position = rng.randrange(count)
                if position < cap:
                    chosen[position] = row_id
        output.extend(chosen)
    return sorted(output)


def selected_vectors(root, plan, ids):
    import numpy as np
    selected = set(ids)
    output = np.empty((len(ids), plan['encoding']['model_spec']['dimension']), dtype='float32')
    positions = {row_id: i for i, row_id in enumerate(ids)}
    for part in plan['parts']:
        chosen = sorted(i for i in selected if part['start'] <= i < part['start'] + part['rows'])
        if not chosen:
            continue
        array = vectors_at(part_path(root, part['index']), part['rows'])
        output[[positions[i] for i in chosen]] = normalized(array[[i - part['start'] for i in chosen]])
    return output


def build(root, output, *, kind='ivfpq', nlist=4096, m=64, nbits=8, nprobe=32,
          train_size=200000, seed=None, checkpoint_batches=100):
    import numpy as np
    root, output = Path(root), Path(output)
    distinct(output, root)
    plan = ready(root)
    seed = plan['seed'] if seed is None else seed
    require(kind in {'ivfpq', 'exact'} and all(type(v) is int and v > 0 for v in (nlist, m, nbits, nprobe, train_size, checkpoint_batches)), 'Invalid index options')
    require(nbits <= 8, 'PQ nbits must be <=8')
    dimension, total = plan['encoding']['model_spec']['dimension'], plan['source']['chunks']
    identity = {'operation': 'dense-search-index', 'plan_signature': plan['signature'], 'kind': kind,
                'nlist': nlist, 'm': m, 'nbits': nbits, 'nprobe': nprobe, 'train_size': train_size, 'seed': seed,
                'code_sha256': file_hash(Path(__file__))}
    manifest = open_run(output, identity)
    with writer_lock(output):
        if manifest['state'] == 'complete':
            if kind == 'ivfpq':
                require(file_hash(output / manifest['checkpoint']) == manifest['index_sha256'], 'Saved FAISS index changed')
            return manifest
        if kind == 'exact':
            manifest.update(state='complete', rows=total, search='streamed float32 normalized cosine over float16 files')
            write_json(output / 'manifest.json', manifest)
            return manifest
        import faiss
        require(total >= 80 and dimension % m == 0, 'IVF-PQ needs >=80 vectors and dimension divisible by m; use --kind exact for tiny pilots')
        effective_nlist = min(nlist, max(1, min(total, train_size) // 40))
        effective_bits = min(nbits, max(1, int(math.log2(max(2, min(total, train_size) // 40)))))
        if manifest.get('checkpoint'):
            require(file_hash(output / manifest['checkpoint']) == manifest['index_sha256'], 'Index checkpoint changed')
            index = faiss.read_index(str(output / manifest['checkpoint']))
            done = manifest['batches_added']
        else:
            with sqlite3.connect((root / 'mapping.sqlite3').resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
                ids = sample_ids(db, train_size, seed)
            train = selected_vectors(root, plan, ids)
            index = faiss.IndexIVFPQ(faiss.IndexFlatIP(dimension), dimension, effective_nlist, m, effective_bits, faiss.METRIC_INNER_PRODUCT)
            index.cp.seed = seed
            index.pq.cp.seed = seed
            index.train(train)
            done = 0
            manifest.update(training_rows=len(train), effective_nlist=effective_nlist, effective_nbits=effective_bits,
                            sampling='seeded language_hint strata; domains unavailable in chunk packets, not domain-stratified', faiss=faiss.__version__)
            del train
        index.nprobe = min(nprobe, index.nlist)
        for number, part in enumerate(plan['parts']):
            if number < done:
                continue
            check_part(part_path(root, part['index']), part, plan)
            array = vectors_at(part_path(root, part['index']), part['rows'])
            for start in range(0, len(array), 8192):
                block = normalized(array[start:start + 8192])
                ids = np.arange(part['start'] + start, part['start'] + start + len(block), dtype='int64')
                index.add_with_ids(block, ids)
            if (number + 1) % checkpoint_batches == 0 or number + 1 == len(plan['parts']):
                name = f'checkpoint-{number + 1:06d}.faiss'
                temporary = output / (name + '.partial')
                faiss.write_index(index, str(temporary))
                temporary.replace(output / name)
                previous = manifest.get('checkpoint')
                manifest.update(checkpoint=name, index_sha256=file_hash(output / name), batches_added=number + 1, rows=int(index.ntotal))
                write_json(output / 'manifest.json', manifest)
                # Only this run's previous checkpoint; the manifest now points
                # to the new complete file. Interrupted writes leave .partial.
                if previous and previous != name:
                    (output / previous).unlink(missing_ok=True)
                print(json.dumps({'phase': 'dense_index', 'batches_added': number + 1, 'rows': int(index.ntotal)}), flush=True)
        require(index.ntotal == total, 'ANN index row total differs')
        manifest.update(state='complete', ann_quality='not measured; recall@200 audit deferred')
        write_json(output / 'manifest.json', manifest)
    return manifest


def exact(root, plan, query, top_k):
    import numpy as np
    heap = []
    for part in plan['parts']:
        array = vectors_at(part_path(root, part['index']), part['rows'])
        for start in range(0, len(array), 8192):
            scores = normalized(array[start:start + 8192]) @ query
            ids = np.arange(part['start'] + start, part['start'] + start + len(scores))
            for position in np.lexsort((ids, -scores))[:top_k]:
                item = (float(scores[position]), -int(ids[position]))
                if len(heap) < top_k:
                    heapq.heappush(heap, item)
                elif item > heap[0]:
                    heapq.heapreplace(heap, item)
    return [(int(-negative), float(score)) for score, negative in sorted(heap, reverse=True)]


def search(root, index_dir, query_dir, output, *, top_k=500):
    import numpy as np
    root, index_dir, query_dir, output = map(Path, (root, index_dir, query_dir, output))
    require(type(top_k) is int and top_k > 0, 'Positive top-k required')
    distinct(output, root, index_dir, query_dir)
    index_state = read_json(index_dir / 'manifest.json')
    query_state = read_json(query_dir / 'manifest.json')
    plan = ready(root)
    require(index_state['state'] == query_state['state'] == 'complete' and
            index_state['identity']['plan_signature'] == query_state['identity']['plan_signature'] == plan['signature'], 'Query/index encoder or corpus plan differs')
    require(file_hash(query_dir / 'vectors.npy') == query_state['vectors_sha256'] and file_hash(query_dir / 'queries.json') == query_state['queries_sha256'], 'Query artifact changed')
    values = normalized(np.load(query_dir / 'vectors.npy', allow_pickle=False))
    query_rows = read_json(query_dir / 'queries.json')
    require(values.shape == (len(query_rows), plan['encoding']['model_spec']['dimension']), 'Query shape differs')
    manifest = open_run(output, {'operation': 'dense-candidates', 'source': plan['source'], 'index_signature': index_state['signature'],
                                 'query_manifest_sha256': file_hash(query_dir / 'manifest.json'), 'top_k': top_k})
    index = None
    if index_state['identity']['kind'] == 'ivfpq':
        import faiss
        require(file_hash(index_dir / index_state['checkpoint']) == index_state['index_sha256'], 'FAISS file changed')
        index = faiss.read_index(str(index_dir / index_state['checkpoint']))
        index.nprobe = min(index_state['identity']['nprobe'], index.nlist)
    with writer_lock(output), sqlite3.connect((root / 'mapping.sqlite3').resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        for row, vector in zip(query_rows, values):
            target = output / f"query-{row['id']}.json"
            if target.exists():
                previous = checked(target)
                require(previous['run_signature'] == manifest['signature'], 'Saved query belongs to another dense run')
                continue
            if index is None:
                ranking = exact(root, plan, vector, top_k)
            else:
                scores, ids = index.search(np.ascontiguousarray(vector[None, :]), min(top_k, plan['source']['chunks']))
                ranking = sorted(((int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0), key=lambda item: (-item[1], item[0]))
            candidates = []
            for row_id, score in ranking:
                found = db.execute('SELECT payload FROM chunks WHERE row_id=?', (row_id,)).fetchone()
                require(found is not None, 'Vector ID has no chunk mapping')
                candidates.append({**json.loads(found[0]), 'score': score})
            write_json(target, signed({'id': row['id'], 'run_signature': manifest['signature'], 'candidates': candidates}))
        manifest.update(state='complete')
        write_json(output / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('build')
    run.add_argument('--kind', choices=['ivfpq', 'exact'], default='ivfpq')
    for name, default in (('nlist',4096), ('m',64), ('nbits',8), ('nprobe',32), ('train-size',200000), ('seed',None), ('checkpoint-batches',100)):
        run.add_argument('--' + name, type=int, default=default)
    find = commands.add_parser('search')
    find.add_argument('--index-dir', required=True, type=Path)
    find.add_argument('--query-dir', required=True, type=Path)
    find.add_argument('--top-k', type=int, default=500)
    for sub in (run, find):
        sub.add_argument('--vector-dir', required=True, type=Path)
        sub.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build(args.vector_dir, args.output_dir, kind=args.kind, nlist=args.nlist, m=args.m, nbits=args.nbits,
                       nprobe=args.nprobe, train_size=args.train_size, seed=args.seed, checkpoint_batches=args.checkpoint_batches) if args.command == 'build' else search(args.vector_dir, args.index_dir, args.query_dir, args.output_dir, top_k=args.top_k)
        print(json.dumps({k: v for k, v in result.items() if k != 'identity'}, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
