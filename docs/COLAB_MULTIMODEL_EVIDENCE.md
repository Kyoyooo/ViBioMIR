# Nhiều GPU chọn evidence: Qwen + BGE và model xử lý bất đồng

## 1. Hai file cần sử dụng

- [`stage9_multimodel_worker_colab.ipynb`](../colab/stage9_multimodel_worker_colab.ipynb):
  mở ở nhiều phiên GPU, chọn model/vai trò và shard trong mỗi phiên.
- [`stage9_multimodel_merge_colab.ipynb`](../colab/stage9_multimodel_merge_colab.ipynb):
  chạy CPU, xác minh điểm, gộp thứ hạng, tạo báo cáo bất đồng và xuất ZIP.

Tất cả code được nhúng trong notebook; không cần upload scripts hoặc các bundle Stage 8.
Đây là mở rộng cho thử nghiệm trên `submission_17.zip`, không thay pipeline frozen S09.

```text
submission_17.zip + query.parquet
          ↓ cùng một pool, có thể thêm đoạn con
GPU A: Qwen 0.6B        GPU B: BGE v2-m3
          ↘             ↙
           CPU: rank fusion + phát hiện bất đồng
                     ↓ tùy chọn
           GPU C: Qwen reranker 4B chấm phần tranh chấp
                     ↓
           CPU: chọn evidence, kiểm tra nguyên văn, xuất ZIP
```

Hai worker đầu là rerankers, không phải hai model sinh lời giải rồi tự xác nhận nhau.
Model thứ ba cũng là reranker; nó không cung cấp lý do y khoa hoặc nhãn GT đã xác nhận.
Qwen 0.6B và Qwen 4B thuộc cùng họ model nên lỗi có thể tương quan. Ensemble không
bảo đảm tốt hơn model đơn; bản nộp tốt nhất hiện tại vẫn cần giữ làm dự phòng.

## 2. Đầu vào và phạm vi

Trên cùng **thư mục Drive vật lý** mà mọi phiên có quyền truy cập:

```text
MyDrive/ViBioMIR/
    submission_17.zip
    query.parquet
```

Nếu các phiên dùng tài khoản khác nhau, cần cùng thư mục chia sẻ/shortcut có quyền đọc ghi;
cùng tên đường dẫn nhưng là hai bản sao thư mục khác nhau sẽ không ghép được worker outputs.
Chỉ dùng các phiên GPU thực tế được nền tảng cấp; notebook không tự tạo GPU/runtime.

Notebook khóa checksum input ZIP và query đã phân tích. Có 1.200 query và 120 chunks/query.
`INCLUDE_SPANS=False` giữ đúng pool đó. `True` bổ sung tối đa 4 đoạn con cho các parent dài
bằng cùng heuristic/offsets như thử nghiệm Qwen trước; không phải semantic chunking bằng model.

Không bổ sung docs/candidates ngoài submission. Các docs không có chunk trong ZIP vẫn
không có văn bản để model xem. `chunk_order` không phải vị trí trong tài liệu nguồn.
Nguồn được kiểm tra là parent chunk trong submission, không phải full corpus gốc.

## 3. Model đã khóa revision

| Vai trò | Model | Revision | Ngày revision |
|---|---|---|---|
| `qwen` | Qwen/Qwen3-Reranker-0.6B | `6e9e69830b95c52b5fd889b7690dda3329508de3` | 09/06/2025 |
| `bge` | BAAI/bge-reranker-v2-m3 | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | 24/06/2024 |
| `arbiter` | Qwen/Qwen3-Reranker-4B | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` | 16/04/2026 |

Các revision được đối chiếu metadata công khai trước cutoff 01/08/2026 trong đặc tả repository.
Model được tải theo revision, runtime đếm tham số ≤15B và kiểm tra context/head phù hợp.
Qwen lấy hiệu logits yes/no; BGE lấy sequence-classification logit. Không cộng trực tiếp
điểm thô giữa model. Hai loại dùng FP16/SDPA, max 4.096 tokens và từ chối input quá dài,
không âm thầm truncate. Model 4B có ngân sách batch ban đầu nhỏ hơn và cần pilot trên GPU thật.

Hướng dẫn chính thức:
[Qwen reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B),
[Qwen 4B](https://huggingface.co/Qwen/Qwen3-Reranker-4B),
[BGE model card](https://huggingface.co/BAAI/bge-reranker-v2-m3).

## 4. Bắt đầu với hai GPU

Mở cùng worker notebook ở hai phiên GPU, chạy setup/dependencies/inputs, rồi cấu hình:

| Biến | Phiên A | Phiên B |
|---|---|---|
| `ROLE` | `'qwen'` | `'bge'` |
| `INCLUDE_SPANS` | `False` | `False` |
| `SHARD_COUNT` | `1` | `1` |
| `SHARD_INDEX` | `0` | `0` |
| `CAMPAIGN` | `'ensemble_v1'` | `'ensemble_v1'` |

Có thể đặt spans True nhưng phải giống nhau ở mọi worker. Plan hash sẽ khác và tạo run mới.
Các worker in đường dẫn plan; cả hai phải trùng nhau để ghép.

Mặc định mỗi worker dừng sau **20 query mới**. Đây là pilot để đo thời gian/VRAM và xem
tỷ lệ bất đồng; chưa dùng để kết luận chất lượng. Trước khi chạy toàn bộ, mở merge notebook
để tạo chẩn đoán. Pilot chỉ tạo báo cáo, không xuất ZIP thiếu query.

Để tiếp tục hết 1.200 query, chạy trong từng worker:

```python
STOP_AFTER_QUERIES = None
```

Sau đó chạy lại **chỉ cell worker**, không chạy lại cell configure đặt giới hạn về 20.
Manifest/score file có checksum, query binding, candidate-pool hash và model registry.
Các query đã hoàn tất được dùng lại sau khi xác minh; query dở có thể tính lại.
Không chạy hai phiên cùng role + shard + campaign. Không trộn hai số shard trong một campaign.

Nếu chỉ có một GPU, có thể chạy Qwen, đổi runtime rồi chạy BGE, sau đó merge.

### Nếu đã có điểm Qwen từ notebook trước

Ở worker `qwen`, đặt `REUSE_QWEN_RUN` bằng Path tới run của
`stage9_qwen_score_submission.ipynb`. Hệ thống chỉ import nếu nguồn, query, span policy,
scoring policy, file hash và candidate hashes khớp. Nó tạo bản ghi mới có provenance,
giữ nguyên scores cũ đã xác minh; không sửa hash để ép qua kiểm tra.

Nếu run cũ không khớp, chọn đúng run hoặc đặt None để chấm mới. Không import điểm BGE
baseline cũ vì ZIP không chứa input retrieval_text/title/context của lần đó.

## 5. Nếu có bốn GPU

Dùng hai shard cho mỗi model:

| Phiên | ROLE | SHARD_COUNT | SHARD_INDEX |
|---|---|---:|---:|
| A | qwen | 2 | 0 |
| B | qwen | 2 | 1 |
| C | bge | 2 | 0 |
| D | bge | 2 | 1 |

Query được chia theo vị trí modulo shard count. Một query không bị hai shard cùng model
chấm lặp. Merge kiểm tra không có query chồng lấp hoặc pool/model khác nhau.
Đổi từ 1 sang 2 shards nên dùng campaign mới, ví dụ `ensemble_2shards_v1`, thay vì để các
worker cũ lẫn trong cùng cây thư mục. Không hứa tăng tốc tuyến tính vì Drive/startup/tải model.

## 6. Ghép hai model

Mở merge notebook CPU, chọn đúng plan directory. Nó có thể tự chọn khi chỉ có một plan.
Kết quả bao gồm:

- Số query hoàn tất ở từng model và giao giữa hai model.
- `DISAGREEMENT_REVIEW.jsonl`: query, một số candidate nguyên văn và thứ hạng hai model.
- `EVIDENCE_PROVENANCE.jsonl`: span/parent offsets và lý do chọn/loại theo từng biến thể.
- `REPORT.json`: hash, phạm vi, các số lượng và biến thể export.
- Một request dành cho model thứ ba, nếu muốn xử lý bất đồng.

RRF hiện dùng `1/(60+rank_Qwen) + 1/(60+rank_BGE)` trên cùng candidate IDs.
Mỗi model có trọng số ngang nhau. Đây là cấu hình khởi đầu, không phải trọng số đã được tối ưu.

Một candidate được đưa vào tập tranh chấp nếu lọt top 20 ở một model mà không ở model kia,
hoặc lệch ít nhất 30 hạng. Tối đa 40 candidates/query, ưu tiên bất đồng top-k và độ lệch lớn.
Các số là heuristic để giới hạn chi phí, không phải ngưỡng relevance hoặc độ tin cậy.

## 7. Model thứ ba là tùy chọn

Sau merge, copy nguyên request path sang worker notebook, đặt:

```python
ROLE = 'arbiter'
REQUEST_FILE = Path('/content/drive/.../requests/request-<hash>.json')
```

Giữ cùng spans/campaign/plan. Chạy configure để áp dụng role và ngân sách batch cho 4B,
rồi worker. Nó chỉ chấm các candidates được yêu cầu. Dữ liệu/điểm gốc thay đổi sẽ làm
request không còn hợp lệ; không được tự sửa hash.

Khi xong, quay lại merge notebook, đặt trong một cell mới:

```python
ARBITRATION_REQUEST = Path('/content/drive/.../requests/request-<hash>.json')
```

Rồi chạy lại **cell merge**. Bản arbitrated chỉ được xuất khi có đủ base scores toàn bộ
query và arbiter hoàn tất đúng request full. Request pilot không được dùng để tuyên bố
đã xử lý bất đồng cho toàn bộ query. Cache model có thể tái sử dụng cặp query/passage
khớp chính xác trong cùng runtime khi request thay đổi, nhưng query files thuộc request khác không bị ép dùng lại.

Arbiter chỉ đổi thứ tự giữa các slot dành cho các ứng viên tranh chấp trong ranking RRF.
Ứng viên ngoài tập tranh chấp giữ vị trí. Không cộng điểm model thứ ba chỉ cho những đoạn
đã hỏi rồi mặc định đoạn chưa hỏi có điểm 0: cách đó tạo lợi thế giả cho đoạn được hỏi.

## 8. Các ZIP có thể xuất khi đủ query

| ZIP | Mục đích |
|---|---|
| S0_baseline.zip | JSON baseline giữ nguyên byte |
| Qwen_80_cap2.zip | Chọn theo Qwen, tối đa 80 chunks/query, 2/doc |
| BGE_80_cap2.zip | Chọn theo BGE, cùng ngân sách để so sánh |
| RRF_120_cap6.zip | Ensemble, ngân sách gần baseline |
| RRF_80_cap2.zip | Ensemble, ngân sách evidence chặt hơn |
| Arbitrated_120_cap6.zip | Nếu có full arbiter request: đổi thứ tự phần tranh chấp |

Giữ nguyên danh sách và thứ tự docs của submission gốc trong mọi biến thể. Nếu không bật
spans, chọn đủ toàn bộ 120 chunks gốc có thể chỉ đổi thứ tự và không đổi điểm set-based.
Report in số query có tập evidence thay đổi để nhận ra trường hợp này.

Overlaps chỉ tính giữa spans có chung parent; chưa có source offsets để đo giữa parent
khác nhau. Exact duplicates chỉ loại trong cùng doc, không xóa xuyên doc mặc định.
Caps là trần, không bắt buộc lấp đầy bằng đoạn yếu. Không tự động chọn ZIP “tốt nhất”.

## 9. Đây có phải silver GT không?

Đây là **đồng thuận và bất đồng của model** để chọn kết quả và ưu tiên review.
Không phải nhãn relevance do con người xác nhận và không tái tạo gold spans BTC.
Hai model đồng ý vẫn có thể cùng sai. Qwen 4B xử lý bất đồng cũng chưa là trọng tài chân lý.

Notebook không đo F2/LCS chính thức, không kiểm tra tính đúng y khoa và không đối chiếu
corpus gốc. Evidence được kiểm tra là chuỗi con nguyên văn của parent trong input submission.
Các giới hạn này được ghi trong REPORT thay vì gán `passed` cho relevance.

## 10. Code và kiểm thử

- [`multimodel_evidence.py`](../scripts/multimodel_evidence.py): workers, fusion và arbitration.
- [`qwen_submission_trial.py`](../scripts/qwen_submission_trial.py): input, span generation, selection và kiểm tra nguồn.
- [`build_multimodel_notebooks.py`](../scripts/build_multimodel_notebooks.py): builder tự chứa code trong notebook.
- [`test_multimodel_evidence.py`](../tests/test_multimodel_evidence.py): fixture pilot/resume,
  sharding, cache import, tamper rejection, rank fusion, arbitration và model forward API.

Không sửa code scoring trong lúc workers đang chạy. Khi đổi code, dùng bộ notebook mới
và plan mới; baseline/runs trước được giữ lại để so sánh.
