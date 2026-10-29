"""Exact S07 BM25/TF-IDF over compact postings, bounded caches and CPU threads.

The sealed original lexical index/code and all scoring definitions are retained.
"""
import argparse
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

import numpy as np

from .chunk_batches import read_json
from .io import file_hash, text_hash, canonical_json, write_json, writer_lock
from .lexical_projection import build_projection, index_snapshot, mapped, readonly, validate_projection
from .retrieval_contracts import checked, distinct, open_run, queries, require, signed


class ExactLexical:
    def __init__(self, projection_dir, index_manifest, database, *, weight_cache_mib=1024):
        require(type(weight_cache_mib) is int and 0 <= weight_cache_mib <= 4096, 'weight-cache-mib must be 0..4096')
        root = Path(projection_dir)
        self.state, self.index = read_json(root / 'manifest.json'), index_manifest
        require(self.state['identity']['index_signature'] == index_manifest['signature'], 'Projection belongs to another index')
        self.terms = {t['tid']: t for t in read_json(root / 'terms.json')}
        n, total = self.state['identity']['chunks'], self.state['postings']
        self.ids, self.tf = mapped(root, 'cids.u32', total), mapped(root, 'tf.u32', total)
        self.norms, self.keys = mapped(root, 'norms.f64', n), mapped(root, 'chunk_ids.s64', n)
        lengths = mapped(root, 'lengths.f64', n)
        config = index_manifest['identity']['config']
        self.length_factor = config['k1'] * (1 - config['b'] + config['b'] * lengths / (index_manifest['average_length'] or 1))
        self.budget, self.bytes = weight_cache_mib * 1024**2, 0
        self.weights, self.lock = OrderedDict(), threading.Lock()
        self.database, self.local = database, threading.local()
        self.connections, self.connection_lock = [], threading.Lock()
        self.payloads, self.payload_lock = OrderedDict(), threading.Lock()
        self.cache_hits, self.cache_misses = 0, 0

    def close(self):
        # Connections are created with check_same_thread=False but used only by
        # their owning worker; close occurs after the executor has finished.
        for db in self.connections: db.close()
        self.connections.clear()

    def db(self):
        if not hasattr(self.local, 'db'):
            self.local.db = sqlite3.connect(Path(self.database).resolve().as_uri() + '?mode=ro&immutable=1',
                                           uri=True, check_same_thread=False)
            self.local.db.execute('PRAGMA cache_size=-16384')
            with self.connection_lock: self.connections.append(self.local.db)
        return self.local.db

    def term_weights(self, tid):
        # Shared bounded LRU prevents repeated log/division work on common words.
        # Serial creation also avoids duplicate large arrays at worker startup.
        with self.lock:
            if tid in self.weights:
                self.cache_hits += 1
                self.weights.move_to_end(tid)
                return self.weights[tid]
            self.cache_misses += 1
            term = self.terms[tid]
            a, b = term['start'], term['start'] + term['df']
            ids = self.ids[a:b]
            frequency = self.tf[a:b].astype('float64')
            n, df = self.index['chunks'], term['df']
            bm_idf = math.log1p((n - df + .5) / (df + .5))
            tf_idf = math.log((n + 1) / (df + 1)) + 1
            bm = bm_idf * frequency * (self.index['identity']['config']['k1'] + 1) / (frequency + self.length_factor[ids])
            cosine = (1 + np.log(frequency)) * tf_idf / self.norms[ids]
            value = (ids, bm, cosine, tf_idf)
            size = bm.nbytes + cosine.nbytes
            while self.weights and self.bytes + size > self.budget:
                _, old = self.weights.popitem(last=False)
                self.bytes -= old[1].nbytes + old[2].nbytes
            if size <= self.budget:
                self.weights[tid] = value; self.bytes += size
            return value

    def top(self, scores, top_k):
        matches = np.flatnonzero(scores > 0)
        if len(matches) > top_k:
            values = scores[matches]
            cutoff = np.partition(values, len(values) - top_k)[len(values) - top_k]
            above, tied = matches[values > cutoff], matches[values == cutoff]
            needed = top_k - len(above)
            if len(tied) > needed:
                tied = tied[np.argpartition(self.keys[tied], needed - 1)[:needed]]
            matches = np.concatenate((above, tied[:needed]))
        order = np.lexsort((self.keys[matches], -scores[matches]))
        return [(int(i), float(scores[i])) for i in matches[order]]

    def payload(self, ids):
        ids = sorted(set(ids))
        result, missing = {}, []
        with self.payload_lock:
            for cid in ids:
                if cid in self.payloads:
                    result[cid] = self.payloads[cid]; self.payloads.move_to_end(cid)
                else: missing.append(cid)
        fetched = []
        for start in range(0, len(missing), 900):
            batch = missing[start:start + 900]
            fetched.extend(self.db().execute('SELECT cid,payload FROM chunks WHERE cid IN (' + ','.join('?' for _ in batch) + ')', batch))
        with self.payload_lock:
            for cid, raw in fetched:
                result[cid] = raw; self.payloads[cid] = raw
            while len(self.payloads) > 65536: self.payloads.popitem(last=False)
        require(len(result) == len(ids), 'Lexical mapping has missing chunk IDs')
        return {cid: json.loads(raw) for cid, raw in result.items()}

    def search(self, query, *, top_k=500, methods=('bm25', 'tfidf')):
        require(type(top_k) is int and top_k > 0 and methods and set(methods) <= {'bm25', 'tfidf'}, 'Invalid search options')
        known = query['known_terms']
        if not known: return {method: [] for method in methods}
        n, query_weights = self.index['chunks'], {}
        for item in known:
            df = self.terms[item['tid']]['df']
            query_weights[item['tid']] = (1 + math.log(item['frequency'])) * (math.log((n + 1) / (df + 1)) + 1)
        query_norm = math.sqrt(sum(w * w for w in query_weights.values()))
        totals = {method: np.zeros(n, dtype='float64') for method in methods}
        for item in sorted(known, key=lambda x: x['tid']):
            tid = item['tid']
            ids, bm, cosine, _ = self.term_weights(tid)
            if 'bm25' in totals: totals['bm25'][ids] += bm
            if 'tfidf' in totals: totals['tfidf'][ids] += cosine * (query_weights[tid] / query_norm)
        best = {method: self.top(scores, top_k) for method, scores in totals.items()}
        payloads = self.payload(cid for rows in best.values() for cid, _ in rows)
        return {method: [{**payloads[cid], 'score': score} for cid, score in rows] for method, rows in best.items()}


def ready(config_path, query_file, projection_dir):
    c, index, report, _ = index_snapshot(config_path)
    projection = read_json(Path(projection_dir) / 'manifest.json')
    require(projection['identity']['index_signature'] == index['signature'] and
            projection['identity']['database_sha256'] == report['database_sha256'] and
            projection['identity']['query_sha256'] == file_hash(query_file), 'Projection/index/query snapshot differs')
    validate_projection(projection_dir, projection)
    analyzed = read_json(Path(projection_dir) / 'queries.json')
    require([(q['id'], q['query']) for q in analyzed] == [(q['id'], q['query']) for q in queries(query_file)], 'Projected query set differs')
    return c, index, projection, analyzed


def bounded_results(pool, engine, pending, top_k, methods, prefetch):
    iterator, queued = iter(pending), deque()
    for _ in range(prefetch):
        q = next(iterator, None)
        if q is None: break
        queued.append((q, pool.submit(engine.search, q, top_k=top_k, methods=methods)))
    while queued:
        q, future = queued.popleft()
        yield q, future.result()
        q = next(iterator, None)
        if q is not None: queued.append((q, pool.submit(engine.search, q, top_k=top_k, methods=methods)))


def run(config_path, query_file, projection_dir, outputs, *, workers=2, top_k=500, weight_cache_mib=1024, stop_after_queries=None):
    require(type(workers) is int and 1 <= workers <= 8 and type(top_k) is int and top_k > 0, 'Choose 1..8 workers and positive top-k')
    require(stop_after_queries is None or type(stop_after_queries) is int and stop_after_queries > 0, 'Positive stop-after-queries required')
    require(outputs and set(outputs) <= {'bm25', 'tfidf'}, 'Invalid output methods')
    started = time.monotonic()
    c, index, projection, analyzed = ready(config_path, query_file, projection_dir)
    states, completed, pending = {}, set(), []
    for method, output in outputs.items():
        distinct(output, c['output_dir'], c['chunk_dir'], projection_dir, query_file)
        for other, path in outputs.items():
            if other != method: distinct(output, path)
        identity = {'operation': 'lexical-exact-candidates', 'source': index['identity']['source'],
                    'index_signature': index['signature'], 'projection_signature': projection['signature'],
                    'method': method, 'query_sha256': file_hash(query_file), 'top_k': top_k,
                    'scoring': 'unchanged global BM25 and cosine TF-IDF; float64; no term dropping',
                    'code_sha256': file_hash(__file__)}
        states[method] = open_run(output, identity)
    engine = ExactLexical(projection_dir, index, Path(c['output_dir']) / 'index.sqlite3', weight_cache_mib=weight_cache_mib)
    with ExitStack() as stack:
        stack.callback(engine.close)
        for output in outputs.values(): stack.enter_context(writer_lock(output))
        for q in analyzed:
            found = True
            for method, output in outputs.items():
                path = Path(output) / f"query-{q['id']}.json"
                if path.exists():
                    value = checked(path)
                    require(value['run_signature'] == states[method]['signature'] and value['id'] == q['id'], 'Saved lexical query belongs to another run')
                else: found = False
            if found: completed.add(q['id'])
            else: pending.append(q)
        if stop_after_queries: pending = pending[:stop_after_queries]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # Bound pending work/results; keep input order and shared immutable
            # mmap/weight caches. NumPy releases the GIL during array work.
            for q, result in bounded_results(pool, engine, pending, top_k, tuple(outputs), workers * 2):
                for method, rows in result.items():
                    write_json(Path(outputs[method]) / f"query-{q['id']}.json", signed({
                        'id': q['id'], 'run_signature': states[method]['signature'], 'candidates': rows}))
                completed.add(q['id'])
                if len(completed) <= 4 or len(completed) % 25 == 0 or len(completed) == len(analyzed):
                    print(json.dumps({'phase': 'lexical_query', 'id': q['id'], 'complete': len(completed),
                                      'total': len(analyzed), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
        for method, state in states.items():
            state.update(state='complete' if len(completed) == len(analyzed) else 'paused', queries=len(completed),
                         expected_queries=len(analyzed), execution={'workers': workers, 'weight_cache_mib': weight_cache_mib,
                         'seconds_this_invocation': time.monotonic() - started,
                         'weight_cache_hits': engine.cache_hits, 'weight_cache_misses': engine.cache_misses})
            write_json(Path(outputs[method]) / 'manifest.json', state)
    return {method: {'state': state['state'], 'queries': state['queries'], 'output_dir': str(outputs[method]),
                     'execution': state['execution']} for method, state in states.items()}


def validate(config_path, query_file, output_dir):
    c, index, source_report, _ = index_snapshot(config_path)
    root = Path(output_dir)
    state = read_json(root / 'manifest.json')
    require(state['state'] == 'complete' and state['signature'] == text_hash(canonical_json(state['identity'])) and
            state['identity']['index_signature'] == index['signature'] and
            state['identity']['source'] == index['identity']['source'] and
            state['identity']['query_sha256'] == file_hash(query_file) and
            state['identity']['code_sha256'] == file_hash(__file__), 'Complete compatible lexical query run required')
    expected = queries(query_file)
    require({p.name for p in root.glob('query-*.json')} == {f"query-{q['id']}.json" for q in expected}, 'Missing/extra lexical query files')
    total, counts, empty = 0, [], []
    with closing(readonly(Path(c['output_dir']) / 'index.sqlite3')) as db:
        for number, q in enumerate(expected, 1):
            value = checked(root / f"query-{q['id']}.json")
            require(value['id'] == q['id'] and value['run_signature'] == state['signature'], 'Lexical query identity differs')
            rows = value['candidates']
            require(len(rows) <= state['identity']['top_k'] and len({x['chunk_id'] for x in rows}) == len(rows), 'Invalid lexical top-k/duplicates')
            require(all(type(x['score']) in (int, float) and math.isfinite(x['score']) and x['score'] > 0 for x in rows), 'Invalid lexical scores')
            require(rows == sorted(rows, key=lambda x: (-x['score'], x['chunk_id'])), 'Unstable lexical rank order')
            found = {}
            for start in range(0, len(rows), 900):
                ids = [x['row_id'] for x in rows[start:start+900]]
                require(all(type(i) is int and 0 <= i < index['chunks'] for i in ids), 'Invalid lexical global row ID')
                if ids:
                    for cid, raw in db.execute('SELECT cid,payload FROM chunks WHERE cid IN (' + ','.join('?' for _ in ids) + ')', ids):
                        found[cid] = json.loads(raw)
            for row in rows:
                require({k:v for k,v in row.items() if k != 'score'} == found.get(row['row_id']), 'Lexical source mapping differs')
            counts.append(len(rows)); total += len(rows)
            if not rows: empty.append(q['id'])
            if number % 200 == 0: print(json.dumps({'validation': 'lexical_candidates', 'queries': number}), flush=True)
    report = {'validation': 'passed', 'queries': len(expected), 'candidates': total,
              'minimum_candidates': min(counts), 'maximum_candidates': max(counts), 'empty_query_ids': empty,
              'query_sha256': file_hash(query_file), 'manifest_sha256': file_hash(root / 'manifest.json'),
              'source': index['identity']['source'], 'all_source_mappings_verified': True,
              'database_sha256': source_report['database_sha256'], 'quality_measured': False}
    write_json(root / 'VALIDATION.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'run', 'validate'):
        p = commands.add_parser(name)
        p.add_argument('--config', default='configs/lexical_stage7.json')
        p.add_argument('--queries', default='Data/official/ViBioMIR/query.parquet')
        if name != 'validate': p.add_argument('--projection-dir', default='artifacts/lexical_stage7_query_cache')
        if name == 'prepare': p.add_argument('--builder', choices=('native', 'python'), default='native')
        elif name == 'run':
            p.add_argument('--workers', type=int, default=2); p.add_argument('--weight-cache-mib', type=int, default=1024)
            p.add_argument('--top-k', type=int, default=500); p.add_argument('--stop-after-queries', type=int)
            p.add_argument('--bm25-output', default='artifacts/candidates_stage7_bm25')
            p.add_argument('--tfidf-output', default='artifacts/candidates_stage7_tfidf')
        else: p.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            value = build_projection(args.config, args.queries, args.projection_dir, builder=args.builder)
            result = {k: value[k] for k in ('state', 'queries', 'terms', 'postings', 'seconds')}
        elif args.command == 'run':
            result = run(args.config, args.queries, args.projection_dir, {'bm25': args.bm25_output, 'tfidf': args.tfidf_output},
                         workers=args.workers, top_k=args.top_k, weight_cache_mib=args.weight_cache_mib, stop_after_queries=args.stop_after_queries)
        else: result = validate(args.config, args.queries, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        parser.exit(130, 'Interrupted. Completed query files are retained; repeat run to resume. An incomplete projection scan restarts.\n')
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, f'Lexical search: {exc}\n')


if __name__ == '__main__':
    main()
