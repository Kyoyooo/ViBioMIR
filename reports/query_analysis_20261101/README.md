# Phân tích query.parquet

Nguồn: `query/query.parquet`, đọc toàn bộ 1.200 bản ghi ngày 2026-11-01.
SHA256: `ce13508b893bbd6b9169679d540630bc37eb5950cad08b4d5e0e5d7063670c08`.
Hash trùng query snapshot của baseline S09. File nguồn không bị chỉnh sửa.

## Cấu trúc và chất lượng dữ liệu

- Schema: `id: int64 not null`, `query: string not null`.
- 1.200 ID duy nhất, đúng thứ tự 1–1.200.
- Không có query rỗng, không có trùng nguyên văn hoặc trùng sau NFC + gom khoảng trắng + casefold.
- Không có ký tự thay thế U+FFFD hoặc khoảng trắng thừa đầu/cuối.
- Bốn query có biểu diễn Unicode thay đổi nếu chuẩn hóa NFC: 103, 470, 976, 1067.
  Đây là khác biệt biểu diễn, không tự nó chứng minh văn bản lỗi. Giữ nguyên query chính thức;
  chỉ tạo bản chuẩn hóa riêng nếu cần tìm kiếm, và ghi nhận thành thí nghiệm mới.
- Cả 1.200 query có dấu hiệu chữ tiếng Việt theo quy tắc ký tự. Đây không phải bộ nhận diện
  ngôn ngữ đã được kiểm định và không loại trừ thuật ngữ tiếng Anh bên trong query.

## Độ dài

Đơn vị khoảng trắng = `len(query.split())`: không phải số từ tiếng Việt hoặc token model.

| Chỉ số | Ký tự | Đơn vị khoảng trắng |
|---|---:|---:|
| Nhỏ nhất | 18 | 5 |
| Trung vị | 81 | 18 |
| Trung bình | 129,51 | 29,91 |
| P90 | 281,10 | 66 |
| P95 | 364,05 | 84 |
| P99 | 494,06 | 111 |
| Lớn nhất | 1.243 | 281 |

| Độ dài theo khoảng trắng | Số query | Tỷ lệ |
|---|---:|---:|
| 1–20 | 661 | 55,1% |
| 21–50 | 329 | 27,4% |
| 51–100 | 189 | 15,8% |
| 101–200 | 20 | 1,7% |
| Trên 200 | 1 | 0,1% |

Query dài nhất là ID 219: 1.243 ký tự, 281 đơn vị khoảng trắng, gồm ba ý đánh số
về độ tuổi tiêm vaccine, tiền sử dị ứng của người hỏi và điều kiện của người chồng.
Các ID dài cần review trước: 219, 271, 71, 93, 1066, 130, 179, 471, 158, 767.
Độ dài query không suy ra được độ dài evidence đáp án hoặc số tài liệu đúng.

## Dấu hiệu nội dung theo quy tắc

Các nhóm chồng lấp, đếm query có từ/cụm từ khớp; không phải nhãn ý định hay nhãn y khoa
đã được người gán. Ví dụ có chữ 'ung thư' chưa chứng minh người hỏi mắc ung thư;
nhắc đến ăn uống chưa chắc là yêu cầu tư vấn dinh dưỡng.

| Dấu hiệu | Số query | Tỷ lệ |
|---|---:|---:|
| Điều trị/xử trí | 333 | 27,8% |
| Nguy cơ/tiên lượng/ảnh hưởng | 310 | 25,8% |
| Dùng thuốc | 248 | 20,7% |
| Nguyên nhân/chẩn đoán | 244 | 20,3% |
| Xét nghiệm/kết quả/thăm dò | 236 | 19,7% |
| Giải thích/định nghĩa | 139 | 11,6% |
| Ăn uống/sinh hoạt | 88 | 7,3% |
| Phòng bệnh/lây nhiễm/tiêm chủng | 85 | 7,1% |

473 query (39,4%) khớp ít nhất hai nhóm trên; chỉ là tín hiệu cần review đa ý định.
172 query không khớp nhóm nào, không được hiểu là query không có ý định.
126 query có ít nhất hai dấu hỏi; 248 query không có dấu hỏi.
Không dùng dấu hỏi làm điều kiện bắt buộc để nhận diện câu hỏi.

| Ràng buộc có thể quan trọng | Số query | Tỷ lệ |
|---|---:|---:|
| Có chữ số | 521 | 43,4% |
| Cụm thời gian theo regex | 266 | 22,2% |
| Ngữ cảnh trẻ em | 162 | 13,5% |
| Cụm nghi vấn/chưa chắc chắn | 146 | 12,2% |
| Thai kỳ/cho bú/sau sinh | 117 | 9,8% |
| Tuổi bằng số + đơn vị tuổi | 110 | 9,2% |
| Phủ định rõ theo regex hẹp | 76 | 6,3% |
| Đơn vị đo theo danh sách regex | 41 | 3,4% |

417 query kết thúc bằng 'không' (có thể kèm dấu câu). Không gán tất cả các trường hợp
này thành phủ định: 'có tiêm được không?' là cấu trúc nghi vấn. Regex phủ định hẹp
vẫn có thể khớp sai/nghĩa nghi vấn, nên chỉ dùng cho review, không hard-filter docs.

## Ý nghĩa đối với pipeline

1. Giữ query gốc trong mọi lần chạy; trích xuất ràng buộc thành sidecar có start/end
   trong bản gốc. Không xóa số, đơn vị, phủ định, thời gian hoặc bệnh nền để chỉ giữ tên bệnh.
2. Chia query phức tạp thành các ý bổ sung có source span. ID 219 là ca cần review;
   không thay toàn bộ query bằng một tên bệnh hoặc một câu hỏi ngắn.
3. So sánh reranker trên cùng pool đã có. Đo token thực tế sau prompt/template bằng
   tokenizer của từng model; các số whitespace ở trên không chứng minh không bị truncate.
4. Nếu xây BM25, thử thêm trường giữ nguyên cụm thuật ngữ, số và đơn vị trên cùng
   validation set. Nhu cầu có số đo là động lực thử lexical retrieval, chưa là bằng chứng tăng điểm.
5. Tách chọn doc và evidence. Với query nhiều ý, thử evidence bao phủ từng ý,
   đồng thời giữ nguyên văn source và đo theo evaluator LCS hai chiều chính thức.
6. NER nên ưu tiên bệnh/triệu chứng/thuốc/xét nghiệm + thuộc tính phủ định/thời gian/đối tượng.
   Mapping ICD-10 là metadata bổ sung, không thay thế query hoặc lọc cứng ngay.
7. Nhóm đánh giá gồm ngắn/dài, số đo, trẻ em/thai kỳ, phủ định, xét nghiệm, nhiều ý.
   Nhóm có thể chồng lấp. Không điều chỉnh hyperparameter trên test gold không được phép.

Ví dụ ID 3 có `Beta-HCG`, `dưới 0.10 U/L`; phải giữ quan hệ so sánh và đơn vị.
ID 15 có `không bị HIV`; nhắc HIV không đồng nghĩa có HIV.
ID 271 mô tả trẻ gần hai tháng, diễn biến từng ngày và kết quả xét nghiệm;
chỉ giữ 'sốt' sẽ làm mất ngữ cảnh truy hồi. Đây là phân tích cấu trúc query, không chẩn đoán.

## Artifact và tái lập

- `SUMMARY.json`: số liệu tổng hợp, hash và giới hạn phương pháp.
- `QUERY_FLAGS.json`: query nguyên văn + độ dài + các dấu hiệu cho từng ID.
- `RULES.json`: toàn bộ regex để kiểm tra và sửa quy tắc công khai.
- Script: `scripts/analyze_query_parquet.py` (cần pyarrow).

Chạy từ root project: `python scripts/analyze_query_parquet.py`.
Ở lần phân tích này pyarrow được cài riêng trong `.cache/query-analysis`, không sửa môi trường
Python chung. Có thể đặt `PYTHONPATH` tới thư mục đó hoặc dùng môi trường đã có pyarrow.

Không có qrels, chunk đáp án, nhãn ý định do người kiểm tra hoặc tokenizer model trong phép đo
này. Vì vậy chưa thể xác định recall, lỗi reranker, kích thước evidence tối ưu, hay mức tăng F2.
