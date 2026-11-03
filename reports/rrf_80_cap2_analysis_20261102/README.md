# Phân tích RRF_80_cap2.zip

Đã kiểm tra ZIP local ngày 02/11/2026 và so sánh trực tiếp với `submission_17.zip`. Đây là phân tích cấu trúc và thay đổi evidence, chưa phải đánh giá độ liên quan.

## Kết quả chính

| Chỉ số | submission_17 | RRF_80_cap2 |
|---|---:|---:|
| Query | 1.200 | 1.200 |
| Tổng lượt document theo query | 143.961 | 143.961 |
| Tổng chunk | 144.000 | 95.913 |
| Chunk trung bình/query | 120 | 79,93 |
| Giới hạn chunk/document | 6 | 2 |
| Document có evidence trung bình/query | 86,48 | 67,08 |
| Lượt document không có evidence | 40.188 (27,92%) | 63.461 (44,08%) |
| Độ dài chunk trung bình, ký tự | 879,59 | 891,85 |
| Độ dài chunk trung vị, ký tự | 858 | 882 |
| Chunk chỉ chứa một trong 5 nhãn chung đã kiểm tra | 149 | 0 |
| Lượt văn bản giống hệt thêm ở document khác trong cùng query | 3.166 | 1.969 |

## Những thay đổi đã xác minh

- Danh sách document, bao gồm thứ tự, giống baseline ở cả 1.200 query. Bản này không thực hiện retrieval mới.
- Cả 95.913 chunk đều khớp nguyên văn với chunk của cùng document và query trong baseline. Không có đoạn con mới hoặc văn bản không khớp.
- Đã bỏ 48.087/144.000 chunk, tương đương 33,39%. Việc bỏ này chưa chứng minh các chunk bị bỏ là không liên quan.
- Có 23.273 lượt document mất toàn bộ evidence so với baseline, nhưng vẫn còn trong danh sách document dự đoán. Không phải lỗi schema; đây là đánh đổi về độ phủ evidence.
- Có 1.184 query đủ 80 chunk, 16 query còn 61–79 chunk. Giới hạn là tối đa 80; pool hữu hạn và giới hạn 2/document có thể khiến không đủ 80. Không có query rỗng.
- Không có chunk trùng nguyên văn trong cùng document của cùng query. Vẫn có 63 lượt chunk bị chứa nguyên văn trong chunk dài hơn của cùng document; không thể suy ra IoU nguồn từ ZIP.
- Heuristic chữ viết: 83.656 chunk có dấu tiếng Việt, 12.245 chunk có chữ CJK, 12 chunk còn lại. Đây không phải bộ nhận diện ngôn ngữ đầy đủ hay nhãn chất lượng.

## Điều này có ý nghĩa gì?

Bản mới thử giả thuyết rằng bỏ bớt evidence theo thứ hạng gộp sẽ cải thiện precision đủ để bù phần recall có thể mất. Không có GT nên chưa xác định giả thuyết đúng hay sai. Metric F2 coi trọng recall, vì vậy không thể kết luận ít chunk hơn sẽ tốt hơn.

Nội dung chunk được giữ nguyên, và độ dài trung bình còn tăng nhẹ. Do đó bản này chưa thực hiện bước tách evidence ngắn hơn để thử nghiệm với điều kiện LCS hai chiều. Ngưỡng LCS không thể kiểm tra nếu không có chunk đáp án và tokenizer đánh giá.

Vì relevant_docs giống hệt baseline, các metric document dựa trên danh sách này phải giống khi chấm cùng bộ nhãn và cùng phiên bản evaluator. Nếu chúng khác, cần kiểm tra phiên bản chấm, đầu vào hoặc receipt; không quy cho thay đổi evidence ngay.

## Bước tiếp theo

1. Lấy điểm BTC của bản này và so với baseline trong cùng cơ chế chấm. Theo dõi riêng CHUNKS_PRECISION, CHUNKS_RECALL và CHUNKS_F2MACRO.
2. Phân tích `RRF_120_cap6.zip` từ cùng lần chạy để tách ảnh hưởng của cắt pool 80/cap2 khỏi việc đổi thứ hạng. Với pool chỉ có 120 chunk gốc, cấu hình này có thể giữ gần như toàn bộ evidence và chủ yếu đổi thứ tự; cần kiểm tra file thực tế.
3. Đọc REPORT.json, DISAGREEMENT_REVIEW.jsonl và EVIDENCE_PROVENANCE.jsonl của lần ghép để biết score, bất đồng và quyết định chọn. ZIP submission không chứa những thông tin đó, nên không thể kiểm chứng việc chạy đủ hai model chỉ từ tên ZIP.
4. Nếu muốn thử độ gọn của evidence, tạo một thí nghiệm riêng có INCLUDE_SPANS=True và chấm lại các đoạn con. Nếu muốn tìm chunk mới, cần chạy retrieval trên corpus thay vì chỉ dùng ZIP.

## Phạm vi kiểm tra

ZIP chỉ có `submission.json`, đọc qua kiểm tra CRC thành công. Schema, kiểu ID, query coverage/thứ tự theo `query/query.parquet`, chunk_order liên tiếp, văn bản không rỗng và quan hệ chunk–document đều đạt. Chưa đối chiếu corpus gốc hoặc allowlist ID chính thức.

SHA256 ZIP: `9d8eea0d32febe976d8980bf1ae28fabb570fe7017116a246a40dcb200e2ec8e`.

SHA256 submission.json: `45b376fa0bd299b6ed429796e9bd226afc0e6d949c819e79effa2757447f6ef0`.

Số liệu máy đọc: SUMMARY.json, QUERY_STATS.json, DUPLICATE_REVIEW.json, COMPARISON.json và QUERY_COMPARISON.json trong cùng thư mục.
