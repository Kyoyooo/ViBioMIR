"""Build a standalone notebook with embedded recovery code."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
old = json.loads((ROOT / 'colab/stage9_expand_pool_colab.ipynb').read_text(encoding='utf-8'))
sources = {c.get('id'): ''.join(c['source']) for c in old['cells']}
cells = []


def cell(kind, name, source):
    value = dict(cell_type=kind, id=name, metadata={}, source=source.splitlines(keepends=True))
    if kind == 'code':
        ast.parse(source)
        value.update(execution_count=None, outputs=[])
    cells.append(value)


cell('markdown', 'intro', '''# Dense V1 + S09 rerank trên Colab Pro

Notebook độc lập: dùng 6 ZIP đầu vào Stage 8 để khôi phục structural chunks,
kiểm tra candidates V1 với văn bản nguồn, rerank top 400 rồi xuất 120 docs / 120 chunks.
Không cần BM25, không chạy lại chunking/embedding, không dùng semantic store.
Đây là thí nghiệm dense riêng, không tái hiện baseline hybrid 0.1626.

Chạy từng cell trong runtime mới. Chuẩn bị dùng CPU và ổ đĩa, GPU chỉ dùng ở bước rerank.
Kiểm tra 5 query trước; sau đó đặt STOP_AFTER_QUERIES=None và chạy lại cell worker.
Không tự nộp lên website. Giữ bản nộp tốt nhất và so sánh bằng cùng evaluator mới của BTC.
Luật LCS hai chiều không được đo ở đây: chưa có evaluator/đáp án chính thức.
Notebook giữ nguyên evidence gốc, chưa cắt hoặc viết lại chunk.

Mã nguồn được nhúng sẵn, chỉ cần upload notebook. Các file dữ liệu vẫn phải có trên Drive.
Không chạy hai phiên cùng RUN_DRIVE. Sau khi mất runtime, chạy lại từ đầu:
source được khôi phục lại, candidates được kiểm tra lại, điểm query hoàn tất được lấy từ backup.
''')
cell('code', 'setup', '''from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, hashlib, zipfile, tempfile, shutil, sys, subprocess, os

ROOT = Path('/content/drive/MyDrive/ViBioMIR')
BUNDLE = ROOT / 'stage9_rerank_colab.zip'
STAGE8_BUNDLES = ROOT / 'dense_stage8_bge_structure/bundles'
DENSE_VERSION = 'query_results_v1'
DENSE_RESULTS = ROOT / 'dense_stage8_bge_structure' / DENSE_VERSION / 'results'
DATA_ZIP = ROOT / 'Data.zip'
QUERY_FILE = ROOT / 'Data/official/ViBioMIR/query.parquet'
LINKS_FILE = ROOT / 'Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet'
LINKS_ID_COLUMN = 'id'
EXPECTED_LINKS_SHA256 = '87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7'
EXPECTED_QUERIES = 1200
LIMITS = dict(max_docs=120, max_chunks=120, max_chunks_per_doc=6, overlap_iou_threshold=0.8)
RERANK_TOP_K = 400
STOP_AFTER_QUERIES = 5
RUN_NAME = f'stage9_dense_{DENSE_VERSION}_k{RERANK_TOP_K}_120_120_v1'
RUN_DRIVE = ROOT / RUN_NAME
LOCAL = Path('/content') / RUN_NAME
CHUNKS = LOCAL / 'chunks'
CANDIDATES = LOCAL / 'validated_candidates'
NEW_PREPARED = LOCAL / 'prepared'
NEW_WORKERS = LOCAL / 'workers'
assert DENSE_VERSION in ('query_results_v1', 'query_results_v2')
assert 0 < RERANK_TOP_K <= 500
assert BUNDLE.is_file(), f'Missing S09 code bundle: {BUNDLE}'
assert STAGE8_BUNDLES.is_dir(), f'Missing Stage-8 input bundles: {STAGE8_BUNDLES}'
assert (DENSE_RESULTS / 'manifest.json').is_file(), f'Missing dense results: {DENSE_RESULTS}'
LOCAL.mkdir(parents=True, exist_ok=True)
RUN_DRIVE.mkdir(parents=True, exist_ok=True)
print('Source bundles:', STAGE8_BUNDLES)
print('Dense results:', DENSE_RESULTS)
print('New output:', RUN_DRIVE)
''')
cell('code', 'load-code', '''# Keep PROJECT creation and extraction in this single cell.
assert not any(n == 'vibiomir' or n.startswith('vibiomir.') for n in sys.modules), 'Restart runtime before rerunning this cell.'
PROJECT = Path(tempfile.mkdtemp(prefix='vibiomir_dense_s09_', dir='/content'))
def digest(data):
    return hashlib.sha256(data).hexdigest()
def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
with zipfile.ZipFile(BUNDLE) as z:
    meta = json.loads(z.read('BUNDLE.json'))
    assert digest(canonical({k:v for k,v in meta.items() if k != 'signature'}).encode()) == meta['signature']
    assert len(z.namelist()) == len(set(z.namelist()))
    assert set(z.namelist()) == set(meta['files']) | {'BUNDLE.json'}
    assert 'vibiomir/stage9_pipeline.py' in meta['files'], 'Wrong bundle: S09 code is missing'
    assert shutil.disk_usage('/content').free > sum(i.file_size for i in z.infolist()) + 1024**3
    for name, expected_hash in meta['files'].items():
        path = (PROJECT / name).resolve()
        assert PROJECT.resolve() in path.parents
        data = z.read(name)
        assert digest(data) == expected_hash, f'Corrupt bundle member: {name}'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
assert (PROJECT / 'vibiomir/stage9_pipeline.py').is_file()
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)  # Frozen bundle() reads requirements-stage9-colab.txt relative to cwd.
import importlib
importlib.invalidate_caches()
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyarrow==22.0.0'], check=True)
from vibiomir.stage9_pipeline import prepared, prepare, bundle, export, validate_results, validate_worker
from vibiomir.retrieval_contracts import queries, chunk_snapshot
from vibiomir.io import file_hash, write_json
state = prepared(PROJECT)
assert state['identity']['scope'] == 'all_queries' and len(state['packets']) == EXPECTED_QUERIES
expected_source = state['identity']['source']
print('Loaded frozen S09 code:', PROJECT)
print('Expected structural source:', expected_source)
''')
cell('code', 'official', sources['expand-3'])
recovery = (ROOT / 'scripts/stage9_dense_recovery.py').read_text(encoding='utf-8')
support = (ROOT / 'scripts/stage9_expand_support.py').read_text(encoding='utf-8')
cell('code', 'helpers', f'''RECOVERY_SOURCE = {recovery!r}
PACKAGE_SOURCE = {support!r}
(PROJECT / 'dense_recovery_support.py').write_text(RECOVERY_SOURCE, encoding='utf-8')
(PROJECT / 'dense_package_support.py').write_text(PACKAGE_SOURCE, encoding='utf-8')
from dense_recovery_support import inspect_bundles, restore_chunks, adapt_candidates
from dense_package_support import package
import time, threading
from contextlib import contextmanager
@contextmanager
def progress(label):
    started = time.monotonic()
    event = threading.Event()
    def heartbeat():
        while not event.wait(30):
            print(label, 'running minutes:', round((time.monotonic()-started)/60, 1), flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    print(label, flush=True)
    thread.start()
    try:
        yield
    finally:
        event.set()
        thread.join()
''')
cell('code', 'preflight', '''# Read metadata before restoring large payloads.
with progress('Inspect six Stage-8 bundles'):
    plan, members, metadata = inspect_bundles(STAGE8_BUNDLES, expected_source)
dense_manifest = json.loads((DENSE_RESULTS / 'manifest.json').read_text())
assert dense_manifest['state'] == 'complete' and dense_manifest.get('corpus_complete') is True
assert dense_manifest['identity']['plan_signature'] == plan['signature']
assert dense_manifest['identity']['query_sha256'] == file_hash(query_path)
assert dense_manifest['signature'] == digest(canonical(dense_manifest['identity']).encode())
missing = [q['id'] for q in official_queries if not (DENSE_RESULTS / 'candidates' / f"query-{q['id']}.json").is_file()]
assert not missing, f'Missing candidate queries: {missing[:20]}'
required_gib = sum(v[2] for v in members.values()) / 1024**3
print('Source compressed payload size after ZIP extraction, GiB:', round(required_gib, 2))
print('Free local GiB:', round(shutil.disk_usage('/content').free / 1024**3, 2))
print('Metadata checks passed; payload validation follows.')
''')
cell('code', 'restore', '''with progress('Restore structural source; completed files are checksum-verified and reused'):
    plan = restore_chunks(STAGE8_BUNDLES, CHUNKS, expected_source)
assert chunk_snapshot(CHUNKS) == expected_source
print('Structural store restored:', CHUNKS)
''')
cell('code', 'adapt', '''# This can take time: validate ALL candidates, not only a sample.
with progress('Validate dense candidate records and source text on CPU'):
    report = adapt_candidates(DENSE_RESULTS, CHUNKS, plan, expected_source,
                              query_path, official_queries, CANDIDATES)
write_json(RUN_DRIVE / 'CANDIDATE_VALIDATION.json', report)
print(report)
''')
cell('code', 'prepare', '''fixed_query = LOCAL / 'query.parquet'
shutil.copy2(query_path, fixed_query)
config = json.loads(json.dumps(state['identity']['settings']))
config.update(queries=str(fixed_query), chunk_dir=str(CHUNKS), output_dir=str(NEW_PREPARED),
              rerank_top_k=RERANK_TOP_K, fused_top_k=500, fusion='rrf', workers=1,
              selection=LIMITS,
              branches=[dict(name='dense_verified', family='dense', facet_id='original',
                             candidate_dir=str(CANDIDATES))])
write_json(LOCAL / 'config.json', config)
with progress('Prepare S09 packets from verified dense candidates'):
    checkpoint = RUN_DRIVE / 'prepared.zip'
    if checkpoint.is_file():
        # Restore the exact gzip bytes referenced by completed worker scores.
        with zipfile.ZipFile(checkpoint) as z:
            meta = json.loads(z.read('BUNDLE.json'))
            assert digest(canonical({k:v for k,v in meta.items() if k != 'signature'}).encode()) == meta['signature']
            assert len(z.namelist()) == len(set(z.namelist()))
            assert set(z.namelist()) == set(meta['files']) | {'BUNDLE.json'}
            for name, expected_hash in meta['files'].items():
                target = (NEW_PREPARED / name).resolve()
                assert NEW_PREPARED.resolve() in target.parents
                payload = z.read(name)
                assert digest(payload) == expected_hash
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
    expanded = prepare(LOCAL / 'config.json')
assert len(expanded['packets']) == EXPECTED_QUERIES
assert expanded['identity']['source'] == expected_source
with progress('Back up prepared bundle'):
    local_zip = LOCAL / 'prepared.zip'
    bundle(NEW_PREPARED, local_zip)
    pending = RUN_DRIVE / 'prepared.zip.tmp'
    shutil.copy2(local_zip, pending)
    assert file_hash(pending) == file_hash(local_zip)
    pending.replace(RUN_DRIVE / 'prepared.zip')
print('Prepared pairs:', expanded['candidates'])
print('No baseline hybrid scores have been imported; new dense run.')
''')
cell('code', 'gpu', sources['expand-6'])
worker_code = '''# Separate checkpoints by exact packet bytes, not only logical run identity.
packet_state = prepared(NEW_PREPARED)
packet_tag = digest(canonical([(p['id'], p['sha256']) for p in packet_state['packets']]).encode())
NEW_WORKERS = LOCAL / 'workers_by_packets' / packet_tag
WORKER_BACKUP = RUN_DRIVE / 'workers_by_packets' / packet_tag / 'worker_0'
print('Packet checkpoint:', packet_tag, flush=True)
print('Worker backup:', WORKER_BACKUP, flush=True)
''' + sources['expand-7'].replace("str(RUN_DRIVE / 'workers/worker_0')", 'str(WORKER_BACKUP)')
cell('code', 'worker', worker_code)
cell('code', 'export', '''worker = NEW_WORKERS / 'worker_0'
assert json.loads((worker / 'manifest.json').read_text())['state'] == 'complete', 'Finish all queries before export'
with progress('Validate worker and original evidence; export submission'):
    validate_worker(NEW_PREPARED, worker, 0)
    RESULTS = LOCAL / 'results'
    export(NEW_PREPARED, RESULTS, mode='reranked', workers_dir=NEW_WORKERS)
    validation = validate_results(NEW_PREPARED, RESULTS, chunk_dir=CHUNKS,
                                  queries_path=fixed_query, workers_dir=NEW_WORKERS)
    from datetime import datetime, timezone
    destination = RUN_DRIVE / ('submission_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    archive = package(RESULTS, [q['id'] for q in official_queries], allowed, destination)
    shutil.copy2(RESULTS / 'VALIDATION.json', destination / 'VALIDATION.json')
    write_json(destination / 'EXPERIMENT.json', dict(kind='dense-only-cross-encoder',
        dense_results=str(DENSE_RESULTS), source=expected_source, limits=LIMITS,
        rerank_top_k=RERANK_TOP_K, relevance_measured=False,
        official_lcs_evaluator_run=False, chunk_text_policy='original source chunks unchanged'))
print('Submission ZIP:', archive)
print('Technical validation passed. Relevance/LCS score has not been measured.')
from google.colab import files
files.download(str(archive))
''')
nb = dict(cells=cells, metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
          colab=dict(name='stage9_dense_recovery_colab.ipynb')), nbformat=4, nbformat_minor=5)
destination = ROOT / 'colab/stage9_dense_recovery_colab.ipynb'
destination.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
print(destination)
