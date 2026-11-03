"""Generate two standalone Colab notebooks embedding the same audited support module."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = (ROOT / 'scripts/qwen_submission_trial.py').read_text(encoding='utf-8')


def cell(kind, name, source):
    value = dict(cell_type=kind, id=name, metadata={}, source=source.splitlines(keepends=True))
    if kind == 'code':
        ast.parse(source)
        value.update(execution_count=None, outputs=[])
    return value


def notebook(name, cells):
    result = dict(cells=cells, metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'),
                  colab=dict(name=name)), nbformat=4, nbformat_minor=5)
    path = ROOT / 'colab' / name
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1)+'\n', encoding='utf-8')
    print(path)


setup = '''from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, os, sys, subprocess, time, hashlib, zipfile, threading
ROOT = Path('/content/drive/MyDrive/ViBioMIR')
SUBMISSION_ZIP = ROOT / 'submission_17.zip'
QUERY_FILE = ROOT / 'query.parquet'
SCORE_PARENT = ROOT / 'qwen_submission_trial_v1'
assert SUBMISSION_ZIP.is_file(), f'Upload local submission_17.zip to {SUBMISSION_ZIP}'
assert QUERY_FILE.is_file(), f'Upload local query/query.parquet to {QUERY_FILE}'
os.environ.update(USE_TF='0', USE_FLAX='0', TOKENIZERS_PARALLELISM='false')
'''
helper = f'''SUPPORT_SOURCE = {SUPPORT!r}
SUPPORT_DIR = Path('/content/qwen_submission_trial_code')
SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
(SUPPORT_DIR / 'qwen_submission_trial_support.py').write_text(SUPPORT_SOURCE, encoding='utf-8')
assert 'qwen_submission_trial_support' not in sys.modules, 'Helper already loaded: continue from input cell or restart runtime before loading different code.'
sys.path.insert(0, str(SUPPORT_DIR))
from qwen_submission_trial_support import (
    MODEL_ID, REVISION, MODEL_DATE, load_inputs, generation_policy, model_policy,
    run_identity, signature, signed, checked, read, write, make_candidates,
    run_scoring, package_variants,
)
from contextlib import contextmanager
@contextmanager
def progress(label):
    event = threading.Event()
    started = time.monotonic()
    def heartbeat():
        while not event.wait(30):
            print(label, 'minutes:', round((time.monotonic()-started)/60, 1), flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    print(label, flush=True)
    thread.start()
    try:
        yield
    finally:
        event.set()
        thread.join()
'''
load = '''with progress('Read and validate ZIP + official query snapshot'):
    rows, queries, inputs, baseline_raw = load_inputs(SUBMISSION_ZIP, QUERY_FILE)
print('Queries:', len(rows), 'Original chunks:', sum(len(r['relevant_chunks']) for r in rows))
print('Verified input hashes:', inputs)
print('Source scope: exact slices of the submitted chunks; original corpus is not loaded.')
'''

notebook('stage9_qwen_score_submission.ipynb', [
    cell('markdown', 'intro', '''# 1. Chấm Qwen cho submission_17.zip

Upload **hai file dữ liệu** lên `MyDrive/ViBioMIR`: `submission_17.zip` và
`query.parquet` (file local `query/query.parquet`). Mã hỗ trợ được nhúng sẵn trong notebook.
Không cần mở hoặc dán nội dung JSON notebook vào cell Python.

Đây là thử nghiệm trên **120 chunks đã xuất/query**, không phải top 400 cũ.
Giữ baseline ZIP. Qwen không sinh câu trả lời; chỉ chấm mức liên quan query–passage.
Model: Qwen3-Reranker-0.6B, revision 09/06/2025 đã khóa. Không đổi embedding.

Mặc định **INCLUDE_SPANS=False** để thử đổi model trước. Nếu muốn chấm thêm đoạn con,
đặt True trước khi tạo identity; hệ thống sẽ dùng một thư mục run riêng.
Tách câu là heuristic đa ngữ, không phải phân đoạn ngữ nghĩa bằng model.
Giữ chunk gốc làm ứng viên; spans mới luôn là chuỗi con nguyên văn.

Chạy từng cell trong runtime GPU mới. Pilot dừng sau 5 query mới; sau đó chạy một cell
`STOP_AFTER_QUERIES = None` rồi chạy lại đúng cell worker. Không Run all để tiếp tục pilot.
Sau mất kết nối, chạy notebook từ đầu cùng cấu hình; query hoàn tất được xác minh và dùng lại.
Không chạy hai phiên cùng thư mục output. Không sửa hash để dùng cache khác model/input.
'''),
    cell('code', 'setup', setup),
    cell('code', 'dependencies', '''# Keep Colab's existing torch installation; dependencies are installed before model import.
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                'pyarrow==22.0.0', 'transformers==4.57.1', 'tokenizers==0.22.1',
                'safetensors>=0.4,<1'], check=True)
print('If Colab requests a restart, restart then run setup again before importing models.')
'''),
    cell('code', 'helper', helper),
    cell('code', 'inputs', load),
    cell('code', 'configure', '''INCLUDE_SPANS = False  # True adds up to 4 subspans per long original chunk.
GENERATION = generation_policy(INCLUDE_SPANS)
POLICY = model_policy()
BATCH_SIZE = 8
PADDED_TOKENS = 8192
STOP_AFTER_QUERIES = 5
identity = run_identity(inputs, GENERATION, POLICY)
RUN = SCORE_PARENT / signature(identity)
LOCAL_CACHE = Path('/content/qwen_submission_trial_cache') / (signature(identity) + '.sqlite3')
with progress('Count original and optional subspan pairs'):
    sizes = [len(make_candidates(row, GENERATION)) for row in rows]
print('Model:', MODEL_ID, 'revision:', REVISION, 'date:', MODEL_DATE)
print('Pairs before text dedup:', sum(sizes), 'max/query:', max(sizes))
print('Output directory:', RUN)
print('This does not measure relevance or official LCS score.')
'''),
    cell('code', 'worker', '''# To finish after pilot: run STOP_AFTER_QUERIES=None in a separate cell, then rerun THIS cell.
import torch
assert torch.cuda.is_available(), 'Runtime > Change runtime type > GPU'
print('GPU:', torch.cuda.get_device_name(0))
started = time.monotonic()
with progress('Qwen scoring'):
    result = run_scoring(rows, queries, identity, RUN, LOCAL_CACHE,
        stop_after_queries=STOP_AFTER_QUERIES, batch_size=BATCH_SIZE, padded_tokens=PADDED_TOKENS)
print('State:', result['state'])
print('Verified completed queries:', len(result['completed_query_ids']), '/', len(rows))
print('New queries this call:', result['new_queries_last_invocation'])
print('Seconds:', round(time.monotonic()-started, 1))
if result['state'] != 'complete':
    print('PILOT/PARTIAL: set STOP_AFTER_QUERIES=None, rerun only this worker cell. Do not export yet.')
else:
    print('COMPLETE: open stage9_qwen_select_submission.ipynb (CPU is sufficient).')
    print('Use RUN_DIRECTORY = Path(' + repr(str(RUN)) + ')')
'''),
])

notebook('stage9_qwen_select_submission.ipynb', [
    cell('markdown', 'intro', '''# 2. Chọn evidence từ điểm Qwen đã lưu

Notebook dùng CPU; không tải model và không chấm GPU lại. Cần cùng ZIP/query của notebook 1
và một run đã hoàn tất đủ 1.200 query. Code hỗ trợ được nhúng sẵn.

Giữ **đúng danh sách và thứ tự docs của baseline**. Có thể thay số lượng hoặc evidence,
nhưng không bổ sung văn bản cho các docs không có chunk trong ZIP gốc.

Xuất S0 baseline và các biến thể, kèm báo cáo/provenance. Không có chức năng tìm “bản tốt nhất”
tự động: điểm Qwen không phải điểm LCS của BTC; cần đánh giá relevance để chọn bản nộp.

Nếu vẫn chọn đủ toàn bộ 120 chunks gốc thì rerank chủ yếu đổi thứ tự; tập evidence có thể
không đổi. Do đó có thêm cấu hình lọc 80 chunks hoặc cap 2/doc để tạo thí nghiệm thực sự khác.
Nếu notebook 1 bật spans, có thêm các biến thể cho phép chọn đoạn con.
'''),
    cell('code', 'setup', setup),
    cell('code', 'dependencies', '''subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyarrow==22.0.0'], check=True)
'''),
    cell('code', 'helper', helper),
    cell('code', 'inputs', load),
    cell('code', 'find-run', '''# Paste the full output directory printed by notebook 1 if there is more than one complete run.
RUN_DIRECTORY = None  # Example: Path('/content/drive/MyDrive/ViBioMIR/qwen_submission_trial_v1/<hash>')
if RUN_DIRECTORY is None:
    completed = []
    for path in sorted(SCORE_PARENT.glob('*/manifest.json')):
        m = checked(read(path))
        print(path.parent.name, 'state:', m['state'], 'completed:', len(m['completed_query_ids']),
              'spans:', m['identity']['generation']['include_spans'])
        if m['state'] == 'complete' and m['identity']['inputs'] == inputs:
            completed.append(path.parent)
    assert len(completed) == 1, 'Set RUN_DIRECTORY to one completed matching run; finish notebook 1 if none is complete.'
    RUN_DIRECTORY = completed[0]
RUN_DIRECTORY = Path(RUN_DIRECTORY)
manifest = checked(read(RUN_DIRECTORY / 'manifest.json'))
identity = manifest['identity']
assert identity == run_identity(inputs, identity['generation'], identity['model_policy']), 'Source, policy or support code differs; use matching notebooks.'
assert manifest['state'] == 'complete', 'Finish all queries in notebook 1 first.'
print('Selected scoring run:', RUN_DIRECTORY)
'''),
    cell('code', 'variants', '''VARIANTS = [
    dict(name='S1_originals_120_cap6', originals_only=True,
         limits=dict(max_chunks=120, per_doc=6, overlap_iou=0.8)),
    dict(name='S1_originals_80_cap2', originals_only=True,
         limits=dict(max_chunks=80, per_doc=2, overlap_iou=0.8)),
    dict(name='S1_originals_120_cap2', originals_only=True,
         limits=dict(max_chunks=120, per_doc=2, overlap_iou=0.8)),
]
if identity['generation']['include_spans']:
    VARIANTS += [
        dict(name='S2_spans_120_cap6', originals_only=False,
             limits=dict(max_chunks=120, per_doc=6, overlap_iou=0.8)),
        dict(name='S2_spans_80_cap2', originals_only=False,
             limits=dict(max_chunks=80, per_doc=2, overlap_iou=0.8)),
    ]
print('Export variants:', [v['name'] for v in VARIANTS])
print('Caps are maxima; fewer available eligible candidates means fewer output chunks.')
'''),
    cell('code', 'export', '''from datetime import datetime, timezone
DESTINATION = RUN_DIRECTORY / ('selection_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
with progress('Verify scores and parent slices; select and package'):
    report = package_variants(rows, queries, identity, RUN_DIRECTORY, DESTINATION, baseline_raw, VARIANTS)
for item in report['exports']:
    print(item['zip'], 'changed queries:', item['changed_queries'],
          'chunks:', item['total_chunks'], 'subspans:', item['total_subspans'])
print('Output folder:', DESTINATION)
print('Validation scope: input submission slices; original corpus not independently verified.')
print('Official LCS/relevance score: not measured. Keep baseline and evaluate variants before choosing.')
'''),
    cell('code', 'download', '''# Select ONE file to download; outputs also remain on Drive.
DOWNLOAD_NAME = 'S1_originals_80_cap2.zip'
assert DOWNLOAD_NAME in ['S0_baseline.zip'] + [e['zip'] for e in report['exports']]
from google.colab import files
files.download(str(DESTINATION / DOWNLOAD_NAME))
'''),
])
