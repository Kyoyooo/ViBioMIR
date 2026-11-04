# Hồ sơ thuyết minh ViBioMIR — ROAD TO AI 2026, Stage 3

**Ngày lập:** 04/11/2026. **Hạn theo thông báo BTC:** 23:59 ngày 04/11/2026, giờ Việt Nam (UTC+07:00).

**Đội:** onlyrelu. **Đại diện:** Võ Trần Duy Hoàng. **Bài nộp được đại diện xác nhận:** submission_17.zip, route Dense V1 + BGE rerank. Hash ZIP/JSON khớp REPORT export đã đọc trên Drive. [Thư mục dữ liệu và artifact](https://drive.google.com/drive/folders/1-9NUCeil3SLWizRsacc4y0FXkzVgSDqr?usp=sharing).

ViBioMIR là hệ thống truy hồi tài liệu và evidence y sinh đa ngữ cho câu hỏi tiếng Việt. Hồ sơ cung cấp phương pháp, dữ liệu, model, mã nguồn và quy trình tái hiện. Các kết quả đã chạy được phân biệt với nhánh thử nghiệm còn dang dở.

## Đọc theo thứ tự

| Yêu cầu BTC | Tài liệu |
|---|---|
| 1.1 Tổng quan, kiến trúc và luồng xử lý | [THUYET_MINH_SAN_PHAM.md](THUYET_MINH_SAN_PHAM.md) |
| 1.2 Nguồn dữ liệu, xử lý và cách truy cập | [DU_LIEU_VA_ARTIFACT.md](DU_LIEU_VA_ARTIFACT.md) |
| 1.3 Model, checkpoint, cấu hình và prompt | [MO_HINH.md](MO_HINH.md) |
| 1.4 Mã nguồn, thư viện và cấu hình | [TAI_HIEN.md](TAI_HIEN.md), mã nguồn đính kèm và SOURCE_MANIFEST.json trong gói |
| 1.5 Cài đặt và chạy lại | README này và [TAI_HIEN.md](TAI_HIEN.md) |
| Link chia sẻ và liên hệ đội | [LINKS.json](LINKS.json) |
| Xác định đúng bài nộp private test | [FINAL_SUBMISSION.json](FINAL_SUBMISSION.json) |

Repository được cấu hình tại [github.com/Kyoyooo/ViBioMIR](https://github.com/Kyoyooo/ViBioMIR). Quyền truy cập của BTC phải được chủ repository xác nhận. Gói mã nguồn ZIP đi kèm chứa phiên bản workspace tại lúc đóng gói, gồm cả thay đổi chưa commit; SOURCE_MANIFEST.json ghi checksum từng file nên không phụ thuộc việc nhánh GitHub đã push hết hay chưa.

## Cài đặt nhanh

Môi trường đề xuất cho tái hiện CPU: Linux/WSL, Python 3.10 hoặc 3.11, SSD local. GPU inference dùng NVIDIA CUDA qua Colab hoặc máy riêng; cài PyTorch phù hợp với driver thay vì chép nguyên CUDA build giữa hai máy.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-stage9.txt
```

Các bước từ raw data đến chunk/index cần dependencies riêng tại các requirements-stage*.txt; xem TAI_HIEN.md. Không coi một môi trường chỉ cài stage9 là đủ cho toàn bộ tiền xử lý và FAISS.

Đường tái hiện ít bước nhất khi có artifact đầy đủ:

1. Tải dữ liệu và artifact từ link trong LINKS.json; giữ đúng cấu trúc thư mục trong DU_LIEU_VA_ARTIFACT.md.
2. Mở colab/stage9_dense_recovery_colab.ipynb trong runtime GPU mới.
3. Notebook kiểm tra bundle, source, query và candidate Dense V1; chuẩn bị packet; BGE rerank; kiểm tra và xuất submission.
4. Nếu bài private thực tế dùng partial fallback, chạy tiếp colab/stage9_partial_retrieval_fallback_colab.ipynb với đúng baseline, snapshot và plan được bàn giao.
5. Đối chiếu hash JSON/ZIP với FINAL_SUBMISSION.json. Kết quả GPU có thể khác nhẹ giữa môi trường; khôi phục score/packet đã lưu để kiểm tra chính xác bài nộp gốc.

## Hoàn thiện trước khi gửi BTC

LINKS.json và FINAL_SUBMISSION.json đã được điền theo xác nhận của đại diện và đối chiếu REPORT trên Drive. Trước khi gửi, kiểm tra lại quyền tải artifact và bài private thực tế. Submission ID, thời điểm nộp và email liên hệ riêng chưa được cung cấp; không tự suy ra chúng từ tên file hoặc điểm công khai.

Chạy từ repository root:

```bash
python scripts/build_btc_submission_package.py
```

Lệnh tạo ZIP tài liệu + mã nguồn, bản HTML thuyết minh để in PDF, manifest checksum và báo cáo các thông tin còn thiếu trong deliverables/r2ai_stage3_20261104. Bản HTML dùng trình duyệt để Print → Save as PDF nếu cần. File PDF không phải điều kiện để sử dụng README/Markdown trong gói.

Hồ sơ chỉ sẵn sàng gửi khi các link bắt buộc đã được điền, BTC được cấp quyền tải, FINAL_SUBMISSION được xác nhận đúng bài private và báo cáo đóng gói không còn thiếu thông tin bắt buộc. Công cụ không gửi email hoặc thay đổi quyền chia sẻ.

Mẫu email nằm trong EMAIL_DRAFT.txt; người đại diện kiểm tra và gửi thủ công đến địa chỉ trong thông báo BTC.
