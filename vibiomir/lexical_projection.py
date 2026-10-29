"""Read-only, query-bound compact postings derived from the sealed S07 index."""
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import numpy as np

from .chunk_batches import read_json
from .io import canonical_json, file_hash, text_hash, write_json, writer_lock
from .lexical_stage7 import Analyzer, config, logical_config
from .retrieval_contracts import chunk_snapshot, distinct, open_run, queries, require


NATIVE = Path(__file__).resolve().parents[1] / 'native/lexical_projection.cpp'
ARRAYS = {'cids.u32': '<u4', 'tf.u32': '<u4', 'lengths.f64': '<f8', 'norms.f64': '<f8', 'chunk_ids.s64': 'S64'}


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.execute('PRAGMA cache_size=-65536')
    return db


def index_snapshot(config_path, *, verify_database=True):
    c = config(config_path)
    root = Path(c['output_dir'])
    manifest, report = read_json(root / 'manifest.json'), read_json(root / 'VALIDATION.json')
    require(manifest['signature'] == text_hash(canonical_json(manifest['identity'])) and
            manifest['state'] == 'complete' and report['validation'] == 'passed' and
            report['manifest_sha256'] == file_hash(root / 'manifest.json'), 'Completed sealed lexical index required')
    require(manifest['identity']['config'] == logical_config(c) and
            manifest['identity']['source'] == chunk_snapshot(c['chunk_dir']), 'Lexical config/source changed')
    for name, digest in manifest['identity']['code'].items():
        require(file_hash(Path(__file__).with_name(name)) == digest, 'Original lexical source code changed')
    analyzer = Analyzer(c['analyzer'])
    require(analyzer.registry == manifest['identity']['analyzer'], 'Original analyzer/dependencies changed')
    if verify_database:
        require(file_hash(root / 'index.sqlite3') == report['database_sha256'], 'Lexical database seal changed')
    return c, manifest, report, analyzer


def mapped(root, name, count):
    if not count: return np.empty(0, dtype=ARRAYS[name])
    return np.memmap(Path(root) / name, mode='r', dtype=ARRAYS[name], shape=(count,))


def _python_build(database, root, terms, n):
    total = sum(t['df'] for t in terms)
    arrays = {}
    for name, dtype in ARRAYS.items():
        count = total if name in {'cids.u32', 'tf.u32'} else n
        path = root / name
        if count: arrays[name] = np.memmap(path, mode='w+', dtype=dtype, shape=(count,))
        else: path.write_bytes(b''); arrays[name] = np.empty(0, dtype=dtype)
    slots = {t['tid']: t for t in terms}
    positions = {t['tid']: t['start'] for t in terms}
    with closing(readonly(database)) as db:
        count = 0
        for cid, length, norm, key in db.execute('SELECT cid,length,norm,chunk_id FROM chunks ORDER BY cid'):
            require(cid == count and cid < n and norm is not None and norm >= 0, 'Invalid chunk geometry/norm')
            arrays['lengths.f64'][cid], arrays['norms.f64'][cid] = length, norm
            require(0 < len(key.encode('utf-8')) <= 64, 'Stable chunk ID needs 1..64 UTF-8 bytes')
            arrays['chunk_ids.s64'][cid] = key.encode('utf-8')
            count += 1
        require(count == n, 'Chunk count differs')
        for cid, tid, tf in db.execute('SELECT cid,tid,tf FROM postings ORDER BY cid,tid'):
            require(0 <= cid < n and 0 < tf <= np.iinfo('uint32').max, 'Invalid posting')
            if tid not in slots: continue
            position = positions[tid]
            require(position < slots[tid]['start'] + slots[tid]['df'], 'Selected DF overflow')
            arrays['cids.u32'][position], arrays['tf.u32'][position] = cid, tf
            positions[tid] += 1
    require(all(positions[t['tid']] == t['start'] + t['df'] for t in terms), 'Selected DF differs')
    for array in arrays.values():
        if hasattr(array, 'flush'): array.flush()


def build_projection(config_path, query_file, output_dir, *, builder='native'):
    require(sys.byteorder == 'little' and builder in {'native', 'python'}, 'Require little-endian host and native/python builder')
    started = time.monotonic()
    c, index, report, analyzer = index_snapshot(config_path)
    database, root = Path(c['output_dir']) / 'index.sqlite3', Path(output_dir)
    distinct(root, c['output_dir'], c['chunk_dir'], query_file)
    query_rows = queries(query_file)
    tokens = [Counter(analyzer(q['query'])) for q in query_rows]
    vocabulary = sorted({term for counter in tokens for term in counter})
    terms, lookup, start = [], {}, 0
    with closing(readonly(database)) as db:
        for word in vocabulary:
            found = db.execute('SELECT tid,df FROM terms WHERE term=?', (word,)).fetchone()
            if found:
                tid, df = found
                require(0 < df <= index['chunks'], 'Invalid global DF')
                terms.append({'term': word, 'tid': tid, 'df': df})
    terms.sort(key=lambda t: t['tid'])
    for term in terms:
        term['start'] = start; start += term['df']; lookup[term['term']] = term['tid']
    analyzed = [{**q, 'known_terms': [{'tid': lookup[t], 'frequency': count} for t, count in counts.items() if t in lookup]}
                for q, counts in zip(query_rows, tokens)]
    identity = {'operation': 'lexical-query-projection-v1', 'index_signature': index['signature'],
                'index_manifest_sha256': file_hash(database.parent / 'manifest.json'),
                'database_sha256': report['database_sha256'], 'source': index['identity']['source'],
                'query_sha256': file_hash(query_file), 'terms_sha256': text_hash(canonical_json(terms)),
                'analyzed_queries_sha256': text_hash(canonical_json(analyzed)), 'chunks': index['chunks'],
                'code': {'lexical_projection.py': file_hash(__file__), 'native': file_hash(NATIVE)}}
    state = open_run(root, identity)
    with writer_lock(root):
        if state['state'] == 'complete':
            validate_projection(root, state)
            return state
        staging = root / '.building'
        if staging.exists(): shutil.rmtree(staging)
        staging.mkdir()
        wanted = staging / 'wanted.tsv'
        wanted.write_text(''.join(f"{t['tid']}\t{t['df']}\n" for t in terms))
        if builder == 'native':
            require(shutil.which('g++') is not None, 'Install a C++17 compiler or choose --builder python (slower, same scores)')
            binary = staging / 'projection_helper'
            subprocess.run(['g++', '-O3', '-std=c++17', str(NATIVE), '-o', str(binary), '-l:libsqlite3.so.0'], check=True)
            subprocess.run([str(binary.resolve()), database.resolve().as_uri() + '?mode=ro&immutable=1',
                            str(wanted.resolve()), str(staging.resolve()), str(index['chunks'])], check=True)
        else:
            _python_build(database, staging, terms, index['chunks'])
        for name in ARRAYS:
            count = start if name in {'cids.u32', 'tf.u32'} else index['chunks']
            require((staging / name).stat().st_size == count * np.dtype(ARRAYS[name]).itemsize, 'Projection size differs')
            (staging / name).replace(root / name)
        write_json(root / 'terms.json', terms)
        write_json(root / 'queries.json', analyzed)
        state.update(state='complete', queries=len(query_rows), terms=len(terms), postings=start,
                     builder=builder, seconds=time.monotonic() - started,
                     files={name: file_hash(root / name) for name in (*ARRAYS, 'terms.json', 'queries.json')})
        write_json(root / 'manifest.json', state)
        shutil.rmtree(staging)
        validate_projection(root, state)
    return state


def validate_projection(root, state=None):
    root = Path(root)
    state = read_json(root / 'manifest.json') if state is None else state
    require(state['state'] == 'complete' and state['signature'] == text_hash(canonical_json(state['identity'])), 'Projection incomplete/identity differs')
    require(state['identity']['code'] == {'lexical_projection.py': file_hash(__file__), 'native': file_hash(NATIVE)}, 'Projection source changed')
    for name, digest in state['files'].items(): require(file_hash(root / name) == digest, f'Projection payload changed: {name}')
    terms, analyzed = read_json(root / 'terms.json'), read_json(root / 'queries.json')
    require(text_hash(canonical_json(terms)) == state['identity']['terms_sha256'] and
            text_hash(canonical_json(analyzed)) == state['identity']['analyzed_queries_sha256'], 'Projection metadata differs')
    n, total = state['identity']['chunks'], state['postings']
    ids, tf = mapped(root, 'cids.u32', total), mapped(root, 'tf.u32', total)
    lengths, norms = mapped(root, 'lengths.f64', n), mapped(root, 'norms.f64', n)
    require(np.isfinite(lengths).all() and (lengths >= 0).all() and np.isfinite(norms).all() and (norms >= 0).all(), 'Invalid length/norm arrays')
    end = 0
    for term in terms:
        begin = term['start']
        require(begin == end, 'Projection term offsets have gaps/overlaps')
        end = begin + term['df']
        chosen = ids[begin:end]
        require(len(chosen) == term['df'] and (chosen < n).all() and
                (chosen[1:] > chosen[:-1]).all() and (tf[begin:end] > 0).all() and (norms[chosen] > 0).all(),
                'Invalid/unsorted projected postings')
    require(end == total and len(terms) == state['terms'] and len(analyzed) == state['queries'], 'Projection accounting differs')
    report = {'validation': 'passed', 'chunks': n, 'queries': len(analyzed), 'terms': len(terms), 'postings': total,
              'manifest_sha256': file_hash(root / 'manifest.json'), 'source': state['identity']['source'],
              'exact_global_df_preserved': True, 'quality_measured': False}
    write_json(root / 'VALIDATION.json', report)
    return report
