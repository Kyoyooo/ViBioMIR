"""Disk-backed multilingual BM25 and cosine TF-IDF with global statistics."""
import argparse
from collections import Counter
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
import html
import importlib.metadata
import json
import math
import multiprocessing
from pathlib import Path
import re
import sqlite3
import unicodedata

from .chunk_batches import read_json
from .io import canonical_json, text_hash, file_hash, write_json, writer_lock
from .retrieval_contracts import chunk_snapshot, source_parts, chunk_rows, mapping, require, distinct, open_run, queries, signed, checked


class Analyzer:
    def __init__(self, kind):
        require(kind in {'pyvi_jieba_v1', 'unicode_reference_v1'}, 'Unknown analyzer')
        self.kind, self.registry = kind, {'kind': kind}
        if kind == 'pyvi_jieba_v1':
            try:
                import pyvi
                import jieba
                from pyvi import ViTokenizer
            except ImportError as exc:
                raise ValueError('Install requirements-stage7.txt; analyzer fallback is never automatic') from exc
            self.vi, self.zh = ViTokenizer.tokenize, jieba.Tokenizer()
            self.zh.initialize()
            for name, package in (('pyvi', pyvi), ('jieba', jieba)):
                root = Path(package.__file__).parent
                files = {p.relative_to(root).as_posix(): file_hash(p) for p in root.rglob('*')
                         if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.log'}}
                self.registry[name] = {'version': importlib.metadata.version(name), 'files': files}
            self.registry['dependencies'] = {}
            for name in ('numpy', 'scikit-learn', 'sklearn-crfsuite', 'python-crfsuite', 'joblib'):
                try:
                    self.registry['dependencies'][name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    self.registry['dependencies'][name] = None

    def __call__(self, text):
        text = ' '.join(html.unescape(unicodedata.normalize('NFKC', text)).casefold().split())
        output = []
        for block in re.findall(r'[\u3400-\u9fff]+|[^\u3400-\u9fff]+', text):
            if '\u3400' <= block[0] <= '\u9fff':
                words = self.zh.cut_for_search(block, HMM=False) if self.kind == 'pyvi_jieba_v1' else list(block) + [block[i:i+2] for i in range(len(block)-1)]
                output.extend('zh:' + word for word in words if word.strip())
            else:
                block = self.vi(block) if self.kind == 'pyvi_jieba_v1' else block
                output.extend('w:' + word for word in re.findall(r'\w+', block, re.UNICODE))
        return output


_ANALYZER = None


def initialize_analyzer(kind):
    global _ANALYZER
    _ANALYZER = Analyzer(kind)


def analyze_in_worker(text):
    return Counter(_ANALYZER(text))


def token_rows(rows, analyzer, pool, prefetch):
    if pool is None:
        for row in rows:
            yield row, Counter(analyzer(row['retrieval_text']))
        return
    rows, pending = iter(rows), deque()
    exhausted = False
    while pending or not exhausted:
        while not exhausted and len(pending) < prefetch:
            row = next(rows, None)
            if row is None:
                exhausted = True
            else:
                pending.append((row, pool.submit(analyze_in_worker, row['retrieval_text'])))
        if pending:
            row, future = pending.popleft()
            yield row, future.result()


def logical_config(c):
    return {k: v for k, v in c.items() if k not in {'workers', 'sqlite_cache_mib', 'top_k'}}


def config(path):
    value = read_json(path)
    require(value.get('schema') == 'vibiomir-lexical-v1', 'Invalid lexical config')
    require(type(value['k1']) in (int, float) and math.isfinite(value['k1']) and value['k1'] > 0 and
            type(value['b']) in (int, float) and math.isfinite(value['b']) and 0 <= value['b'] <= 1, 'Invalid k1/b')
    require(type(value['sqlite_cache_mib']) is int and 16 <= value['sqlite_cache_mib'] <= 4096, 'sqlite_cache_mib must be 16..4096')
    require(type(value['top_k']) is int and value['top_k'] > 0, 'Positive top_k required')
    require(type(value.get('workers', 1)) is int and 1 <= value.get('workers', 1) <= 8, 'workers must be 1..8')
    return value


def connect(path, cache=256):
    db = sqlite3.connect(path)
    db.execute(f'PRAGMA cache_size={-cache * 1024}')
    db.execute('PRAGMA temp_store=FILE')
    db.execute('PRAGMA journal_mode=DELETE')
    db.create_function('ln', 1, math.log, deterministic=True)
    db.create_function('sqrt', 1, math.sqrt, deterministic=True)
    return db


def build(config_path, *, stop_after_batches=None):
    c = config(config_path)
    require(stop_after_batches is None or stop_after_batches > 0, 'Positive stop-after-batches required')
    source, root = Path(c['chunk_dir']), Path(c['output_dir'])
    distinct(root, source)
    snapshot, analyzer = chunk_snapshot(source), Analyzer(c['analyzer'])
    parts = source_parts(source, snapshot)
    identity = {'operation': 'lexical-global-index', 'source': snapshot, 'config': logical_config(c), 'analyzer': analyzer.registry,
                'code': {name: file_hash(Path(__file__).with_name(name)) for name in ('lexical_stage7.py', 'retrieval_contracts.py')}}
    manifest = open_run(root, identity)
    with writer_lock(root), connect(root / 'index.sqlite3', c['sqlite_cache_mib']) as db, ExitStack() as stack:
        db.executescript('''CREATE TABLE IF NOT EXISTS chunks(cid INTEGER PRIMARY KEY,chunk_id TEXT UNIQUE,doc_id INTEGER,version TEXT,batch INTEGER,payload TEXT,length INTEGER,norm REAL);
          CREATE TABLE IF NOT EXISTS terms(tid INTEGER PRIMARY KEY,term TEXT UNIQUE,df INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS chunks_batch ON chunks(batch);
          CREATE TABLE IF NOT EXISTS postings(cid INTEGER,tid INTEGER,tf INTEGER,PRIMARY KEY(cid,tid)) WITHOUT ROWID;
          CREATE INDEX IF NOT EXISTS postings_term ON postings(tid,cid);
          CREATE TABLE IF NOT EXISTS jobs(batch INTEGER PRIMARY KEY,sha TEXT,n INTEGER,norm_done INTEGER DEFAULT 0);''')
        added, pool = 0, None
        for part in parts:
            previous = db.execute('SELECT sha,n FROM jobs WHERE batch=?', (part['index'],)).fetchone()
            if previous:
                require(previous == (part['source_sha256'], part['rows']), 'Committed source batch differs')
                continue
            if pool is None and c.get('workers', 1) > 1:
                pool = stack.enter_context(ProcessPoolExecutor(max_workers=c['workers'], mp_context=multiprocessing.get_context('spawn'),
                                                              initializer=initialize_analyzer, initargs=(c['analyzer'],)))
            with db:
                term_ids, increments = {}, Counter()
                for row, counts in token_rows(chunk_rows(source, part), analyzer, pool, c.get('workers', 1) * 4):
                    db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)',
                               (row['row_id'], row['chunk_id'], row['doc_id'], row['derived_version_id'], part['index'], json.dumps(mapping(row), ensure_ascii=False), sum(counts.values())))
                    missing = [term for term in counts if term not in term_ids]
                    db.executemany('INSERT OR IGNORE INTO terms(term,df) VALUES (?,0)', ((term,) for term in missing))
                    for term in missing:
                        term_ids[term] = db.execute('SELECT tid FROM terms WHERE term=?', (term,)).fetchone()[0]
                    db.executemany('INSERT INTO postings VALUES (?,?,?)', ((row['row_id'], term_ids[term], frequency) for term, frequency in counts.items()))
                    increments.update(term_ids[term] for term in counts)
                db.executemany('UPDATE terms SET df=df+? WHERE tid=?', ((count, tid) for tid, count in increments.items()))
                db.execute('INSERT INTO jobs(batch,sha,n) VALUES (?,?,?)', (part['index'], part['source_sha256'], part['rows']))
            added += 1
            print(json.dumps({'phase': 'lexical_postings', 'batch': part['index'], 'chunks': part['rows']}), flush=True)
            if stop_after_batches and added >= stop_after_batches:
                manifest.update(state='paused', phase='postings')
                write_json(root / 'manifest.json', manifest)
                return manifest
        n, total = db.execute('SELECT COUNT(*),COALESCE(SUM(length),0) FROM chunks').fetchone()
        require(n == snapshot['chunks'], 'Indexed chunk count differs')
        manifest.update(state='building', phase='tfidf_norms', chunks=n, average_length=total / n if n else 0)
        write_json(root / 'manifest.json', manifest)
        # Freeze global DF before computing any norm; transactions checkpoint
        # one source batch, so interruption never marks partial norms complete.
        for part in parts:
            if db.execute('SELECT norm_done FROM jobs WHERE batch=?', (part['index'],)).fetchone()[0]:
                continue
            with db:
                db.execute('''UPDATE chunks SET norm=COALESCE((SELECT sqrt(SUM(
                  ((1+ln(p.tf))*(ln((?+1.0)/(t.df+1.0))+1))*((1+ln(p.tf))*(ln((?+1.0)/(t.df+1.0))+1))))
                  FROM postings p JOIN terms t ON t.tid=p.tid WHERE p.cid=chunks.cid),0) WHERE batch=?''', (n, n, part['index']))
                db.execute('UPDATE jobs SET norm_done=1 WHERE batch=?', (part['index'],))
            print(json.dumps({'phase': 'lexical_norms', 'batch': part['index']}), flush=True)
        manifest.update(state='complete', phase='complete', terms=db.execute('SELECT COUNT(*) FROM terms').fetchone()[0])
        write_json(root / 'manifest.json', manifest)
    return manifest


def validate(config_path):
    c = config(config_path)
    root = Path(c['output_dir'])
    require((root / 'manifest.json').is_file(), 'Run the lexical build first')
    manifest = read_json(root / 'manifest.json')
    require(manifest['state'] == 'complete' and manifest['identity']['config'] == logical_config(c) and
            manifest['identity']['source'] == chunk_snapshot(c['chunk_dir']), 'Complete matching lexical index required')
    require(manifest['signature'] == text_hash(canonical_json(manifest['identity'])) and
            manifest['identity']['code'] == {name: file_hash(Path(__file__).with_name(name)) for name in ('lexical_stage7.py', 'retrieval_contracts.py')}, 'Lexical identity/code differs')
    with writer_lock(root), connect(root / 'index.sqlite3', c['sqlite_cache_mib']) as db:
        require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'SQLite integrity check failed')
        require(db.execute('SELECT COUNT(*) FROM chunks WHERE norm IS NULL OR length<0 OR norm<0').fetchone()[0] == 0, 'Missing/invalid norms')
        require(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == manifest['chunks'], 'Index total differs')
        require(not db.execute('SELECT 1 FROM terms t WHERE t.df != (SELECT COUNT(*) FROM postings p WHERE p.tid=t.tid) LIMIT 1').fetchone(), 'Global DF differs')
        for part in source_parts(c['chunk_dir'], manifest['identity']['source']):
            for row in chunk_rows(c['chunk_dir'], part):
                found = db.execute('SELECT payload FROM chunks WHERE cid=?', (row['row_id'],)).fetchone()
                require(found and json.loads(found[0]) == mapping(row), 'Chunk mapping differs')
        report = {'validation': 'passed', 'chunks': manifest['chunks'], 'source': manifest['identity']['source'],
                  'database_sha256': file_hash(root / 'index.sqlite3'), 'manifest_sha256': file_hash(root / 'manifest.json'), 'quality_measured': False}
        write_json(root / 'VALIDATION.json', report)
    return report


def search(db, analyzer, query, manifest, *, method='bm25', top_k=500):
    require(method in {'bm25', 'tfidf'} and type(top_k) is int and top_k > 0, 'Invalid method/top-k')
    n, avg = manifest['chunks'], manifest['average_length']
    known = []
    for term, frequency in Counter(analyzer(query)).items():
        item = db.execute('SELECT tid,df FROM terms WHERE term=?', (term,)).fetchone()
        if item:
            tid, df = item
            idf = math.log1p((n - df + 0.5) / (df + 0.5)) if method == 'bm25' else math.log((n + 1) / (df + 1)) + 1
            known.append((tid, idf if method == 'bm25' else (1 + math.log(frequency)) * idf, idf))
    if not known:
        return []
    db.execute('CREATE TEMP TABLE IF NOT EXISTS query_terms(tid INTEGER PRIMARY KEY,weight REAL,idf REAL)')
    db.execute('DELETE FROM query_terms')
    db.executemany('INSERT INTO query_terms VALUES (?,?,?)', known)
    if method == 'bm25':
        c = manifest['identity']['config']
        expression, args = 'q.weight*p.tf*(?+1)/(p.tf+?*(1-?+?*c.length/?))', (c['k1'], c['k1'], c['b'], c['b'], avg or 1)
    else:
        norm = math.sqrt(sum(weight ** 2 for _, weight, _ in known))
        expression, args = 'q.weight*(1+ln(p.tf))*q.idf/(c.norm*?)', (norm,)
    rows = db.execute(f'''SELECT c.payload,SUM({expression}) AS score FROM query_terms q
      CROSS JOIN postings p INDEXED BY postings_term CROSS JOIN chunks c
      WHERE p.tid=q.tid AND c.cid=p.cid
      GROUP BY c.cid ORDER BY score DESC,c.chunk_id LIMIT ?''', (*args, top_k))
    return [{**json.loads(payload), 'score': score} for payload, score in rows]


def search_queries(config_path, query_file, output_dir, *, method='bm25', top_k=None):
    c = config(config_path)
    top_k = c['top_k'] if top_k is None else top_k
    require(type(top_k) is int and top_k > 0, 'Positive top-k required')
    query_rows = queries(query_file)
    root, output = Path(c['output_dir']), Path(output_dir)
    distinct(output, root, c['chunk_dir'])
    manifest = read_json(root / 'manifest.json')
    report = read_json(root / 'VALIDATION.json')
    require(manifest['state'] == 'complete' and report['validation'] == 'passed', 'Build/validate lexical index first')
    require(report['manifest_sha256'] == file_hash(root / 'manifest.json') and report['database_sha256'] == file_hash(root / 'index.sqlite3'), 'Lexical index changed since validation')
    analyzer = Analyzer(c['analyzer'])
    require(analyzer.registry == manifest['identity']['analyzer'] and logical_config(c) == manifest['identity']['config'], 'Analyzer/config changed')
    identity = {'source': manifest['identity']['source'], 'index_signature': manifest['signature'], 'method': method,
                'query_sha256': file_hash(query_file), 'top_k': top_k}
    state = open_run(output, identity)
    with writer_lock(output), connect(root / 'index.sqlite3', c['sqlite_cache_mib']) as db:
        for query in query_rows:
            target = output / f"query-{query['id']}.json"
            if target.exists():
                record = checked(target)
                require(record['run_signature'] == state['signature'] and record['id'] == query['id'], 'Saved query belongs to another run')
                continue
            write_json(target, signed({'id': query['id'], 'run_signature': state['signature'],
                       'candidates': search(db, analyzer, query['query'], manifest, method=method, top_k=top_k)}))
        state.update(state='complete')
        write_json(output / 'manifest.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='configs/lexical_stage7.json')
    commands = parser.add_subparsers(dest='command', required=True)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--stop-after-batches', type=int)
    commands.add_parser('validate')
    run = commands.add_parser('search')
    run.add_argument('--queries', required=True, type=Path)
    run.add_argument('--output-dir', required=True, type=Path)
    run.add_argument('--method', choices=['bm25', 'tfidf'], default='bm25')
    run.add_argument('--top-k', type=int)
    args = parser.parse_args()
    try:
        result = build(args.config, stop_after_batches=args.stop_after_batches) if args.command == 'build' else validate(args.config) if args.command == 'validate' else search_queries(args.config, args.queries, args.output_dir, method=args.method, top_k=args.top_k)
        print(json.dumps({k: v for k, v in result.items() if k != 'identity'}, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
