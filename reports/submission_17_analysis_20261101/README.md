# Phân tích submission_17.zip

Đọc toàn bộ `A:\ViBioMIR\submission_17.zip`, đối chiếu với `query/query.parquet` ngày 01/11/2026.
File gốc không bị chỉnh sửa. Đây là audit cấu trúc, phân bố và nội dung dạng mẫu;
không phải chấm relevance hoặc evaluator LCS của BTC.

## 1. Kết luận sử dụng

**ZIP có thể dùng cho thí nghiệm chọn evidence bên trong các chunks đã xuất.**
Không đủ để tái hiện truy hồi/rerank top 400, bổ sung evidence cho các docs chưa có chunk,
hoặc xác thực độc lập rằng văn bản trùng corpus gốc. Muốn làm các việc đó cần packet/corpus/worker tương ứng.

ZIP chỉ có một file `submission.json`, danh sách 1.200 kết quả, mỗi kết quả gồm:

```text
id
relevant_docs: [doc_id, ...]
relevant_chunks: [{doc_id, chunk_text, chunk_order}, ...]
```

Không có `chunk_id`, `derived_version_id`, `region_id`, source offsets, query text,
retrieval text có title/context, model metadata hoặc điểm rerank. Query có thể nối theo ID
từ file query chính thức. `chunk_order` là số thứ tự xuất trong query, không phải thứ tự
đoạn trong tài liệu gốc; không ghép chunks theo trường này để dựng lại tài liệu.

Tên file không chứng minh điểm chấm. Bản này được người dùng cung cấp trong ngữ cảnh
baseline 0.1706, nhưng chưa có biên nhận chấm gắn với hash ZIP để xác minh độc lập.

## 2. Kiểm tra kỹ thuật

| Mục | Kết quả |
|---|---|
| ZIP | 50.964.249 bytes, khoảng 48,60 MiB |
| JSON giải nén | 192.969.487 bytes, khoảng 184,03 MiB |
| Query coverage | Đúng ID và thứ tự 1–1.200 |
| Schema | Các trường đúng với cấu trúc xuất hiện tại |
| Doc IDs | Kiểu int, không trùng trong một query |
| Chunk parent | Mỗi doc_id của chunk nằm trong relevant_docs cùng query |
| Evidence rỗng | Không có |
| Output order | chunk_order liên tục 0–119 ở mọi query |
| ZIP CRC và JSON parse | Đọc thành công |

Đây là phạm vi của `technical_validation: passed` trong báo cáo audit này.
Chưa kiểm tra doc IDs thuộc allowlist corpus chính thức, chưa đối chiếu source slice
và chưa kết luận nội dung trả lời đúng câu hỏi.

Hash ZIP:
`0963641cb422e4223d6fc4b0c22eb09938f8b03c8227053cc7a398bf33e25dbd`.

Hash submission JSON:
`24e4e6490afc53f2a08af229b54c354a7d47436b96f00f92efb1ebf6ba671e32`.

Hash query:
`ce13508b893bbd6b9169679d540630bc37eb5950cad08b4d5e0e5d7063670c08`.

## 3. Số lượng docs và độ phủ evidence

Các số dưới đây đếm **lượt query–document**, cùng doc xuất hiện ở hai query được đếm hai lần.

| Chỉ số | Kết quả |
|---|---:|
| Tổng lượt docs | 143.961 |
| Docs phân biệt toàn ZIP | 89.516 |
| Docs/query | Trung bình 119,97; min 94; max 120 |
| Tổng chunks | 144.000 |
| Chunks/query | Đúng 120 ở mọi query |
| Docs có evidence/query | Trung bình 86,48 |
| Docs không có evidence/query | Trung bình 33,49 |
| Tổng lượt docs không có chunk | 40.188 / 143.961 = **27,92%** |
| Query có ít nhất một doc không có chunk | 1.197 / 1.200 |

Ví dụ query 529: 120 docs, 120 chunks nhưng chunks chỉ nằm trong **37 docs**;
**83 docs** còn lại không có evidence trong submission.
Query 1: 120 docs, 120 chunks, evidence thuộc 88 docs, còn 32 docs không có chunk.

Điều này phù hợp cơ chế chọn docs và chunks với hai ngân sách riêng. Không tự nó là
lỗi format hay bằng chứng 40.188 lượt doc đó sai. Nó cho thấy ngân sách evidence chưa
phủ hết danh sách docs và cần xem mức tập trung có tốt cho relevance hay không.

**Giới hạn quan trọng của ZIP:** không thể thực hiện đầy đủ phương án “mỗi doc một chunk”
cho toàn bộ relevant_docs, vì không có văn bản cho các docs chưa có evidence.
Giảm cap mỗi doc từ 6 xuống 2 trên ZIP chỉ có thể chọn lại/xóa các chunks đã có;
không tự bổ sung được các chunks từng bị loại khỏi pool top 400.

Phân bố số chunk trên một cặp query–doc có evidence:

| Số chunks/doc | Số cặp query–doc |
|---|---:|
| 1 | 80.046 |
| 2 | 14.824 |
| 3 | 4.746 |
| 4 | 1.928 |
| 5 | 1.018 |
| 6 | 1.211 |

## 4. Độ dài và ngôn ngữ evidence

| Chỉ số | Số ký tự/chunk |
|---|---:|
| Min | 2 |
| Trung vị | 858 |
| Trung bình | 879,59 |
| P90 | 1.479 |
| P95 | 1.558 |
| Max | 2.059 |

63.676 chunks dài trên 1.000 ký tự, khoảng **44,22%**. Đây là nhóm đáng rà soát ranh giới
evidence, chưa chứng minh chúng quá dài so với đáp án. Chưa có gold chunk lengths hoặc LCS
để xác định tỷ lệ trượt. Không cắt theo một số ký tự cố định chỉ từ bảng này.

| Dấu hiệu ngôn ngữ | Số chunks | Tỷ lệ |
|---|---:|---:|
| Có dấu tiếng Việt, không phát hiện CJK | 124.630 | 86,55% |
| Có ký tự CJK | 19.312 | 13,41% |
| Chưa phân loại | 58 | 0,04% |

Đây là quy tắc ký tự, không phải bộ phân loại ngôn ngữ. Các mẫu CJK được đọc có tiếng Trung.
Không loại chúng chỉ vì query là tiếng Việt: nhiệm vụ có corpus đa ngữ và đoạn khác ngôn ngữ
có thể liên quan. Cần phân đoạn câu hỗ trợ dấu `。！？` khi tạo đoạn con; không dùng số
đơn vị khoảng trắng để so độ dài tiếng Việt và tiếng Trung.

## 5. Đoạn dạng nhãn và trùng lặp

Đã tìm thấy các evidence chỉ chứa một nhãn/tiêu đề chung. So khớp toàn văn sau trim/casefold:

| Nội dung | Số lượt chunk |
|---|---:|
| Tin liên quan | 85 |
| Có thể bạn quan tâm | 40 |
| Sức khỏe | 13 |
| Sống khỏe | 6 |
| Phòng mạch Online | 5 |
| Tổng | **149** |

Ví dụ query 9/doc 293887 có riêng “Tin liên quan” và “Có thể bạn quan tâm”.
Query 5/doc 43339 có chunk “Sức khỏe”. Đây là các mẫu evidence yếu cần review/lọc theo
quy tắc nội dung; không đồng nghĩa mọi tiêu đề ngắn đều vô ích.
149 lượt chỉ khoảng **0,10%** tổng chunks, nên lọc các mẫu này không đủ giải thích hoặc
giải quyết toàn bộ điểm chunk thấp. Danh sách nhãn hữu hạn chưa đo hết boilerplate.

Có 829 chunks dưới 30 ký tự và 120 chunks dưới 10 ký tự. Một số đoạn Trung ngắn có
nội dung câu hoàn chỉnh, do đó không dùng ngưỡng độ dài đơn thuần để loại tất cả.

Kết quả trùng lặp:

- Không có chunk trùng nguyên văn trong cùng doc ở cùng query.
- Có 3.166 lượt doc bổ sung có cùng văn bản với doc khác trong cùng query.
- Có 117 lượt chunk là chuỗi con nghiêm ngặt của chunk khác cùng doc/cùng query.
- Có 1.234 văn bản phân biệt xuất hiện ở nhiều doc trên toàn ZIP.

Không xóa trùng xuyên doc một cách mặc định: các doc_id khác nhau vẫn là những đối tượng
chấm riêng. Không có source offsets thì chưa đo được overlap IoU thực; phép kiểm tra
containment ở đây chỉ là quan hệ chuỗi, không chứng minh hai chunk trỏ cùng vị trí nguồn.

## 6. Có thể cải tiến gì chỉ với ZIP và query.parquet?

**Phạm vi khả thi:** giữ nguyên relevant_docs; tạo đoạn con nguyên văn trong từng chunk đã
xuất, chấm lại các ứng viên bằng query tương ứng, chọn evidence mới và xuất ZIP mới.

Thiết kế thí nghiệm nên có ba bản:

1. **S0:** giữ nguyên submission_17.zip để làm mốc.
2. **S1:** chấm lại nguyên các chunks hiện có với query + chunk_text, thử chọn lại evidence.
3. **S2:** cùng cách chấm như S1 nhưng bổ sung đoạn con theo câu/đoạn; giữ cả chunk gốc.

S1 quan trọng vì submission không có `retrieval_text` chứa title/context và điểm model cũ.
Chấm lại chunk_text có thể khác input BGE ban đầu; nếu chỉ so S0 với S2 sẽ gộp thay đổi input,
thay đổi điểm và thay đổi span vào một thí nghiệm.

Mỗi span mới lưu sidecar:

```text
query_id, doc_id
parent_submission_json_sha256
parent_chunk_order, parent_chunk_text_sha256
start/end trong chuỗi parent chunk
model/policy, score
```

Kiểm tra `parent_chunk_text[start:end] == new_chunk_text` trước export. Đây là xác thực
với submission gốc, không được ghi thành “đã đối chiếu corpus gốc”. Không dùng title model
sinh ra, không dịch evidence trả về và không nối các chunk dựa trên chunk_order.

Giới hạn số spans/chunk, chạy pilot trước: có 144.000 cặp query–chunk ban đầu; tạo nhiều
đoạn con có thể tăng số cặp GPU lên nhiều lần. Chưa có số giờ chạy đáng tin cậy trước pilot.

## 7. Điều chưa thể kết luận

- File này có thực sự là ZIP được chấm 0.1706 nếu chưa có scoring receipt gắn hash.
- Doc/chunk nào liên quan hoặc không liên quan theo đáp án BTC.
- Model nào tạo file chỉ từ nội dung JSON; việc dùng BGE/structural dựa trên lịch sử lần chạy.
- Độ dài evidence tối ưu hoặc mức tăng F2 kỳ vọng sau cắt đoạn.
- Có thể phục hồi top 400 ứng viên, scores, nguồn doc và ngữ cảnh liền kề từ submission.

## 8. Artifact phân tích

- [`SUMMARY.json`](SUMMARY.json): thống kê, ví dụ và giới hạn phép đo.
- [`QUERY_STATS.json`](QUERY_STATS.json): docs/chunks/độ phủ/độ dài theo từng query.
- [`DUPLICATE_REVIEW.json`](DUPLICATE_REVIEW.json): query có trùng nguyên văn cùng doc hoặc containment.
- Script: [`scripts/analyze_submission_zip.py`](../../scripts/analyze_submission_zip.py).

Tái lập từ root: `python scripts/analyze_submission_zip.py` trong môi trường có pyarrow.
Đã đối chiếu tổng số chunks theo nhóm ngôn ngữ, theo số chunks/doc và tổng docs chưa có evidence.
