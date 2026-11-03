# Chấm Qwen và chọn evidence từ submission_17.zip

Hai notebook độc lập:

1. [`stage9_qwen_score_submission.ipynb`](../colab/stage9_qwen_score_submission.ipynb): GPU,
   chấm lại các chunk đã xuất, có tùy chọn tạo/chấm đoạn con.
2. [`stage9_qwen_select_submission.ipynb`](../colab/stage9_qwen_select_submission.ipynb): CPU,
   đọc điểm hoàn tất, tạo các cấu hình chọn evidence và đóng gói ZIP.

Mã Python được nhúng trong cả hai file, không cần upload script hoặc giải nén các bundle cũ.
Đây là lần chạy mới trên 120 chunks đã xuất/query, không phải toàn bộ top 400 của notebook
`stage9_expand_pool_colab.ipynb`. Không sửa code/metadata của baseline BGE.

## 1. Đầu vào đã biết

Upload hai file sau vào cùng thư mục Drive:

```text
/content/drive/MyDrive/ViBioMIR/
    submission_17.zip
    query.parquet
```

File query local là `A:\ViBioMIR\query\query.parquet`; trên Drive dùng tên `query.parquet`.
Nếu lưu ở nơi khác, chỉ sửa hai biến đường dẫn trong cell setup của mỗi notebook.

Hash ZIP được khóa:
`0963641cb422e4223d6fc4b0c22eb09938f8b03c8227053cc7a398bf33e25dbd`.

Hash query được khóa:
`ce13508b893bbd6b9169679d540630bc37eb5950cad08b4d5e0e5d7063670c08`.

Điểm chấm 0.1706 do người dùng báo cáo; không có scoring receipt để notebook xác nhận
độc lập quan hệ điểm–ZIP. Bản gốc được giữ làm S0, không đổi danh sách docs.

## 2. Model và cách chấm

- Model: `Qwen/Qwen3-Reranker-0.6B`.
- Weights/tokenizer revision: `6e9e69830b95c52b5fd889b7690dda3329508de3`.
- Metadata Hugging Face tại revision: ngày 09/06/2025, 595.776.512 tham số theo safetensors metadata.
- Runtime đếm tham số và kiểm tra ≤15B; revision được pin trước cutoff trong đặc tả project.
- Transformers 4.57.1, tokenizers 0.22.1; giữ torch có sẵn trên Colab.
- Template query/document theo ví dụ Transformers của Qwen, instruction tiếng Anh cố định.
- Điểm là `logit(yes) - logit(no)` tại vị trí cuối, không sinh câu trả lời và không phải
  xác suất relevance đã được hiệu chỉnh. Xếp hạng tương đương sigmoid của hiệu logits.
- FP16, SDPA; chỉ tính logits token cuối để tránh allocation sequence × vocabulary.
- Mặc định tối đa 4.096 tokens cho cả template/query/passage. Không cắt ngầm;
  nếu quá dài sẽ dừng và yêu cầu cấu hình run mới với ngân sách lớn hơn phù hợp context.
- Nếu CUDA OOM khi batch >1, giảm batch và thử lại; không bỏ passage. OOM ở batch 1 được báo lỗi.

Tham khảo chính thức:
[model revision](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B/tree/6e9e69830b95c52b5fd889b7690dda3329508de3),
[model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B),
[Qwen3 forward trong Transformers 4.57.1](https://github.com/huggingface/transformers/blob/v4.57.1/src/transformers/models/qwen3/modeling_qwen3.py).

Đây không phải khẳng định Qwen mạnh hơn BGE trên bộ query này. Chưa có điểm relevance mới.
Input chấm là query gốc + `chunk_text`; ZIP không có `retrieval_text` kèm title/context cũ,
nên so sánh với baseline còn có khác biệt về biểu diễn passage, không chỉ model.

## 3. Chạy notebook 1

Mở nguyên file `.ipynb` trong Colab GPU runtime mới, chạy lần lượt các cell.
Không dán JSON notebook vào cell và không trộn với các cell của notebook cũ.

Mặc định:

```python
INCLUDE_SPANS = False
BATCH_SIZE = 8
PADDED_TOKENS = 8192
STOP_AFTER_QUERIES = 5
```

`False` chấm 144.000 lượt chunk đã có. Trong một query, các passage có cùng văn bản
được tính điểm một lần và dùng lại cho các doc tương ứng; không xóa các doc đó.

Sau pilot, xem thời gian và log GPU; chạy một cell mới:

```python
STOP_AFTER_QUERIES = None
```

Rồi chạy lại **chỉ cell worker**. Không chạy lại cell configure vì nó đặt giới hạn về 5.
Khi output là `State: complete`, mở notebook 2. Trước đó không thể export kết quả đầy đủ.

Run directory nằm trong `ViBioMIR/qwen_submission_trial_v1/<identity_hash>/`.
Nhận dạng run gồm input hashes, model policy, span policy và hash code. Notebook 1 in
nguyên đường dẫn để sao chép sang notebook 2 khi cần.

### Tùy chọn thử thêm đoạn con

Để so sánh cách chọn evidence sau thí nghiệm model-only, đổi `INCLUDE_SPANS=True` trước
cell configure. Đây là run riêng. Không ghép điểm cũ bằng cách sửa hash.

Giữ chunk gốc; với parent ≥350 ký tự, tạo cửa sổ 1/2/3 câu, span tối thiểu 100 ký tự,
chọn tối đa 4 span bổ sung theo vị trí phân bố đều. Các số này là cấu hình thí nghiệm,
không phải độ dài tối ưu đã xác nhận. Chọn mẫu 4 span có thể bỏ lỡ đoạn tốt.

Tách ranh giới dựa vào newline và dấu câu Việt/CJK; bảo toàn dấu thập phân dạng `0.10`.
Đây là heuristic, có thể xử lý chưa tốt viết tắt, bảng hoặc câu đặc biệt.
Không gọi đây là semantic chunking. Không tạo embedding/index mới.

Số pairs có thể tăng đến nhiều lần; cell count in lượng thực trước khi tải model.
Run spans dùng thư mục/cache riêng và sẽ chấm lại originals; chưa có import điểm giữa hai run.

## 4. Resume và checkpoint

Mỗi query hoàn tất lưu `query-<id>.json` có checksum, binding query/candidates/model và scores.
Manifest chứa ID hoàn tất và hash các file điểm. SQLite cache cục bộ phục vụ các passage
trùng trong phiên; backup query hoàn tất được lưu trực tiếp trên Drive.

Sau reset runtime, chạy notebook 1 từ đầu với cùng file/config. Query đã hoàn tất được xác minh
và bỏ qua inference. Query dở có thể phải tính lại; không cần chấm lại toàn bộ query đã xong.
Không có packet gzip trung gian nên không phụ thuộc timestamp gzip như lỗi resume cũ.

Không chạy hai notebook scoring đồng thời vào cùng run. Không thay file support giữa
scoring và selection; dùng cặp notebook được tạo cùng phiên bản. Thay batch/token budget
để xử lý OOM không đổi model/span policy, nhưng sai khác số học GPU nhỏ vẫn có thể xảy ra.

## 5. Chạy notebook 2 và hiểu từng ZIP

CPU runtime đủ; không cần torch/model. Dùng cùng file input và chọn run hoàn tất.
Nếu có đúng một run complete khớp input, notebook tự chọn; nếu nhiều run, đặt
`RUN_DIRECTORY` bằng đường dẫn notebook 1 đã in ra.

| ZIP | Nội dung |
|---|---|
| `S0_baseline.zip` | JSON nguyên byte từ submission gốc, được đóng gói lại |
| `S1_originals_120_cap6.zip` | Chỉ chunk gốc, thứ tự theo Qwen, cap 120/6 |
| `S1_originals_80_cap2.zip` | Chỉ chunk gốc, tối đa 80/query, 2/doc |
| `S1_originals_120_cap2.zip` | Chỉ chunk gốc, tối đa 120/query, 2/doc |
| `S2_spans_120_cap6.zip` | Nếu đã chấm spans: chọn cả original/subspan, cap 120/6 |
| `S2_spans_80_cap2.zip` | Nếu đã chấm spans: chọn cả original/subspan, cap 80/2 |

**S1_originals_120_cap6 có thể không đổi tập evidence.** Bản gốc đã có 120/query và cap 6;
chấm lại rồi chọn tất cả chủ yếu đổi thứ tự. Báo cáo in `changed queries` để nhận ra điều này.
Các cap là trần; sau giới hạn/trùng/overlap có thể trả ít hơn, không ép bổ sung evidence yếu.

Mọi biến thể giữ **nguyên danh sách và thứ tự relevant_docs**. Điều này giúp tập trung
thử evidence; không thêm evidence cho các docs không có văn bản trong ZIP.

Loại trùng nguyên văn chỉ trong cùng doc. Overlap IoU chỉ tính giữa spans chung parent,
vì không có source coordinates để đo overlap giữa hai parent khác nhau. Không loại
trùng xuyên doc mặc định và không nối chunks theo `chunk_order`.

Xuất thêm:

- `REPORT.json`: cấu hình, hash ZIP, số chunks/spans, query thay đổi và giới hạn xác thực.
- `EVIDENCE_PROVENANCE.jsonl`: candidate ID, parent index/hash, start/end và quyết định chọn/loại.

Chọn một ZIP để download ở cell cuối. Không tự upload lên website, không tự gọi biến thể nào
là tốt nhất. Dùng cùng evaluator mới để so sánh và giữ baseline làm dự phòng.

## 6. Phạm vi xác thực

Notebook xác nhận ID query khớp, docs không đổi, điểm thuộc đúng input/model/policy,
và mỗi span là lát cắt nguyên văn của chunk trong submission gốc.

**Không xác thực độc lập corpus/source slice gốc**, không có official doc allowlist,
không biết gold relevance hoặc LCS. `REPORT.json` ghi rõ các giới hạn đó.
Phần này không thay thế validation đầy đủ khi có lại corpus/packets.

## 7. Kiểm thử và tái tạo

- Script hỗ trợ: [`qwen_submission_trial.py`](../scripts/qwen_submission_trial.py).
- Builder: [`build_qwen_submission_notebooks.py`](../scripts/build_qwen_submission_notebooks.py).
- Tests: [`test_qwen_submission_trial.py`](../tests/test_qwen_submission_trial.py).
- Tái tạo notebooks: `python scripts/build_qwen_submission_notebooks.py`.
- Kiểm thử offline: `python -m unittest discover -s tests -p test_qwen_submission_trial.py -v`.

Các kiểm thử fixture không phải suy luận weights Qwen thật và không chứng minh tăng điểm.
Notebook cần pilot GPU thực tế trước khi chạy toàn bộ corpus submission.
