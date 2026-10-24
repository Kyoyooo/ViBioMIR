# ViBioMIR — tổng hợp bài toán và khám phá dữ liệu

Đặc tả bài toán: [docs/BAI_TOAN_VIBIOMIR.md](docs/BAI_TOAN_VIBIOMIR.md).
Báo cáo của lượt phân tích dữ liệu hiện tại: [reports/eda/REPORT.md](reports/eda/REPORT.md).
Các bước EDA phân tích dữ liệu cục bộ, không gọi mô hình, không huấn luyện và
không tạo submission. `exploration.download_official` là bước tải dữ liệu riêng,
cần kết nối Hugging Face.

## Cài đặt và chạy

Python 3.10 trở lên. Nếu môi trường chưa có PyArrow:

```bash
python -m pip install -r requirements-eda.txt
```

Chạy từ thư mục gốc dự án, với **thư mục output mới**:

```bash
python -m exploration --data-dir Data --output-dir reports/eda
```

Đọc kết quả ở `reports/eda/REPORT.md`. Đầu ra có JSON tổng hợp, CSV và ba biểu
đồ SVG; `metrics.sqlite3` chứa chỉ số từng dòng cùng ID/URL/đường dẫn shard,
không sao chép toàn bộ nội dung văn bản/HTML. CSV dùng UTF-8 BOM để mở bằng Excel.

Chạy thử hai shard đầu theo thứ tự đường dẫn (đây **không** phải mẫu ngẫu nhiên
hay mẫu đại diện):

```bash
python -m exploration --output-dir reports/smoke --max-files 2 --inspect-blocks
```

Kiểm tra thêm cấu trúc `blocks` trên toàn corpus:

```bash
python -m exploration --output-dir reports/eda_blocks --inspect-blocks
```

Tùy chọn `--batch-size 512` giới hạn số dòng mỗi lô, không giới hạn byte; một dòng
rất dài vẫn có thể chiếm nhiều RAM. Mã đọc chọn cột và không đọc `html_trim`.
Phân tích đầy đủ có thể mất vài phút hoặc lâu hơn tùy máy; cần thêm dung lượng
cho SQLite/index và file tạm. Các bước tổng hợp dùng SQLite trên đĩa thay vì giữ
toàn bộ corpus trong RAM.

## Các bước độc lập

```bash
python -m exploration --stage inventory --output-dir reports/inventory
python -m exploration --stage corpus --output-dir reports/corpus
python -m exploration --stage references --output-dir reports/corpus
python -m exploration --stage report --output-dir reports/corpus
```

`corpus` và `all` từ chối ghi đè `metrics.sqlite3` đã có để tránh trộn các lần
chạy. `inventory`, `references`, `report` có thể cập nhật các báo cáo tương ứng
trong output đã có. Khi so sánh kết quả giữa các lần chạy, dùng thư mục riêng.
`run_config.json` ghi tham số cho lệnh `all`.

## Dữ liệu chính thức từ Hugging Face

Đã bổ sung `Data/official/ViBioMIR/` gồm `query.parquet`,
`links_corpus_vi_en_zh.parquet`, dataset card `README.md` và `provenance.json`.
Nguồn: [AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR),
revision `71be0e1239a19dfd7a717e2bc13e3bec21bd0334`.
Manifest lưu URL theo commit, SHA-256, thời điểm tải, schema và số dòng.

Tải lại vào thư mục mới nếu cần (không tự ghi đè dữ liệu đã có):

```bash
python -m exploration.download_official --output-dir Data/official/ViBioMIR_copy
```

Khi có nhiều bản sao dữ liệu, phải chỉ định đường dẫn cho EDA để tránh nhầm tệp.
Dataset card công bố giấy phép CC BY-NC 4.0; chi tiết và trích dẫn ở
[tài liệu bài toán](docs/BAI_TOAN_VIBIOMIR.md#35-giấy-phép-và-trích-dẫn).

EDA tự tìm `query.parquet` cùng `links_corpus_vi_en_zh.parquet` hoặc tên cũ
`links_corpus.parquet` bên dưới `Data/`. Nếu chưa có, báo cáo ghi rõ thiếu dữ
liệu; EDA không tự tải. Cập nhật báo cáo đã có, dùng lại metrics của lượt quét
corpus trước để tránh đọc lại toàn bộ văn bản:

```bash
python -m exploration --stage references --output-dir reports/eda \
  --query-file Data/official/ViBioMIR/query.parquet \
  --links-file Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet
python -m exploration --stage report --output-dir reports/eda
```

Phần truy vấn kiểm tra kiểu ID, ID trùng, nội dung rỗng, câu trùng sau chuẩn hóa,
độ dài và số lượng 1.200 ID. Phần links kiểm tra ID/URL và đối chiếu với corpus
đã quét: ID có shard, ID có text HTTP 2xx không rỗng, ID ngoài danh sách chính
thức, ví dụ ID còn thiếu và các ID có URL nguồn không khớp nguyên văn. Có thêm
`official_coverage_by_domain.csv` thống kê theo ID duy nhất từng tên miền,
`official_url_mismatch_examples.csv` để kiểm tra lại ánh xạ ID–URL. Chỉ số này là độ phủ thu thập, **không phải Recall
truy hồi**. Cần nhãn liên quan để đánh giá Precision/Recall/F2.

## Cấu trúc code

| Tệp | Nội dung |
| --- | --- |
| `exploration/__main__.py` | CLI và điều phối các bước |
| `exploration/common.py` | Đọc theo lô, chuẩn hóa văn bản, IO, heuristic ngôn ngữ |
| `exploration/inventory.py` | Inventory/schema và trạng thái crawl từ SQLite |
| `exploration/corpus.py` | Chỉ số từng dòng, chất lượng, trùng lặp, tổng hợp và đối chiếu crawl |
| `exploration/references.py` | EDA truy vấn, links và độ phủ ID chính thức khi có |
| `exploration/download_official.py` | Tải hai Parquet và dataset card theo commit cố định, lưu provenance |
| `exploration/report.py` | Báo cáo Markdown và biểu đồ SVG |

## Đọc kết quả đúng phạm vi

- A/B/C chỉ là nhãn nhóm thư mục, không suy ra train/dev/test hoặc public/private.
- Phân bố tính trên **dòng đã crawl**; ID có thể lặp do retry. Các bảng cũng cung
  cấp số ID khác nhau. Hash nội dung bỏ qua text rỗng và chuẩn hóa theo đặc tả.
- Shard chỉ có metadata crawl vẫn được đọc và gắn cờ `missing_text_column`;
  không bị bỏ qua hay tự coi là nội dung thu thập thành công.
- `latin_undetermined` không phải nhãn tiếng Anh; `cjk_likely_zh` chưa xác nhận
  tiếng Trung. Nhận diện ngôn ngữ hiện tại chỉ là heuristic trên tiền tố văn bản.
- Độ dài theo khoảng trắng không phải token của embedding hay tokenizer bộ chấm;
  đặc biệt không dùng làm cơ sở so sánh độ dài ngôn ngữ Trung với Việt/Anh.
- HTTP 2xx có thể chứa trang chặn, menu hoặc nội dung không liên quan. Cờ chất
  lượng là gợi ý kiểm tra, không tự xóa/lọc dữ liệu.
- `quality_examples.csv` tối đa 20 dòng mỗi cờ; CSV trùng tối đa 1.000 nhóm; JSON
  tổng hợp đếm toàn bộ phạm vi đã quét. ID/URL gốc vẫn có trong SQLite metrics.
- Inventory ghi lỗi đọc; corpus bỏ qua toàn bộ shard lỗi và đánh dấu `partial`.
  `--max-files` cũng đánh dấu `partial`; số liệu từ lần chạy này không đại diện
  toàn bộ corpus. Báo cáo JSON giữ chi tiết lỗi.
- `last_run_*.json` chỉ là log lần chạy cuối; số crawl tổng hợp lấy từ SQLite.
- Dừng crawler trước khi chạy: SQLite nguồn được mở read-only/immutable để tránh
  sinh sidecar; dữ liệu đang ghi hoặc còn WAL chưa checkpoint có thể chưa được
  phản ánh. EDA không sửa các tệp nguồn.

Ví dụ kiểm tra lại các ID có nhiều bản ghi bằng CLI SQLite nếu đã cài:

```bash
sqlite3 reports/eda/metrics.sqlite3 \
  'SELECT doc_id, COUNT(*) AS n FROM documents GROUP BY doc_id HAVING n > 1 LIMIT 20;'
```

Các kiểm tra nhỏ cho chuẩn hóa, thống kê, đối chiếu crawl và dữ liệu chính thức:

```bash
python -m unittest discover -s tests -v
```
