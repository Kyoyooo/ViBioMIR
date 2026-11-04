"""Build standalone Colab notebooks with identical embedded support modules."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('qwen_submission_trial', 'multimodel_evidence', 'stage9_dense_recovery', 'corpus_multiretrieval')


def cell(kind, source):
    value = dict(cell_type=kind, metadata={}, source=source.splitlines(keepends=True))
    if kind == 'code':
        ast.parse(source)
        value.update(execution_count=None, outputs=[])
    return value


def code(source):
    return cell('code', source)


def md(source):
    return cell('markdown', source)


SETUP = '''from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, sys, subprocess, zipfile, shutil, time, threading
from contextlib import contextmanager
ROOT = Path('/content/drive/MyDrive/ViBioMIR')
STAGE8_BUNDLES = ROOT / 'dense_stage8_bge_structure/bundles'
DATA_ZIP = ROOT / 'Data.zip'
QUERY_FILE = ROOT / 'query.parquet'
LINKS_FILE = ROOT / 'Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet'
CAMPAIGN = 'corpus_four_branch_v1'
RUN_PARENT = ROOT / 'corpus_retrieval_trials' / CAMPAIGN
LOCAL = Path('/content/corpus_multiretrieval')
LOCAL.mkdir(parents=True, exist_ok=True)
RUN_PARENT.mkdir(parents=True, exist_ok=True)
os_environ = __import__('os').environ
os_environ.update(USE_TF='0', USE_FLAX='0', TOKENIZERS_PARALLELISM='false')
DEBUG_CUDA = False  # Set True in a FRESH session if a CUDA assertion persists.
if DEBUG_CUDA:
    assert 'torch' not in sys.modules, 'Restart session before enabling CUDA diagnostics'
    os_environ['CUDA_LAUNCH_BLOCKING'] = '1'
'''

FILES = {name+'.py': (ROOT/'scripts'/f'{name}.py').read_text(encoding='utf-8') for name in MODULES}
HELPERS = 'SUPPORT_FILES = '+repr(FILES)+'''
SUPPORT = LOCAL / 'code'
SUPPORT.mkdir(exist_ok=True)
for filename, source in SUPPORT_FILES.items():
    module_name = filename[:-3]
    path = SUPPORT / filename
    if module_name in sys.modules:
        assert Path(sys.modules[module_name].__file__).read_text(encoding='utf-8') == source, 'Restart runtime: support code changed'
    path.write_text(source, encoding='utf-8')
sys.path.insert(0, str(SUPPORT))
import corpus_multiretrieval as pipeline
from corpus_multiretrieval import core
import pyarrow.parquet as pq

def official_file(path, basename):
    if path.is_file():
        return path
    candidates = [ROOT/'Data/official/ViBioMIR'/basename, ROOT/'query'/basename]
    found = next((p for p in candidates if p.is_file()), None)
    if found:
        return found
    assert DATA_ZIP.is_file(), f'Missing {basename}; set its path or provide Data.zip'
    with zipfile.ZipFile(DATA_ZIP) as archive:
        matches = [n for n in archive.namelist() if Path(n).name == basename and 'official' in Path(n).parts]
        assert len(matches) == 1, f'Ambiguous/missing official file: {matches}'
        target = LOCAL / basename
        with archive.open(matches[0]) as src, target.open('wb') as dst:
            shutil.copyfileobj(src, dst)
    return target

QUERY_FILE = official_file(QUERY_FILE, 'query.parquet')
assert core.file_hash(QUERY_FILE) == core.EXPECTED_QUERY_SHA256, 'Wrong official query snapshot'
QUERIES = pq.read_table(QUERY_FILE, columns=['id', 'query']).to_pylist()

@contextmanager
def progress(label):
    finished = threading.Event()
    started = time.monotonic()
    def heartbeat():
        while not finished.wait(30):
            print(f'{label}: {(time.monotonic()-started)/60:.1f} minutes', flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    print(label, flush=True)
    thread.start()
    try:
        yield
    finally:
        finished.set()
        thread.join()
'''

INSTALL_GPU = '''subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
    'pyarrow==22.0.0', 'transformers==4.57.1', 'tokenizers==0.22.1',
    'safetensors>=0.4,<1', 'sentencepiece>=0.2,<0.3'], check=True)
'''
INSTALL_CPU = "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyarrow==22.0.0'], check=True)\n"
CONFIG = '''# SAME configuration in every retrieval session.
# Pilot: 20 queries, first 4 source batches. NOT a competition submission.
# Full run: set BOTH limits to None in ALL sessions; it creates a different RUN.
QUERY_LIMIT = 20
SOURCE_BATCH_LIMIT = 4
TOP_K_PER_BRANCH = 500
RERANK_TOP_K = 400
QWEN_ATTENTION = 'sdpa'  # faster; 'eager' for a separate diagnostic plan if needed
PLAN = pipeline.make_plan(QUERY_FILE, QUERIES, QUERY_LIMIT, SOURCE_BATCH_LIMIT,
                          TOP_K_PER_BRANCH, RERANK_TOP_K, QWEN_ATTENTION)
RUN = RUN_PARENT / PLAN['signature']
RUN.mkdir(parents=True, exist_ok=True)
plan_path = RUN / 'PLAN.json'
if plan_path.exists():
    assert core.checked(core.read(plan_path)) == PLAN, 'Different existing plan'
else:
    core.write(plan_path, PLAN)
print('RUN =', RUN)
print('Full scope:', PLAN['full_scope'], 'Queries:', len(PLAN['queries']), 'Batches:', len(PLAN['batch_indices']))
'''
LOAD_PLAN = '''# Copy the printed RUN path from the retrieval notebook.
# Automatic choice only when this campaign has exactly one plan.
PLAN_DIRECTORY = None  # e.g. Path('/content/drive/MyDrive/ViBioMIR/corpus_retrieval_trials/.../<signature>')
if PLAN_DIRECTORY is None:
    matches = sorted(RUN_PARENT.glob('*/PLAN.json'))
    assert len(matches) == 1, 'Set PLAN_DIRECTORY explicitly; multiple/no runs in this campaign'
    RUN = matches[0].parent
else:
    RUN = Path(PLAN_DIRECTORY)
PLAN = core.checked(core.read(RUN / 'PLAN.json'))
pipeline.check_plan(PLAN)
assert core.file_hash(QUERY_FILE) == PLAN['query_sha256']
print('RUN:', RUN, 'Full scope:', PLAN['full_scope'])
'''


def save(name, cells):
    notebook = dict(cells=cells, metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
        language_info=dict(name='python'), colab=dict(provenance=[])), nbformat=4, nbformat_minor=5)
    for index, item in enumerate(cells):
        item['id'] = f'corpus-{index}'
    (ROOT/'colab'/name).write_text(json.dumps(notebook, ensure_ascii=False, indent=1)+'\n', encoding='utf-8')


save('stage9_corpus_retrieval_colab.ipynb', [
    md('''# 1. Retrieval từ corpus: BM25 + BGE + Qwen + E5

Không dùng submission_17.zip. Nguồn: sáu worker ZIP của structural corpus Stage 8.
Mở notebook ở các phiên riêng: ROLE=bm25 (CPU), bge/qwen/e5 (GPU).
Giữ cùng CAMPAIGN và toàn bộ cấu hình plan. Dense có thể chia SHARD_COUNT/SHARD_INDEX;
BM25 dùng một chỉ mục toàn cục, SHARD_COUNT=1.

Mặc định pilot 20 query / 4 batch, chỉ kiểm tra luồng kỹ thuật. Khi chạy toàn bộ, đổi hai giới hạn
thành None ở tất cả phiên. Mỗi model thực sự encode lại corpus; không tái dùng chỉ mục BGE cũ.
Embedding float32 lưu đủ corpus có thể cần khoảng 22,6 GB/model nếu chiều 1024, ba nhánh khoảng 67,7 GB.
Đặt SAVE_EMBEDDINGS=False để chỉ giữ candidate/checkpoint; vẫn thực hiện embedding thật.
'''), code(SETUP), code(INSTALL_GPU), code(HELPERS), code(CONFIG),
    code('''ROLE = 'qwen'  # bm25 | bge | qwen | e5
SHARD_COUNT = 1
SHARD_INDEX = 0
SAVE_EMBEDDINGS = True
ENCODE_BATCH_SIZE = 32  # maximum; actual batches also obey ENCODE_PADDED_TOKENS
ENCODE_PADDED_TOKENS = 8192  # conservative start; increase after measuring GPU memory
STOP_AFTER_BATCHES = 2  # pilot timing; then None and rerun ONLY worker cell below
assert ROLE in pipeline.BRANCHES
assert ROLE != 'bm25' or (SHARD_COUNT, SHARD_INDEX) == (1, 0)
with progress('Inspect source ZIP metadata'):
    SOURCE_PLAN, MEMBERS = pipeline.setup_source(STAGE8_BUNDLES, PLAN)
print('Assigned batches:', pipeline.assigned(PLAN, SHARD_INDEX, SHARD_COUNT))
'''), code('''with progress(f'Retrieve {ROLE}'):
    if ROLE == 'bm25':
        RESULT = pipeline.retrieve_bm25(PLAN, RUN, SOURCE_PLAN, MEMBERS,
            LOCAL / PLAN['signature'] / 'bm25.sqlite3', STOP_AFTER_BATCHES)
    else:
        RESULT = pipeline.retrieve_dense(PLAN, RUN, ROLE, SOURCE_PLAN, MEMBERS,
            SHARD_INDEX, SHARD_COUNT, STOP_AFTER_BATCHES, SAVE_EMBEDDINGS,
            encoder_factory=lambda policy: pipeline.Encoder(policy, ENCODE_BATCH_SIZE, ENCODE_PADDED_TOKENS))
print('State:', RESULT['state'], 'Completed source batches:', len(RESULT['completed_batches']))
'''), md('''Sau khi cả 4 nhánh và mọi shard đều complete, mở notebook 2.
Paused là checkpoint thời gian, chưa phải hoàn tất. Đổi STOP_AFTER_BATCHES=None rồi chạy lại cell worker.
Không chạy hai phiên cùng ROLE/SHARD_INDEX. Thay SHARD_COUNT phải dùng CAMPAIGN mới.
''')])

save('stage9_corpus_rerank_colab.ipynb', [
    md('''# 2. Hợp nhất candidate, đối chiếu nguồn, Qwen + BGE rerank

Đầu tiên mở một phiên với MODE='prepare' để hợp nhất đủ bốn nhánh và đọc lại evidence gốc.
Khi prepare xong, mở hai phiên GPU với MODE='rerank', ROLE='qwen' và ROLE='bge'.
Hai model chấm cùng pool top 400 sau retrieval RRF. Có thể chia query theo shard.
Không chạy lại prepare trong khi rerank đang hoạt động. Giữ nguyên PLAN và code.
'''), code(SETUP), code(INSTALL_GPU), code(HELPERS), code(LOAD_PLAN),
    code('''MODE = 'prepare'  # prepare first; then rerank in separate GPU sessions
ROLE = 'qwen'  # qwen | bge
SHARD_COUNT = 1
SHARD_INDEX = 0
STOP_AFTER_QUERIES = 5  # then None and rerun ONLY worker cell
'''), code('''if MODE == 'prepare':
    with progress('Verify retrieval coverage and hydrate original evidence'):
        SOURCE_PLAN, MEMBERS = pipeline.setup_source(STAGE8_BUNDLES, PLAN)
        RESULT = pipeline.prepare_pool(PLAN, RUN, SOURCE_PLAN, MEMBERS,
            LOCAL / PLAN['signature'] / 'evidence.sqlite3')
elif MODE == 'rerank':
    with progress(f'Rerank {ROLE}'):
        RESULT = pipeline.rerank(PLAN, RUN, ROLE, SHARD_INDEX, SHARD_COUNT, STOP_AFTER_QUERIES)
else:
    raise ValueError('Use MODE prepare or rerank')
print(RESULT)
'''), md('''Chấm đầu vào retrieval_text (giữ title/context từ corpus), xuất chunk_text gốc.
Không dùng điểm reranker cũ: model input và pool đã thay đổi.
Mặc định không tách đoạn con. Token dài ở embedding được chia window; reranker từ chối
cặp vượt 4096 token thay vì cắt ngầm. Nếu gặp lỗi này, cần thiết kế thí nghiệm window rerank riêng.
''')])

save('stage9_corpus_export_colab.ipynb', [
    md('''# 3. Gộp Qwen/BGE rerank và xuất kết quả

CPU; cần mọi query của cả hai reranker. Xếp hạng gộp RRF, chọn tối đa 120 document,
120 chunk, 6 chunk/document; loại exact duplicate cùng document và IoU nguồn >=0.8,
giữ một derived version/document. Document được xếp lại từ candidate mới.
Pilot chỉ xuất PILOT_PREDICTIONS.json, không xuất submission.zip.
'''), code(SETUP), code(INSTALL_CPU), code(HELPERS), code(LOAD_PLAN),
    code('''ALLOWED_IDS = None
if PLAN['full_scope']:
    LINKS_FILE = official_file(LINKS_FILE, 'links_corpus_vi_en_zh.parquet')
    assert core.file_hash(LINKS_FILE) == pipeline.LINKS_SHA, 'Official links snapshot differs'
    LINKS_ID_COLUMN = 'id'
    pf = pq.ParquetFile(LINKS_FILE)
    assert LINKS_ID_COLUMN in pf.schema_arrow.names, pf.schema_arrow.names
    ALLOWED_IDS = set()
    for batch in pf.iter_batches(columns=[LINKS_ID_COLUMN]):
        values = batch.column(0).to_pylist()
        assert all(type(v) is int for v in values), 'Official IDs must be integers'
        ALLOWED_IDS.update(values)
    assert ALLOWED_IDS
DESTINATION = RUN / 'exports' / time.strftime('%Y%m%d_%H%M%S')
with progress('Validate scores and export source evidence'):
    REPORT = pipeline.merge_export(PLAN, RUN, DESTINATION, ALLOWED_IDS)
print(json.dumps(REPORT, indent=2))
print('Output:', DESTINATION)
'''), code('''# Manual download only; no website submission is automated.
if REPORT['state'] == 'full_submission_exported':
    from google.colab import files
    files.download(str(DESTINATION / 'submission.zip'))
else:
    print('PILOT ONLY. Run all 1200 queries and all 1712 source batches before submitting.')
''')])
