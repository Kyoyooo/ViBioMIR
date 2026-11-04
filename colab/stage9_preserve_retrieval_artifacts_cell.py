"""Paste into a Colab cell AFTER stopping the worker, WITHOUT restarting runtime.

Uses RUN, PLAN, LOCAL already defined by the retrieval notebook. Does not change
the old plan, mark workers complete, or copy all embedding files again.
"""
from datetime import datetime
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3


def checked_snapshot(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    unsigned = {k: v for k, v in value.items() if k != 'signature'}
    payload = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
    if value.get('signature') != hashlib.sha256(payload).hexdigest():
        raise ValueError(f'Checksum differs: {path}')
    return value


def backup_bm25(source, destination, plan):
    """Snapshot a committed SQLite index, including data still local to Colab."""
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError('Choose a new backup file distinct from source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.sqlite3.tmp')
    if temporary.exists():
        raise ValueError(f'Existing pending backup: {temporary}')
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)) as src:
        identity = src.execute("SELECT value FROM metadata WHERE key='plan'").fetchone()
        if identity != (plan['signature'],):
            raise ValueError('BM25 database belongs to another plan')
        batches = [r[0] for r in src.execute('SELECT id FROM batches ORDER BY id')]
        if batches != plan['batch_indices'][:len(batches)]:
            raise ValueError('BM25 batch coverage differs')
        if shutil.disk_usage(destination.parent).free < source.stat().st_size + 128*1024**2:
            raise ValueError('Insufficient destination disk for SQLite snapshot')
        import time
        last_print = [0.0]
        def progress(status, remaining, total):
            now = time.monotonic()
            if now-last_print[0] >= 30 or remaining == 0:
                print(f'SQLite backup: {total-remaining}/{total} pages', flush=True)
                last_print[0] = now
        with closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst, pages=4096, progress=progress)
            copied = [r[0] for r in dst.execute('SELECT id FROM batches ORDER BY id')]
            if copied != batches or dst.execute("SELECT value FROM metadata WHERE key='plan'").fetchone() != identity:
                raise ValueError('Backup metadata/coverage differs')
    temporary.replace(destination)
    return dict(path=str(destination), indexed_batches=len(batches),
                expected_batches=len(plan['batch_indices']), bytes=destination.stat().st_size,
                validation_scope='SQLite online backup plus plan and batch coverage; not retrieval completeness')


def preserve_artifacts(run, plan, local):
    run, local = Path(run), Path(local)
    existing_plan = checked_snapshot(run/'PLAN.json')
    if existing_plan != plan:
        raise ValueError('RUN and PLAN differ')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    snapshot = run/'recovery_snapshots'/stamp
    snapshot.mkdir(parents=True, exist_ok=False)
    report = dict(run=str(run), plan_signature=plan['signature'], dense=[], bm25=None,
                  full_retrieval_claimed=False)
    for role in ('qwen', 'bge', 'e5'):
        for path in sorted((run/'retrieval'/role).glob('part-*/checkpoint.json')):
            value = checked_snapshot(path)
            if value['identity']['plan'] != plan['signature']:
                raise ValueError(f'Checkpoint from another plan: {path}')
            destination = snapshot/role/path.parent.name/'checkpoint.json'
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False), encoding='utf-8')
            vector_count = sum(1 for _ in path.parent.glob('embeddings-*.npy'))
            record = dict(role=role, shard=path.parent.name, completed_batches=len(value['completed_batches']),
                saved_queries=len(value['hits']), vector_files_already_on_drive=vector_count,
                checkpoint_snapshot=str(destination))
            report['dense'].append(record)
            print(record, flush=True)
    source = local/plan['signature']/'bm25.sqlite3'
    if source.is_file():
        report['bm25'] = backup_bm25(source, snapshot/'bm25.sqlite3', plan)
        print('BM25 preserved:', report['bm25'], flush=True)
    else:
        print('No local BM25 database in THIS runtime. Run this cell in the original BM25 session too.', flush=True)
    (snapshot/'PRESERVATION_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved:', snapshot, flush=True)
    return report


if __name__ == '__main__':
    required = ('RUN', 'PLAN', 'LOCAL')
    if not all(key in globals() for key in required):
        raise RuntimeError('Paste into the existing retrieval notebook after stopping its worker; RUN/PLAN/LOCAL are required')
    PRESERVATION_REPORT = preserve_artifacts(RUN, PLAN, LOCAL)
