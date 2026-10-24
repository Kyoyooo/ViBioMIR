# Báo cáo khám phá dữ liệu ViBioMIR

Thời điểm sinh (giờ Việt Nam): 2026-10-24T02:03:54+07:00

Phân tích dữ liệu cục bộ; không tải dữ liệu, không gọi mô hình hoặc API.

## Inventory

Nguồn: `/home/duyhoang/Downloads/AI_Guru/Data`.

| Chỉ số | Giá trị |
| --- | --- |
| Tệp Parquet | 1197 |
| Dung lượng Parquet (byte) | 2466230403 |
| Tổng dòng theo metadata (mọi Parquet) | 2326958 |
| Biến thể schema | 4 |
| Lỗi inventory | 0 |

Schema và lỗi chi tiết: [inventory.json](inventory.json); danh sách tệp: [files.csv](files.csv).

## Corpus

Đã quét toàn bộ shard corpus phát hiện được.

| Chỉ số | Giá trị |
| --- | --- |
| Shard đã chọn | 1195 |
| Shard phát hiện được | 1195 |
| Dòng đã đọc thành công | 1105160 |
| ID tài liệu khác nhau | 855745 |
| ID có nhiều nội dung khác nhau | 28480 |
| Lỗi đọc shard | 0 |

### Chất lượng (các cờ có thể chồng lấp)

| Dấu hiệu | Số dòng |
| --- | --- |
| missing_or_invalid_id | 0 |
| empty_text | 3444 |
| short_text | 19613 |
| missing_title | 3512 |
| invalid_url | 0 |
| non_2xx_status | 3058 |
| text_len_mismatch | 0 |
| html_in_text | 1391 |
| replacement_character | 84 |
| possible_block_page | 841 |
| invalid_blocks | 0 |
| missing_text_column | 853 |

`short_text` dùng số ký tự thô của văn bản không rỗng, ngưỡng 200. `text_len_mismatch` so sánh `text_len` với `len(text)` Python. Cờ HTML/trang chặn là heuristic, cần xem lại mẫu.

Ví dụ để kiểm tra lại bản gốc: [quality_examples.csv](quality_examples.csv).

### Phân bố độ dài

| Đơn vị | Min | P50 | P90 | P99 | Max | Trung bình |
| --- | --- | --- | --- | --- | --- | --- |
| Ký tự | 0 | 2492 | 6244 | 11693 | 336970 | 3089.7728057475842 |
| Đơn vị tách khoảng trắng | 0 | 503 | 1360 | 2533 | 66214 | 625.2311583843064 |

Các phân vị dùng phần tử ở vị trí floor((n−1)×q) trong dãy đã sắp xếp. Đơn vị tách khoảng trắng **không phải token mô hình/LCS**, và không dùng để so sánh lượng thông tin giữa tiếng Trung với tiếng Việt/Anh.

### Bản ghi trùng

| Khóa | Nhóm trùng | Dòng dư |
| --- | --- | --- |
| id | 248909 | 249415 |
| url | 248909 | 249415 |
| normalized_text_sha256 | 223088 | 225574 |

Dòng dư = tổng (kích thước nhóm − 1). Hash SHA-256 tính trên toàn bộ văn bản sau NFKC → HTML unescape → lowercase → gộp khoảng trắng. Bỏ qua nội dung rỗng khi đếm trùng nội dung. URL được so sánh nguyên văn, chưa chuẩn hóa URL. Đây là trùng chính xác sau chuẩn hóa, chưa phải near-duplicate. Các CSV nhóm trùng chỉ xuất tối đa 1.000 nhóm; tổng đếm trong JSON là đầy đủ cho phạm vi đã quét.

### Dấu hiệu ngôn ngữ

| Heuristic | Số dòng |
| --- | --- |
| cjk_likely_zh | 314350 |
| latin_undetermined | 422 |
| other_or_mixed | 3 |
| unknown | 3444 |
| vi_diacritics | 786941 |

Heuristic xem tối đa 4.000 ký tự đầu: tỷ lệ chữ CJK ≥20% → `cjk_likely_zh`; có dấu đặc trưng tiếng Việt → `vi_diacritics`; nhiều chữ Latin ASCII → `latin_undetermined`. CJK không khẳng định tiếng Trung; Latin không khẳng định tiếng Anh. Không có mô hình nhận diện ngôn ngữ, không dùng tên miền làm nhãn ngôn ngữ.

### Đối chiếu trạng thái crawl

| Nhóm | ID trạng thái | ID trạng thái thiếu shard | ID shard thiếu trạng thái | ID HTTP 2xx thiếu text 2xx không rỗng |
| --- | --- | --- | --- | --- |
| A | 147786 | 0 | 0 | 120 |
| B | 429143 | 0 | 0 | 16 |
| C | 527636 | 0 | 0 | 49 |

Đối chiếu ID trong cùng nhóm A/B/C. SQLite trạng thái không phải danh sách tài liệu chính thức; HTTP 2xx không tự chứng minh nội dung đúng. Nếu chỉ quét mẫu shard thì số thiếu shard chưa phản ánh corpus đầy đủ. Log `last_run` chỉ là lần chạy gần nhất.

### Cấu trúc khối nội dung

| Chỉ số | Giá trị |
| --- | --- |
| Dòng có blocks hợp lệ | 1101858 |
| Tổng khối | 34658787 |
| Số khối trung bình/dòng có blocks hợp lệ | 31.45485806701045 |
| Số khối tối đa | 8728 |

Khối nội dung từ crawler chưa mặc định là chunk truy hồi. Cấu trúc hợp lệ là danh sách các cặp chuỗi `[loại_khối, văn_bản]`; EDA không thay đổi văn bản khối.

![Top domains by corpus rows](top_domains.svg)

![HTTP status by corpus rows](status.svg)

![Text length in characters](lengths.svg)

## Truy vấn và danh sách tài liệu chính thức

Nguồn: [AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR); revision `71be0e1239a19dfd7a717e2bc13e3bec21bd0334`. Thời điểm tải: 2026-10-24T02:00:16+07:00. Manifest cục bộ: `/home/duyhoang/Downloads/AI_Guru/Data/official/ViBioMIR/provenance.json`.

### Truy vấn

| Chỉ số | Giá trị |
| --- | --- |
| file | /home/duyhoang/Downloads/AI_Guru/Data/official/ViBioMIR/query.parquet |
| rows | 1200 |
| unique_valid_ids | 1200 |
| expected_rows | 1200 |
| has_1200_unique_valid_ids | True |
| duplicate_id_groups | 0 |
| duplicate_normalized_query_groups | 0 |
| quality_flags | {} |
| mean_characters | 129.50583333333333 |
| median_characters_lower | 81 |
| max_characters | 1243 |
| whitespace_units | {"min": 5, "p50_lower": 18, "p95_lower": 84, "max": 281} |
| language_hints | {"vi_diacritics": 1200} |

### Danh sách tài liệu

| Chỉ số | Giá trị |
| --- | --- |
| file | /home/duyhoang/Downloads/AI_Guru/Data/official/ViBioMIR/links_corpus_vi_en_zh.parquet |
| rows | 1220598 |
| unique_valid_ids | 1220598 |
| invalid_ids | 0 |
| invalid_urls | 0 |
| domains | 94 |
| duplicate_id_extra_rows | 0 |
| official_ids_with_shard | 855745 |
| official_ids_with_nonempty_2xx_text | 852995 |
| official_ids_with_only_mismatched_source_urls | 0 |
| shard_ids_outside_official_corpus | 0 |
| official_ids_without_shard | 364853 |
| nonempty_2xx_id_coverage | 0.6988336864389422 |
| note | Coverage refers to the scanned shards; a partial run understates full coverage. Domain coverage counts unique IDs, using MIN(domain) for ambiguous duplicate official IDs. Source URL matching is exact; redirects/encoding differences need manual review. |

Độ phủ ID có ít nhất một bản text HTTP 2xx không rỗng: **69.88%**. Đây là độ phủ thu thập; chưa xác nhận văn bản đúng nội dung và không phải Recall truy hồi.

### Tên miền thiếu ID so với danh sách chính thức

| Tên miền | ID chính thức | ID chưa có shard | ID có text HTTP 2xx không rỗng |
| --- | --- | --- | --- |
| pubmed.ncbi.nlm.nih.gov | 240000 | 239957 | 0 |
| nhathuoclongchau.com.vn | 79598 | 79598 | 0 |
| ask.39.net | 32772 | 32772 | 0 |
| zysjonline.com | 6591 | 6548 | 0 |
| pmc-ecm-healthblog.beta.pharmacity.io | 5333 | 5292 | 0 |
| article.iiyi.com | 729 | 686 | 0 |

Tối đa 15 tên miền, xếp theo số ID thiếu. Toàn bộ thống kê: [official_coverage_by_domain.csv](official_coverage_by_domain.csv). Các URL nguồn không khớp nguyên văn: [official_url_mismatch_examples.csv](official_url_mismatch_examples.csv).

Độ dài truy vấn ở đây dùng `str.split()`, phân vị theo phần tử floor((n−1)×q). Dataset card công bố min–max số từ là 5–283; lượt phân tích tệp đang đọc cho 5–281 theo cách đếm trên. Chưa xác định cách tách từ của thống kê công bố; không thay đổi văn bản truy vấn.

## Giới hạn và bước tiếp theo

- Xem các cờ chất lượng theo domain trước khi quyết định lọc; mã EDA không loại/sửa bản ghi nguồn.
- Kiểm tra các ID còn thiếu và các nguồn chưa có text thành công trong thống kê độ phủ chính thức.
- Giữ ID gốc khi xử lý nội dung trùng giữa nhiều tài liệu.
- Chưa có nhãn tham chiếu nên không tính Precision/Recall/F2, không kết luận chất lượng truy hồi.
- Nếu bật `--inspect-blocks`, có thêm số khối và cờ JSON/structure lỗi; khối trống không được coi là JSON lỗi.

Dữ liệu chi tiết: [corpus_summary.json](corpus_summary.json), [references_summary.json](references_summary.json), [metrics.sqlite3](metrics.sqlite3).
