# Tận dụng retrieval dang dở, giữ baseline cho query chưa xử lý

Chạy [stage9_partial_retrieval_fallback_colab.ipynb](../colab/stage9_partial_retrieval_fallback_colab.ipynb) trong một phiên Colab GPU mới. Notebook đã nhúng code hỗ trợ; không cần upload repository. Không cần chờ retrieval hoàn tất hoặc tạo lại embedding.

## Luồng xử lý

```text
Baseline ZIP tốt nhất ──────────────────────────────────────┐
                                                          │
Checkpoint retrieval hiện có → snapshot → top 20/query      │
                                      ↓                   │
                              Đọc evidence nguồn           │
                                      ↓                   │
                         Gộp toàn bộ evidence baseline ←───┤
                                      ↓                   │
                             Một reranker chấm chung       │
                                      ↓                   │
                         Query có kết quả hoàn chỉnh       │
                                      ↓                   │
                              Chọn lại evidence            │
                                                          ↓
                 Thiếu score/lỗi/chưa xử lý → giữ baseline
                                      ↓
                      submission.zip đủ 1.200 query
```

Reranker mặc định là BGE. Có thể chọn Qwen khi tạo fallback plan khác. Đây là một model chấm cùng lúc evidence cũ và mới, không cộng điểm embedding với điểm reranker, không dùng score khác model/thang điểm để thay trực tiếp.

## Cần chỉnh ba đường dẫn

Trong cell đầu:

```python
ROOT = Path('/content/drive/MyDrive/ViBioMIR')
BASELINE_ZIP = ROOT / 'submission_17.zip'  # hoặc ZIP tốt nhất đã được xác nhận
OLD_RUN = Path('/content/drive/MyDrive/ViBioMIR/corpus_retrieval_trials/<campaign>/<signature>')
```

OLD_RUN là đúng đường dẫn RUN mà retrieval đã in; bên trong có PLAN.json và retrieval/qwen, bge, e5 hoặc bm25. Không chọn thư mục cha chứa nhiều run. Giữ nguyên PLAN/checkpoint cũ, kể cả chúng được tạo bằng phiên bản code cũ.

Các nguồn còn lại: query.parquet chính thức, sáu structural source ZIP trong dense_stage8_bge_structure/bundles, links chính thức hoặc Data.zip chứa chúng. Query và source snapshot được kiểm tra đúng danh tính. Baseline được ghi lại hash và kiểm tra đủ query/schema, nhưng điểm thi của baseline cần do bạn xác nhận.

## 1. Đóng băng checkpoint

Notebook đọc checkpoint.json hoặc result.json mới nhất theo số batch đã commit của từng shard. File được đọc một lần thành bytes, kiểm tra chữ ký và copy sang snapshot riêng. Snapshot chỉ dùng dữ liệu, không chạy lại code producer cũ và không ép producer cũ vượt check_plan bằng code mới.

- Qwen/BGE/E5 có checkpoint dù paused vẫn dùng được.
- Không bắt buộc đủ bốn role hoặc đủ mọi shard. Chỉ nhận coverage thực sự đã commit.
- BM25 chỉ có database mà chưa có result.json chưa được nhập. Không bắt nó chạy lại truy vấn trong notebook này vì có thể tốn nhiều thời gian.
- Không cần file vector .npy để chọn candidate; checkpoint đã chứa chunk ID và retrieval score.
- Không sửa hoặc ghi vào RUN cũ. Retrieval có thể tiếp tục; snapshot đã đóng băng sẽ không đổi theo.
- Đổi SNAPSHOT_LABEL nếu muốn lấy ảnh chụp mới ở thời điểm khác. Không ghi đè snapshot đã dùng để chấm.

Các nhánh quét những phạm vi corpus khác nhau. RRF ở đây chỉ là cách ưu tiên candidate để chấm; không thể coi thứ hạng hợp nhất là kết quả tìm kiếm toàn corpus.

## 2. Tạo pool và xác minh nguồn

Mặc định lấy tối đa 20 chunk ID/query sau RRF từ checkpoint sẵn có. Notebook đọc lại các batch đã quét, lấy chunk_text gốc và kiểm tra payload hash, quan hệ document/version, checksum văn bản.

Sau đó gộp với **toàn bộ evidence đã có trong baseline** của query, loại trùng cùng document/văn bản. Vì thế với baseline có 120 chunk, pool thường tối đa khoảng 140 chunk/query. Các candidate trùng baseline không tính là phần bổ sung.

Query không có evidence bổ sung không cần chấm lại, được giữ baseline. Candidate thuộc query nào cũng phải khớp query chính thức từ producer plan.

Prepare vẫn phải đọc các batch nguồn đã quét để tìm chunk ID. Với vài trăm batch, bước này có thể tốn thời gian I/O. Không có cam kết xong trong vài phút. Prepare thành công được lưu để không phải đọc nguồn lại ở lần mở notebook sau.

Baseline chỉ được xác nhận từ ZIP đầu vào; không có chunk ID/offset nguồn của nó trong ZIP. Báo cáo ghi rõ baseline_source_verified=False và source_verified riêng cho candidate mới. Không gán nhãn toàn bộ kết quả là đã kiểm tra corpus gốc.

## 3. Chấm trong thời gian cho phép

```python
RERANK_MODEL = 'bge'
STOP_AFTER_QUERIES = 5
TIME_BUDGET_MINUTES = 60
```

Sau pilot, đặt STOP_AFTER_QUERIES=None, chạy lại **riêng cell worker**. TIME_BUDGET_MINUTES tính lại cho mỗi lần gọi worker và dừng giữa các query, không cắt ngang query đang chấm. Log từng query có số pair và thời gian để bạn tự ước lượng phần còn lại.

Cả evidence baseline và mới đều được chấm bằng **chunk_text**, vì baseline không có retrieval_text/context giống nguồn. Không reuse score cũ được chấm bằng title/context khác. Một số query có thể không hưởng lợi từ việc bỏ context; đây là policy cố ý thống nhất đầu vào để so sánh chung.

Mỗi query lưu một score file có chữ ký, hash packet và model policy. Chạy tiếp cùng RUN sẽ reuse score hợp lệ. Nếu query vượt token budget hoặc lỗi dữ liệu, notebook ghi error-queryid và tiếp tục; query đó dùng baseline khi export. Lỗi tải model/thiếu CUDA là lỗi phiên và được báo ngay. Lỗi CUDA nghiêm trọng có thể dừng worker; restart riêng phiên fallback để export các query đã lưu.

Nếu muốn chia hai GPU, dùng cùng RUN và model, SHARD_COUNT=2, SHARD_INDEX lần lượt 0 và 1. Chỉ một phiên chạy prepare. Không để hai phiên cùng chấm một query.

## 4. Export ngay khi cần nộp

Không cần chờ chấm đủ. Interrupt worker, rồi chạy các cell allowlist và export. Các score file hoàn chỉnh đã lưu được dùng; query đang chấm dở chưa có file sẽ giữ nguyên baseline.

Nếu chưa prepare hoặc chưa chấm query nào, vẫn export được bản nền. Khi không có thay đổi, nội dung submission.json được giữ byte-for-byte từ baseline, ZIP được đóng gói lại. Khi không lấy được allowlist ID chính thức, các query định thay đổi cũng quay về baseline và báo rõ lý do.

Hai chính sách xuất dùng cùng score:

| UPDATE_DOCUMENTS | Kết quả |
|---|---|
| False — mặc định | Giữ nguyên relevant_docs của baseline; chỉ chọn evidence trong các document đó. Candidate từ document mới không được đưa vào submission. |
| True | Xếp document theo score chunk cao nhất, lấy tối đa 120 rồi bổ sung document baseline còn thiếu chỗ. Có thể thêm hoặc loại document so với baseline. |

Muốn tận dụng document mới từ retrieval, cần đặt True và export sang folder mới. Đây là thay đổi mạnh hơn, có thể ảnh hưởng metric document, chưa có bảo đảm tăng điểm.

Giới hạn lựa chọn: tối đa 120 chunk/query, 6 chunk/document. Candidate mới giữ một version/document và loại overlap nguồn ≥0,8 trong cùng region. Vì không có version mapping cho baseline, nếu một document đã chọn evidence baseline thì không trộn thêm evidence mới của document đó, và ngược lại. Việc này có thể loại bỏ các đoạn bổ trợ; báo cáo provenance giúp kiểm tra quyết định.

Fallback là ở cấp query: thiếu score, packet lỗi hoặc kết quả không hợp lệ thì giữ nguyên toàn bộ row baseline. Query đã rerank được chọn theo model; chưa có GT hoặc confidence threshold để bảo đảm nó tốt hơn baseline.

## Kết quả để kiểm tra

- submission.zip: một file gốc submission.json, đủ query theo baseline và query chính thức.
- REPORT.json: số query rerank/fallback, số query thay đổi tập evidence/document, số candidate mới được chọn, hash ZIP/JSON và phạm vi kiểm tra.
- QUERY_AUDIT.json: lý do giữ baseline hoặc thay đổi của từng query.
- EVIDENCE_PROVENANCE.jsonl: nguồn baseline hoặc partial retrieval, chunk ID/offset/version của các đoạn mới.

Đọc ít nhất ba trường:

```text
reranked_queries
materially_changed_queries
selected_new_candidate_occurrences
```

Nếu hai trường sau bằng 0, chưa có tập evidence/document mới được đưa vào, dù thứ tự có thể thay đổi. “Reranked” không đồng nghĩa “tốt hơn”. Trước hạn nên giữ sẵn ZIP tốt nhất đã có điểm để dự phòng.

## Kiểm chứng

7 kiểm thử offline: checkpoint thiếu nhánh, snapshot bất biến khi producer tiếp tục, đọc source gzip, gộp pool, score/resume giả, fallback khi thiếu/sai score, lựa chọn document tùy chọn, export đủ 1.200 query khi chỉ một query đã chấm và kiểm tra source nhúng trong notebook. Không chạy weights pretrained trên GPU trong phiên phát triển; chưa đo điểm thi của bản kết hợp.
