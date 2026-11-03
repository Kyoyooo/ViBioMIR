"""Build two self-contained Colab notebooks for parallel model workers and CPU fusion."""
import ast
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=(ROOT/'scripts/qwen_submission_trial.py').read_text(encoding='utf-8')
ENSEMBLE=(ROOT/'scripts/multimodel_evidence.py').read_text(encoding='utf-8')


def cell(kind,name,source):
    v=dict(cell_type=kind,id=name,metadata={},source=source.splitlines(keepends=True))
    if kind=='code':
        ast.parse(source)
        v.update(execution_count=None,outputs=[])
    return v


def save(name,cells):
    nb=dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3'),
        colab=dict(name=name)),nbformat=4,nbformat_minor=5)
    p=ROOT/'colab'/name
    p.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    print(p)


setup='''from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import json, sys, os, subprocess, hashlib, time, threading
ROOT = Path('/content/drive/MyDrive/ViBioMIR')
SUBMISSION_ZIP = ROOT / 'submission_17.zip'
QUERY_FILE = ROOT / 'query.parquet'
CAMPAIGN = 'ensemble_v1'  # Same across all workers. Use a new campaign if changing shard count.
RUN_PARENT = ROOT / 'multimodel_evidence' / CAMPAIGN
assert SUBMISSION_ZIP.is_file(), f'Upload submission_17.zip: {SUBMISSION_ZIP}'
assert QUERY_FILE.is_file(), f'Upload query/query.parquet as {QUERY_FILE}'
os.environ.update(USE_TF='0', USE_FLAX='0', TOKENIZERS_PARALLELISM='false')
'''
helper=f'''BASE_SOURCE = {BASE!r}
ENSEMBLE_SOURCE = {ENSEMBLE!r}
CODE_DIR = Path('/content/multimodel_evidence_code')
CODE_DIR.mkdir(parents=True, exist_ok=True)
for name, source in [('qwen_submission_trial', BASE_SOURCE), ('multimodel_evidence', ENSEMBLE_SOURCE)]:
    if name in sys.modules:
        assert Path(sys.modules[name].__file__).read_text(encoding='utf-8') == source, 'Restart runtime to load new support code.'
    else:
        (CODE_DIR / (name + '.py')).write_text(source, encoding='utf-8')
sys.path.insert(0, str(CODE_DIR))
import qwen_submission_trial as core
from multimodel_evidence import build_plan, validate_plan, score_worker, merge_and_export
from contextlib import contextmanager
@contextmanager
def progress(label):
    stop = threading.Event()
    start = time.monotonic()
    def heartbeat():
        while not stop.wait(30):
            print(label, 'minutes:', round((time.monotonic()-start)/60, 1), flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    print(label, flush=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
'''
inputs='''with progress('Verify input ZIP, official query snapshot and evidence schema'):
    rows, queries, inputs, baseline_raw = core.load_inputs(SUBMISSION_ZIP, QUERY_FILE)
print('Queries:', len(rows), 'Input chunks:', sum(len(r['relevant_chunks']) for r in rows))
'''

save('stage9_multimodel_worker_colab.ipynb',[
cell('markdown','intro','''# Worker GPU: Qwen / BGE / Qwen 4B xử lý bất đồng

Mở notebook này trong **hai phiên GPU**: phiên A đặt `ROLE='qwen'`, phiên B `ROLE='bge'`.
Hai phiên dùng cùng nguồn Drive và cùng `INCLUDE_SPANS`. Chúng chấm cùng pool độc lập.
Có thể chạy tuần tự nếu chỉ một GPU. Code được nhúng sẵn, không cần upload script.

Upload `submission_17.zip` và `query.parquet` vào `MyDrive/ViBioMIR` trước.
Pool chỉ gồm 120 chunks/query trong submission và đoạn con nếu bật; không phải top 400 cũ.
Giữ nguyên docs. Đây là ensemble rerankers; không tự tạo human GT hoặc xác nhận tính đúng y khoa.

Pilot mặc định dừng sau 20 query MỚI cho từng worker. Sau đó mở notebook merge để xem
chẩn đoán; pilot không xuất ZIP nộp. Để chạy hết: đặt `STOP_AFTER_QUERIES=None` trong
cell mới rồi chạy lại cell worker. Không chạy lại cell configure nếu không muốn reset biến.

Worker thứ ba là tùy chọn: sau khi merge tạo request, dùng phiên GPU khác với
`ROLE='arbiter'` và `REQUEST_FILE` là đường dẫn request. Qwen3-Reranker-4B chỉ chấm các
ứng viên tranh chấp, không chấm toàn pool. Cần pilot VRAM/thời gian, không bảo đảm phù hợp mọi GPU.
'''),
cell('code','setup',setup),
cell('code','dependencies','''subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
    'pyarrow==22.0.0', 'transformers==4.57.1', 'tokenizers==0.22.1',
    'safetensors>=0.4,<1', 'sentencepiece>=0.2,<0.3'], check=True)
print('Restart if Colab requests it, then rerun setup before model import.')
'''),
cell('code','helpers',helper),
cell('code','inputs',inputs),
cell('code','configure','''ROLE = 'qwen'  # Other session: 'bge'. Optional third session: 'arbiter'.
INCLUDE_SPANS = False  # Must match across ALL models. True adds up to 4 subspans per long parent.
SHARD_COUNT = 1  # For 4 GPUs: 2 shards/model; use 0 and 1 in separate sessions.
SHARD_INDEX = 0
BATCH_SIZE = 8 if ROLE != 'arbiter' else 2
PADDED_TOKENS = 8192 if ROLE != 'arbiter' else 4096
STOP_AFTER_QUERIES = 20
REQUEST_FILE = None  # Arbiter only: Path('.../requests/request-<hash>.json') printed by merge notebook.
REUSE_QWEN_RUN = None  # Optional Path to a matching old stage9_qwen_score_submission run.

plan = build_plan(inputs, core.generation_policy(INCLUDE_SPANS))
RUN = RUN_PARENT / plan['signature']
RUN.mkdir(parents=True, exist_ok=True)
if (RUN / 'plan.json').is_file():
    assert core.read(RUN / 'plan.json') == plan, 'Existing plan differs'
else:
    core.write(RUN / 'plan.json', plan)
request = core.read(REQUEST_FILE) if REQUEST_FILE is not None else None
assert ROLE in ('qwen', 'bge', 'arbiter')
assert ROLE != 'arbiter' or request is not None, 'Set REQUEST_FILE from merge output'
LOCAL_CACHE = Path('/content/multimodel_evidence_cache') / plan['signature'] / (ROLE + '.sqlite3')
print('Plan:', RUN)
print('Role/shard:', ROLE, SHARD_INDEX, '/', SHARD_COUNT)
print('Model:', plan['models'][ROLE])
print('Input original pairs:', sum(len(r['relevant_chunks']) for r in rows))
if INCLUDE_SPANS:
    with progress('Count shared originals + subspans'):
        print('Candidate pairs:', sum(len(core.make_candidates(r,plan['generation'])) for r in rows))
if request is not None:
    print('Arbiter requested pairs:', sum(len(t['requested_candidate_ids']) for t in request['tasks']))
'''),
cell('code','worker','''# After pilot: STOP_AFTER_QUERIES=None, then rerun THIS cell only.
import torch
assert torch.cuda.is_available(), 'Select CUDA GPU runtime'
print('GPU:', torch.cuda.get_device_name(0))
started = time.monotonic()
with progress('Model ' + ROLE):
    result = score_worker(rows, queries, plan, RUN, ROLE,
        shard_index=SHARD_INDEX, shard_count=SHARD_COUNT, request=request,
        stop_after_queries=STOP_AFTER_QUERIES, local_cache=LOCAL_CACHE,
        batch_size=BATCH_SIZE, padded_tokens=PADDED_TOKENS,
        reuse_qwen=REUSE_QWEN_RUN if ROLE == 'qwen' else None)
print('State:', result['state'], 'Completed assigned queries:', len(result['completed_query_ids']),
      '/', len(result['expected_query_ids']))
print('Elapsed seconds:', round(time.monotonic()-started, 1))
print('Plan directory for merge:', RUN)
if result['state'] != 'complete':
    print('PILOT/PARTIAL: set STOP_AFTER_QUERIES=None and rerun this worker cell to finish.')
'''),
])

save('stage9_multimodel_merge_colab.ipynb',[
cell('markdown','intro','''# CPU: ghép Qwen + BGE, xem bất đồng, xuất ZIP

Không tải model. Đọc và xác minh điểm của hai worker trên cùng pool, ghép bằng RRF
để tránh cộng trực tiếp hai thang điểm khác nhau. Chỉ xuất ZIP khi đủ 1.200 query ở cả hai model.
Nếu mới chạy pilot, tạo báo cáo và request nhưng **không xuất submission thiếu query**.

Mẫu bất đồng gồm: một model xếp trong top 20 nhưng model kia không, hoặc lệch ít nhất
30 hạng; giới hạn tối đa 40 ứng viên/query. Đây là heuristic review, không phải nhãn đúng/sai.

Tùy chọn: copy request path sang worker GPU với `ROLE='arbiter'`. Khi worker đó xong,
đặt `ARBITRATION_REQUEST` tại đây rồi chạy lại cell merge. Model thứ ba chỉ đổi thứ tự
giữa các ứng viên được hỏi, giữ nguyên vị trí của các ứng viên ngoài tranh chấp.

Giữ nguyên docs, kiểm tra spans là nguyên văn của chunk trong submission.
Không có corpus nguồn hoặc evaluator BTC; model đồng thuận không được gọi là GT.
'''),
cell('code','setup',setup),
cell('code','dependencies',"subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyarrow==22.0.0'], check=True)\n"),
cell('code','helpers',helper),
cell('code','inputs',inputs),
cell('code','choose-plan','''PLAN_DIRECTORY = None  # Paste Path printed by GPU workers if several plans exist.
if PLAN_DIRECTORY is None:
    plans = []
    for path in sorted(RUN_PARENT.glob('*/plan.json')):
        value = core.read(path)
        print('Plan:', path.parent, 'spans:', value['generation']['include_spans'])
        if value['inputs'] == inputs:
            plans.append(path.parent)
    assert len(plans) == 1, 'Set PLAN_DIRECTORY explicitly to the shared worker plan'
    PLAN_DIRECTORY = plans[0]
RUN = Path(PLAN_DIRECTORY)
plan = core.read(RUN / 'plan.json')
validate_plan(plan, inputs)
ARBITRATION_REQUEST = None  # Optional Path to the request scored by the arbiter worker.
TOP_K_DISAGREEMENT = 20
RANK_GAP = 30
MAX_ARBITER_CANDIDATES_PER_QUERY = 40
'''),
cell('code','merge','''from datetime import datetime, timezone
DESTINATION = RUN / ('merge_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
request = core.read(ARBITRATION_REQUEST) if ARBITRATION_REQUEST is not None else None
with progress('Verify worker scores, merge ranks and select evidence'):
    report = merge_and_export(rows, queries, plan, RUN, DESTINATION, baseline_raw,
        request=request, top_k=TOP_K_DISAGREEMENT, rank_gap=RANK_GAP,
        request_limit=MAX_ARBITER_CANDIDATES_PER_QUERY)
print('State:', report['state'])
print('Qwen / BGE / shared:', report['qwen_complete_queries'], report['bge_complete_queries'], report['shared_queries'])
print('Disputed queries:', report['queries_with_disagreements'], 'Arbiter pairs:', report['requested_pairs'])
print('Request path:', report['request_path'])
print('Report and review:', DESTINATION)
for item in report['exports']:
    print(item['zip'], 'changed queries:', item['changed_queries'], 'chunks:', item['chunks'], 'subspans:', item['subspans'])
if not report['exports']:
    print('PILOT ONLY: finish all base workers, then rerun this merge cell; no submission ZIP was created.')
print('Relevance, official LCS and clinical correctness: not measured.')
'''),
cell('code','download','''assert report['state'] == 'full_export', 'Pilot diagnostics cannot be submitted; finish both model workers first'
DOWNLOAD_NAME = 'RRF_80_cap2.zip'  # A trial configuration, not an automatically selected best result.
assert DOWNLOAD_NAME in ['S0_baseline.zip'] + [e['zip'] for e in report['exports']]
from google.colab import files
files.download(str(DESTINATION / DOWNLOAD_NAME))
'''),
])
