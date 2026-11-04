# Model, checkpoint và cấu hình inference

## 1. Danh mục và phạm vi sử dụng

| Vai trò | Model | Revision khóa | Phạm vi |
|---|---|---|---|
| Tokenizer/chunking và dense baseline | BAAI/bge-m3 | 5617a9f61b028005a4858fdac845db406aefb181 | Structural + Dense V1 |
| Reranker baseline | BAAI/bge-reranker-v2-m3 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e | S09, dense recovery, fallback BGE |
| Reranker Qwen | Qwen/Qwen3-Reranker-0.6B | 6e9e69830b95c52b5fd889b7690dda3329508de3 | Chấm lại evidence hoặc fallback Qwen nếu route chọn |
| Dense Qwen | Qwen/Qwen3-Embedding-0.6B | 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 | Retrieval đa nhánh thử nghiệm, chưa hoàn tất theo log |
| Dense E5 | intfloat/multilingual-e5-large | 3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3 | Retrieval đa nhánh thử nghiệm, chưa hoàn tất theo log |

Qwen3-Reranker-4B tại revision `22e683669bc0f0bd69640a1354a6d0aebcfeede5` được hỗ trợ làm arbiter tùy chọn trong notebook multimodel; không mặc định coi là đã chạy hoặc là thành phần bài private. mBERT revision `3f076fdb1ab68d5b2880cb87a0886f315b8146f8` có trong code control/silver, không phải encoder Dense V1 được mô tả.

Không fine-tune các neural checkpoint này trong các route bàn giao. Giới hạn parameter và revision được kiểm tra trong loader/registry. Ngày model/revision lưu trong policy; danh sách trên mô tả đúng checkpoint đã khóa, không dùng nhánh main tự cập nhật. Các model nêu chính có revision trước mốc 01/08/2026 theo metadata đã kiểm tra trong quá trình phát triển; BTC có thể kiểm tra lịch sử public checkpoint.

## 2. Link tải chính thức

- [BGE-M3 revision dùng cho baseline](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181).
- [BGE reranker revision dùng cho baseline](https://huggingface.co/BAAI/bge-reranker-v2-m3/tree/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e).
- [Qwen reranker 0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/6e9e69830b95c52b5fd889b7690dda3329508de3).
- [Qwen embedding 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3).
- [Multilingual E5 large](https://huggingface.co/intfloat/multilingual-e5-large/tree/3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3).
- [Qwen arbiter 4B tùy chọn](https://huggingface.co/Qwen/Qwen3-Reranker-4B/tree/22e683669bc0f0bd69640a1354a6d0aebcfeede5).
- [mBERT control](https://huggingface.co/google-bert/bert-base-multilingual-cased/tree/3f076fdb1ab68d5b2880cb87a0886f315b8146f8).

Không cần đính kèm weights vào ZIP mã nguồn nếu BTC tải được các đường dẫn công khai này. Không có API key đóng gói trong hồ sơ.

## 3. Cách tải và sử dụng

Ví dụ tải nguyên checkpoint, không đổi revision:

```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='BAAI/bge-m3',
    revision='5617a9f61b028005a4858fdac845db406aefb181',
    local_dir='local_models/bge_m3'
)
```

Pipeline dùng AutoTokenizer/AutoModel cho embedding, AutoModelForSequenceClassification cho BGE reranker và AutoModelForCausalLM cho Qwen reranker. Loader không bật trust_remote_code. Phiên bản BGE-M3 chính thức này có pytorch_model.bin; PyTorch cần đủ mới cho chính sách đọc weights của Transformers. Notebook cài đúng các dependencies và giữ PyTorch phù hợp runtime CUDA.

### Dense baseline Stage 8

CLS, dimension 1024, window max512/overlap32, FP16 inference/storage, L2 normalization và token-count-weighted aggregation. Không thêm query instruction hoặc ICD prefix. Đây là policy của vibiomir/dense_models.py và configs/dense_stage8.json.

### BGE rerank S09 và dense recovery

Chấm logit một lớp cho cặp query/passage, FP16. Frozen policy S09 dùng max_pair_tokens=1024, query_window_tokens=384, overlap32, gộp mean theo query windows sau max theo passage windows. Các tham số thực đọc từ prepared identity; không thay bằng max4096 của notebook thử nghiệm mới.

### BGE/Qwen trên submission và partial fallback

Hai loại notebook này dùng policy riêng tối đa4096 token/cặp, từ chối input vượt budget thay vì cắt ngầm. Fallback chấm chunk_text thống nhất cho evidence cũ/mới; corpus rerank đầy đủ chấm retrieval_text. Vì input khác, không reuse score chéo các route chỉ vì cùng model/chunk ID.

Qwen dùng instruction cố định:

```text
Given a web search query, retrieve relevant passages that answer the query
```

Mẫu body:

```text
<Instruct>: {instruction}
<Query>: {query}
<Document>: {passage}
```

Body được ghép với system/prefix/suffix trong scripts/qwen_submission_trial.py; lấy logit yes trừ no ở vị trí cuối. Không sampling, không sinh câu trả lời và không viết lại evidence. Để tái hiện đúng token template, dùng nguyên code thay vì tự tạo chat prompt tương đương.

### Embedding đa nhánh thử nghiệm

Qwen dùng instruction query và last-token pooling; E5 dùng query:/passage: và mean pooling; BGE dùng CLS. Bản tối ưu mới chia window overlap64 và chọn BF16 chỉ khi native, nếu không dùng FP16. Các log cũ Qwen BF16/eager trên T4 thuộc cấu hình trước sửa; không nhầm policy đó với Dense V1 baseline hoặc gán nó cho toàn bộ artifact đã báo cáo.

## 4. Registry và khả năng tái hiện

Lưu model ID, revision tokenizer/weights, dtype, pooling/window, version torch/transformers/tokenizers, GPU, số parameter và hash code/config trong registry/plan. Checkpoint sau thay đổi policy phải tạo run mới. Khi chỉ cần kiểm chứng file bài nộp, khôi phục đúng packet/score đã bàn giao và xác minh hash; không cần inference lại toàn bộ model.
