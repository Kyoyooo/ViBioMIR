# Thử nghiệm retrieval từ query và corpus, không dùng submission cũ

**Thành viên mới:** đọc [Thuyết minh pipeline và kế hoạch cải tiến cho nhóm](THUYET_MINH_PIPELINE_RETRIEVAL_CHO_NHOM.md) trước để hiểu mục tiêu, vai trò từng module, cách phân công và giới hạn của thí nghiệm. File hiện tại tập trung vào thao tác chạy Colab.

Bộ này thực hiện luồng bạn yêu cầu:

```text
Query gốc + structural corpus
  ├─ BM25 (chỉ mục từ khóa)
  ├─ BGE-M3 embedding (chỉ mục vector riêng)
  ├─ Qwen3-Embedding-0.6B (chỉ mục vector riêng)
  └─ multilingual-E5-large (chỉ mục vector riêng)
                  ↓
         RRF → pool tối đa 400 chunk/query
                  ↓
        Qwen reranker + BGE reranker
                  ↓
      RRF → chọn document và evidence gốc
                  ↓
          kiểm tra → submission.zip
```

Khác với notebook multimodel trước: pool được tìm trên corpus, không bị giới hạn bởi evidence trong `submission_17.zip`. Cả danh sách document lẫn evidence đều được chọn lại. Bộ này tạo embedding thật cho cả ba nhánh; chưa dùng lại index/candidate BGE cũ để tránh trộn cấu hình không tương thích.

## 1. Dữ liệu trên Drive

Mặc định thư mục gốc `/content/drive/MyDrive/ViBioMIR`:

| Đường dẫn | Dùng làm gì |
|---|---|
| `dense_stage8_bge_structure/bundles/worker_0.zip` … `worker_5.zip` | Văn bản structural chunks gốc |
| `query.parquet` | 1.200 query chính thức |
| `Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet` | Kiểm tra ID document trước xuất bản đầy đủ |
| `Data.zip` | Nguồn thay thế nếu chưa giải nén query/links chính thức |

Không cần `submission_17.zip`, `artifacts.zip` hoặc bundle S09 chứa code. Code hỗ trợ đã nhúng sẵn trong mỗi notebook. Không cần tải repository lên Colab.

Nguồn được khóa bằng snapshot structural 855.745 document / 5.512.624 chunk / 1.712 batch. Sáu ZIP phải có `plan.json`, metadata nguồn và hợp đủ batch, đúng hash. Không thay bằng semantic store 9.036.279 chunk. Mỗi payload batch được kiểm tra checksum trước khi đọc; evidence đối chiếu quan hệ document/version và checksum văn bản.

## 2. Bắt đầu bằng pilot

Mở [notebook retrieval](../colab/stage9_corpus_retrieval_colab.ipynb) ở bốn phiên, cùng tài khoản/thư mục Drive hoặc thư mục thật sự dùng chung:

| Phiên | ROLE | Runtime |
|---|---|---|
| A | `bm25` | CPU được |
| B | `bge` | GPU |
| C | `qwen` | GPU |
| D | `e5` | GPU |

Có thể chạy tuần tự nếu ít GPU. Đây là bốn nhánh độc lập, không phải sparse lọc xong mới đến dense.

Mặc định chung cho cả bốn phiên:

```python
QUERY_LIMIT = 20
SOURCE_BATCH_LIMIT = 4
TOP_K_PER_BRANCH = 500
RERANK_TOP_K = 400
SHARD_COUNT = 1
SHARD_INDEX = 0
STOP_AFTER_BATCHES = 2
```

Chạy các cell theo thứ tự. Sau hai batch để kiểm tra thời gian, đặt `STOP_AFTER_BATCHES = None`, chạy lại **riêng cell worker** để hoàn tất 4 batch của pilot. Không chạy lại cell cấu hình vì nó đặt lại giới hạn thời gian.

Giữ cùng `CAMPAIGN`, QUERY_LIMIT, SOURCE_BATCH_LIMIT và các thông số pool ở mọi phiên. Mỗi notebook in đường dẫn `RUN`; bốn nhánh phải có cùng đường dẫn này. Đổi model/code/phạm vi sẽ tạo fingerprint khác. Không sửa JSON checkpoint để ép tương thích.

Pilot chỉ dùng 4 batch đầu, không đại diện chất lượng tìm kiếm toàn corpus. Mục tiêu là kiểm tra dữ liệu, tải model, GPU, checkpoint và luồng xuất kết quả.

## 3. Hợp nhất và rerank

Khi bốn nhánh retrieval báo `complete`, mở [notebook rerank](../colab/stage9_corpus_rerank_colab.ipynb).

1. Đặt `PLAN_DIRECTORY` bằng đường dẫn RUN đã in nếu có nhiều run. Mặc định chỉ tự chọn khi có đúng một plan.
2. Chạy `MODE = 'prepare'` trên **một phiên**. Bước này kiểm tra đủ mọi nhánh/shard, hợp nhất thứ hạng RRF, đọc lại văn bản từ nguồn và tạo packet cố định.
3. Khi prepare hoàn tất, mở hai phiên GPU: `MODE = 'rerank'`, lần lượt `ROLE = 'qwen'` và `ROLE = 'bge'`.
4. Mặc định chấm 5 query mới/phiên. Sau đó đặt `STOP_AFTER_QUERIES = None`, chạy lại riêng cell worker.

Không chạy lại prepare trong khi reranker đang ghi điểm. Hai reranker dùng chính xác cùng packet, chấm `retrieval_text` có context từ corpus. Evidence xuất là `chunk_text` nguyên văn. Điểm gắn với hash packet, query, model, code và plan; file sai identity sẽ bị từ chối.

`RUN/prepared/RETRIEVAL_DIAGNOSTICS.json` ghi theo từng query: số candidate của từng nhánh, số candidate chỉ nhánh đó tìm được, kích thước hợp và số candidate của nhánh còn trong pool rerank. Đây là số đo độ đa dạng candidate, chưa phải recall theo GT.

## 4. Xuất kết quả

Mở [notebook export](../colab/stage9_corpus_export_colab.ipynb), chọn cùng PLAN_DIRECTORY và chạy các cell.

- Pilot: xuất `PILOT_PREDICTIONS.json`, `EVIDENCE_PROVENANCE.jsonl` và `REPORT.json`. **Không xuất submission.zip**.
- Full: kiểm tra links chính thức, xuất `submission.json`, `submission.zip`, provenance và báo cáo hash. Notebook chỉ tải file về khi bạn chạy cell download; không tự nộp lên website.

Chọn tối đa 120 document, 120 chunk/query, 6 chunk/document; giữ một derived version/document, loại exact duplicate cùng document và overlap IoU nguồn ≥ 0,8 trong cùng vùng. Document được xếp theo chunk có thứ hạng gộp cao nhất. Giới hạn chunk toàn query có thể khiến một số document dự đoán không có evidence.

## 5. Chuyển sang toàn bộ corpus

Trong **tất cả các phiên retrieval**, đổi:

```python
QUERY_LIMIT = None
SOURCE_BATCH_LIMIT = None
STOP_AFTER_BATCHES = None
```

Đường dẫn RUN mới được tạo; các output pilot không bị trộn vào run đầy đủ. Sau retrieval, chọn đường dẫn RUN **mới** trong notebook rerank/export. Full phải có đủ 1.200 query và 1.712 batch của cả bốn nhánh trước prepare; cả hai reranker phải hoàn tất trước export.

### Chia thêm GPU

Dense worker chia theo source batch:

```python
ROLE = 'qwen'
SHARD_COUNT = 2
SHARD_INDEX = 0  # phiên kia = 1
```

BGE/E5 cũng có thể chia như vậy. BM25 bắt buộc một worker vì dùng thống kê IDF toàn chỉ mục; không trộn điểm BM25 của những chỉ mục con khác nhau.

Rerank chia theo query bằng cùng SHARD_COUNT/SHARD_INDEX, riêng cho từng role. Không để hai phiên cùng role xử lý trùng query. Nếu đổi cách chia dense khi đã có output, dùng CAMPAIGN mới để tránh trộn shard counts; collector sẽ từ chối shard trùng hoặc thiếu.

## 6. Dung lượng, checkpoint và các giới hạn

- Embedding được chuẩn hóa và tìm bằng tích vô hướng float32, quét từng batch. Đây là exact search trên các vector tạo ra, chưa phải ANN index; không cần nạp toàn bộ vector corpus vào RAM cùng lúc.
- Với 5.512.624 vector 1.024 chiều, lưu float32 khoảng **22,6 GB/model**, ba nhánh khoảng **67,7 GB** chưa kể candidate, packet, weights và BM25. Pilot nhỏ hơn nhiều.
- `SAVE_EMBEDDINGS=True`: lưu `.npy` theo batch và receipt checksum, có thể tái sử dụng khi chạy tiếp cùng plan. Đây là vector blocks, chưa có FAISS/HNSW index phục vụ nhiều lượt query.
- `SAVE_EMBEDDINGS=False`: vẫn encode corpus nhưng không giữ vector sau batch; giữ top candidate và cursor để tiếp tục. Tham số này thuộc identity; không đổi giữa chừng trong cùng nhánh/shard.
- Dense checkpoint top candidate được ghi sau mỗi batch. Với full 1.200 query, ghi Drive có thể chậm và tạo nhiều I/O; chia shard giảm thời gian mỗi worker nhưng không loại chi phí quét corpus/backup. Chạy pilot để đo trước khi mở rộng.
- BM25 dùng SQLite FTS5, phân từ unicode cho Việt/Anh, thêm unigram/bigram chữ CJK cho Trung. Đây là baseline lexical đơn giản, chưa có tách từ chuyên ngành/word segmentation tiếng Việt. Query BM25 dùng tối đa 128 term khác nhau theo thứ tự xuất hiện; query quá dài có thể mất term ở cuối, cần thử riêng chính sách chọn term nếu nhánh này yếu.
- BM25 commit từng batch vào ổ local; backup database lên Drive ở cuối mỗi lần cell trả về. Nếu mất runtime đột ngột giữa cell, các batch sau lần backup Drive gần nhất phải làm lại. Có thể chạy từng nhóm batch bằng STOP_AFTER_BATCHES để backup thường xuyên, đổi lại thời gian copy database lớn.
- E5 dùng prefix `query:` / `passage:` và mean pooling; BGE dùng CLS; Qwen dùng instruction cho query và last-token pooling. Token dài được chia window overlap 64, lấy trung bình có trọng số và chuẩn hóa lại; đây là policy thử nghiệm, khác với encode chỉ một đoạn bị truncate.
- Reranker giới hạn 4.096 token/cặp, vượt giới hạn sẽ dừng rõ lỗi, không cắt ngầm. Chưa tích hợp window rerank hoặc tách đoạn evidence.
- Chưa thực hiện query decomposition, dịch query, tạo GT, huấn luyện hoặc tối ưu theo nhãn. Chưa đo LCS hai chiều hoặc độ liên quan chính thức. Không có cam kết tăng điểm.
- Không thay đổi frozen S09 producer, chỉ mục cũ hoặc notebook cũ.

## 7. Model đã khóa

| Vai trò | Model | Revision |
|---|---|---|
| Dense BGE | BAAI/bge-m3 | `5617a9f61b028005a4858fdac845db406aefb181` |
| Dense Qwen | Qwen/Qwen3-Embedding-0.6B | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| Dense E5 | intfloat/multilingual-e5-large | `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` |
| Rerank Qwen | Qwen/Qwen3-Reranker-0.6B | `6e9e69830b95c52b5fd889b7690dda3329508de3` |
| Rerank BGE | BAAI/bge-reranker-v2-m3 | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |

Metadata công khai được kiểm tra ngày 02/11/2026. Các revision embedding trên có ngày sửa trước 01/08/2026. BGE-M3 revision này chỉ có `pytorch_model.bin`, nên loader chọn đúng định dạng đó; E5 và Qwen dùng safetensors. Dùng PyTorch từ Colab hiện hành; Transformers yêu cầu PyTorch ≥2.6 khi đọc `.bin` qua torch.load.

Tài liệu model gốc: [BGE-M3](https://huggingface.co/BAAI/bge-m3), [Qwen embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [E5](https://huggingface.co/intfloat/multilingual-e5-large).

## Kiểm chứng đã thực hiện

Kiểm thử offline dùng source gzip thật của fixture, SQLite FTS5 thật, matrix search CPU và scorer/encoder giả để kiểm tra luồng. Bao gồm resume, chia shard, từ chối coverage thiếu/trùng, hash nguồn/packet sai, policy chọn evidence và chặn export pilot thành submission. Kiểm tra notebook parse được và nhúng đúng source hỗ trợ.

**Chưa chạy pretrained weights trên GPU Colab hoặc benchmark toàn corpus.** Pilot của bạn là bước xác nhận vận hành tiếp theo; chất lượng chỉ xác định được qua đánh giá phù hợp hoặc điểm BTC.
