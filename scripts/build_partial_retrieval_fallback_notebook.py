"""Build one standalone deadline notebook; old retrieval runs remain read-only."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
cells = []


def add(kind, source):
    item = dict(cell_type=kind, id=f'fallback-{len(cells)}', metadata={}, source=source.splitlines(keepends=True))
    if kind == 'code':
        ast.parse(source)
        item.update(execution_count=None, outputs=[])
    cells.append(item)


add('markdown', '''# Deadline fallback: baseline + candidate từ retrieval dang dở

Giữ đủ 1.200 query của bài nộp nền. Đóng băng checkpoint hiện có, lấy tối đa 20 candidate
mỗi query từ các nhánh sẵn có, kiểm tra evidence nguồn rồi chấm **chunk_text** của cả
baseline và candidate mới bằng một reranker (mặc định BGE để hạn chế thời gian).

Query chưa chấm xong hoặc lỗi sẽ giữ nguyên baseline khi export. Có thể export ngay cả
khi chưa prepare/chấm query nào. Không yêu cầu đủ E5/Qwen/BGE/BM25; không chạy embedding lại.
Artifact đang có chỉ đại diện corpus đã quét, không được gọi là retrieval đầy đủ hay GT.

Mở runtime GPU mới; KHÔNG reset phiên BM25 có database chưa backup. Notebook này chỉ đọc
RUN cũ và tạo thư mục kết quả riêng. Có thể đọc checkpoint trong khi retrieval tiếp tục;
snapshot đóng băng sẽ không tự cập nhật khi checkpoint cũ tiến thêm.

**Nếu sắp đến hạn, nộp baseline đã được xác nhận trước.** Bản kết hợp là thí nghiệm,
không có bảo đảm điểm cao hơn. Xem REPORT và QUERY_AUDIT trước khi chọn file nộp.
''')
add('code', '''from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, sys, subprocess, zipfile, shutil, time, threading, os
from contextlib import contextmanager
ROOT = Path('/content/drive/MyDrive/ViBioMIR')
BASELINE_ZIP = ROOT / 'submission_17.zip'  # chọn đúng bài nộp tốt nhất của bạn
QUERY_FILE = ROOT / 'query.parquet'
DATA_ZIP = ROOT / 'Data.zip'
LINKS_FILE = ROOT / 'Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet'
STAGE8_BUNDLES = ROOT / 'dense_stage8_bge_structure/bundles'
OLD_RUN = None  # BẮT BUỘC: Path('/content/drive/MyDrive/ViBioMIR/corpus_retrieval_trials/.../<signature>')
SNAPSHOT_LABEL = 'deadline_01'  # đổi tên để lấy snapshot mới, không sửa snapshot cũ
OUTPUT_PARENT = ROOT / 'partial_retrieval_fallback' / SNAPSHOT_LABEL
LOCAL = Path('/content/partial_retrieval_fallback')
LOCAL.mkdir(parents=True, exist_ok=True)
OUTPUT_PARENT.mkdir(parents=True, exist_ok=True)
os.environ.update(USE_TF='0', USE_FLAX='0', TOKENIZERS_PARALLELISM='false')
assert BASELINE_ZIP.is_file(), f'Missing baseline: {BASELINE_ZIP}'
''')
add('code', '''subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyarrow==22.0.0',
    'transformers==4.57.1', 'tokenizers==0.22.1', 'safetensors>=0.4,<1', 'sentencepiece>=0.2,<0.3'], check=True)
''')
names = ('qwen_submission_trial', 'multimodel_evidence', 'stage9_dense_recovery', 'corpus_multiretrieval', 'partial_retrieval_fallback')
files = {name+'.py': (ROOT/'scripts'/f'{name}.py').read_text(encoding='utf-8') for name in names}
add('code', 'SUPPORT_FILES = '+repr(files)+'''
SUPPORT = LOCAL/'code'
SUPPORT.mkdir(exist_ok=True)
for filename, source in SUPPORT_FILES.items():
    name = filename[:-3]
    if name in sys.modules:
        assert Path(sys.modules[name].__file__).read_text(encoding='utf-8') == source, 'Restart this fallback session: support code changed'
    (SUPPORT/filename).write_text(source, encoding='utf-8')
sys.path.insert(0, str(SUPPORT))
import partial_retrieval_fallback as fallback
from partial_retrieval_fallback import core, corpus

def official_file(path, basename):
    if path.is_file():
        return path
    for candidate in (ROOT/'Data/official/ViBioMIR'/basename, ROOT/'query'/basename):
        if candidate.is_file():
            return candidate
    assert DATA_ZIP.is_file(), f'Missing {basename}; provide its path or Data.zip'
    with zipfile.ZipFile(DATA_ZIP) as z:
        matches = [name for name in z.namelist() if Path(name).name == basename and 'official' in Path(name).parts]
        assert len(matches) == 1, f'Ambiguous official file: {matches}'
        target = LOCAL/basename
        with z.open(matches[0]) as src, target.open('wb') as dst:
            shutil.copyfileobj(src, dst)
    return target

@contextmanager
def progress(label):
    finished = threading.Event()
    started = time.monotonic()
    def heartbeat():
        while not finished.wait(30):
            print(f'{label}: {(time.monotonic()-started)/60:.1f} minutes', flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    print(label, flush=True)
    try:
        yield
    finally:
        finished.set()
        thread.join()

QUERY_FILE = official_file(QUERY_FILE, 'query.parquet')
ROWS, QUERIES, INPUTS, BASELINE_RAW = fallback.load_baseline(BASELINE_ZIP, QUERY_FILE)
print('Baseline:', BASELINE_ZIP)
print('SHA256:', INPUTS['baseline_zip_sha256'], 'Queries:', len(ROWS))
''')
add('code', '''# Chỉ đọc PLAN và checkpoint/result JSON; không cần vector .npy hoặc database BM25.
assert OLD_RUN is not None, 'Set OLD_RUN in the first cell to the exact old retrieval RUN'
ROLES = ('qwen', 'bge', 'e5', 'bm25')  # nhánh chưa có checkpoint/result được ghi là unavailable
SNAPSHOT_DIR = OUTPUT_PARENT / 'snapshot'
with progress('Freeze current retrieval checkpoints'):
    SNAPSHOT = fallback.snapshot_retrieval(OLD_RUN, SNAPSHOT_DIR, QUERIES, ROLES)
print('Source batches covered by role:', SNAPSHOT['coverage_batches'])
print('Unavailable roles:', SNAPSHOT['unavailable_roles'])
print('Frozen files:', len(SNAPSHOT['files']))
''')
add('code', '''MAX_NEW_CANDIDATES_PER_QUERY = 20
RERANK_MODEL = 'bge'  # bge | qwen. Một model chấm chung cả baseline và candidate mới.
PLAN = fallback.new_plan(INPUTS, QUERIES, SNAPSHOT, MAX_NEW_CANDIDATES_PER_QUERY, RERANK_MODEL)
RUN = OUTPUT_PARENT / PLAN['signature']
RUN.mkdir(parents=True, exist_ok=True)
path = RUN/'PLAN.json'
if path.exists():
    assert core.checked(core.read(path)) == PLAN, 'Existing fallback plan differs'
else:
    core.write(path, PLAN)
print('Fallback RUN:', RUN)
''')
add('markdown', '''## Chuẩn bị và chấm — có thể bỏ qua để export baseline ngay

Prepare đọc lại các source batch đã quét để xác minh candidate. Việc này vẫn có thể mất
thời gian I/O; không cần đợi bốn nhánh hoàn tất. Chạy prepare một lần; giữ nguyên packet
khi chấm. Baseline chỉ được xác nhận là văn bản trong ZIP, chưa được đối chiếu corpus.
''')
add('code', '''# Nếu đã prepare thành công, kiểm tra rồi dùng lại, không đọc nguồn lần nữa.
manifest_path = RUN/'prepared/manifest.json'
if manifest_path.exists():
    PREPARED = core.checked(core.read(manifest_path))
    assert PREPARED['plan'] == PLAN['signature']
else:
    with progress('Hydrate new candidates and build joint pools'):
        PREPARED = fallback.prepare(PLAN, RUN, ROWS, QUERIES, INPUTS, SNAPSHOT_DIR,
            STAGE8_BUNDLES, LOCAL/PLAN['signature']/'evidence.sqlite3')
print('Queries eligible for rerank:', PREPARED['eligible_queries'])
print('First eligible IDs:', list(PREPARED['files'])[:20])
''')
add('code', '''STOP_AFTER_QUERIES = 5  # pilot; sau đó None rồi chạy lại RIÊNG cell worker dưới
TIME_BUDGET_MINUTES = 60  # dừng ở ranh giới query, không ngắt query đang chấm
SHARD_COUNT = 1
SHARD_INDEX = 0
BATCH_SIZE = 8
PADDED_TOKENS = 8192
''')
add('code', '''fallback.validate_run(PLAN, ROWS, QUERIES, INPUTS)
with progress(f'Rerank baseline + partial candidates using {RERANK_MODEL}'):
    STATUS = fallback.score(PLAN, RUN, QUERIES, STOP_AFTER_QUERIES, TIME_BUDGET_MINUTES,
        SHARD_INDEX, SHARD_COUNT, BATCH_SIZE, PADDED_TOKENS)
print(STATUS)
''')
add('markdown', '''## Xuất bất kỳ lúc nào — không cần chấm đủ 1.200 query

Khi cần dừng để nộp, Interrupt cell worker rồi chạy các cell export dưới. Query chưa có
file score hoàn chỉnh vẫn giữ baseline. Lỗi CUDA cần restart phiên và mở lại đúng RUN
để export; không chạy thêm GPU trong context lỗi.

Mặc định giữ danh sách document baseline, chỉ chọn lại chunk trong các document đó.
Candidate từ document mới chỉ có thể được chọn nếu UPDATE_DOCUMENTS=True; chế độ đó
cũng xếp lại document và có rủi ro thay đổi điểm document. Không có GT để bảo đảm tăng điểm.
''')
add('code', '''# Đọc links nếu có. Không có allowlist sẽ giữ baseline cho các query định thay đổi.
ALLOWED_IDS = None
try:
    import pyarrow.parquet as pq
    LINKS_FILE = official_file(LINKS_FILE, 'links_corpus_vi_en_zh.parquet')
    assert core.file_hash(LINKS_FILE) == corpus.LINKS_SHA, 'Wrong official links snapshot'
    ALLOWED_IDS = set()
    for batch in pq.ParquetFile(LINKS_FILE).iter_batches(columns=['id']):
        values = batch.column(0).to_pylist()
        assert all(type(value) is int for value in values)
        ALLOWED_IDS.update(values)
    assert ALLOWED_IDS
except (OSError, AssertionError, ValueError) as exc:
    ALLOWED_IDS = None
    print('Allowlist unavailable:', exc, '\\nChanged queries will fall back to baseline.')
''')
add('code', '''UPDATE_DOCUMENTS = False  # True để thử thêm/xếp lại document; xuất sang folder mới mỗi lần
DESTINATION = RUN/'exports'/time.strftime('%Y%m%d_%H%M%S')
with progress('Export all queries with baseline fallback'):
    REPORT = fallback.export(PLAN, RUN, ROWS, QUERIES, INPUTS, BASELINE_RAW,
        DESTINATION, ALLOWED_IDS, UPDATE_DOCUMENTS)
print(json.dumps(REPORT, indent=2))
print('Output:', DESTINATION)
''')
add('code', '''# Xem materially_changed_queries và selected_new_candidate_occurrences trước khi nộp.
# Nếu đều bằng 0, file chỉ giữ tập evidence baseline hoặc thay thứ tự.
from google.colab import files
files.download(str(DESTINATION/'submission.zip'))
''')
notebook = dict(cells=cells, metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
    language_info=dict(name='python'), colab=dict(provenance=[])), nbformat=4, nbformat_minor=5)
(ROOT/'colab/stage9_partial_retrieval_fallback_colab.ipynb').write_text(
    json.dumps(notebook, ensure_ascii=False, indent=1)+'\n', encoding='utf-8')
