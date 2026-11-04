# Dữ liệu, nguồn gốc và artifact bàn giao

## 1. Nguồn và quyền truy cập

Nguồn bài toán là [ViBioMIR do AIGuruTinix công bố](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR). Corpus làm việc trong repository gồm các shard đã được thu thập tại các domain nguồn, metadata, block và payload HTML local khi có. Các file raw đang dùng phải được chia sẻ qua link đội quản lý trong LINKS.json; link dataset công khai không thay thế cho đúng snapshot shard đã xử lý của đội.

Đợt xử lý được mô tả sử dụng dữ liệu đã có và khôi phục offline từ block/HTML đã lưu; không khẳng định đã thu thập lại toàn bộ Internet hoặc toàn bộ URL official. Official links là bảng đối chiếu ID; không được dùng thay corpus nội dung. Query chính thức là đầu vào inference, không phải nhãn huấn luyện.

Dataset card và nguồn gốc nội dung vẫn cần được giữ khi chia sẻ. Hồ sơ không tuyên bố đội sở hữu bản quyền văn bản của các domain gốc. Chỉ chia sẻ theo quyền và điều kiện BTC/dataset cho phép; phạm vi dữ liệu bên ngoài chưa có trong route được chọn không được gộp ngầm vào artifact.

## 2. Quy mô và xử lý

| Giai đoạn | Số liệu lưu trong báo cáo repository |
|---|---:|
| Raw audit | 1.195 shard / 1.105.160 record |
| Document store | 913.969 version / 855.745 document ID |
| Cleaning đầy đủ | 1.029.307 region; 147.803 lượt loại bỏ theo rule đã review |
| Structural store | 5.512.624 chunk / 1.712 batch |
| Semantic store riêng | 9.036.279 chunk; không dùng thay structural |
| Query chính thức | 1.200 query |

Không suy ra mọi record là một document độc lập hoặc một ngôn ngữ đã xác nhận. Một document có thể có nhiều version; heuristic language/script không phải nhãn ngôn ngữ đã được con người kiểm duyệt.

Chuỗi xử lý: audit checksum/schema → import version/provenance → cleaning có vị trí nguồn → chọn derived version theo policy → tạo region/chunk → kiểm tra text/hash/offset → embedding/index/candidate. Rule cleaning chi tiết tại configs/cleaning_stage4_reviewed.json; cấu hình chunk tại configs/chunking_stage5.json.

Evidence không được sửa chính tả, dịch hoặc diễn giải lại khi xuất. Chuẩn hóa phục vụ analyzer/vector là biểu diễn tìm kiếm riêng, không thay chunk_text gốc.

## 3. Snapshot chuẩn

```json
{
  "signature": "5ce3d7c02e6a0a8275e203a4cff9c12fb371fc8cf0fd9515574250acfff3751c",
  "manifest_sha256": "d88fd1c046a93950c7e97ef5c8786d933a677ef1f54e7880f5c34fa6c4ff40f1",
  "validation_sha256": "5bdc88900f47989a4edb653ae1f6b90ba90a79760f620edb35ac09a48d20d993",
  "documents": 855745,
  "chunks": 5512624,
  "batches": 1712
}
```

Query SHA256: `ce13508b893bbd6b9169679d540630bc37eb5950cad08b4d5e0e5d7063670c08`.

Official links SHA256: `87e44a792d7a99fb0fde5dff7f09f1a38a0a1fdf02ddd1ac93a005736e00b0a7`.

Sáu ZIP Stage 8 hợp lại chứa source structural. Mỗi ZIP chỉ có một phần batch, cùng plan và metadata; không coi một ZIP là corpus đầy đủ. Kiểm tra bằng stage9_dense_recovery.inspect_bundles và checksum payload từng batch. Dữ liệu cùng số batch nhưng khác signature/chunk count không tương thích.

## 4. Bố trí dữ liệu trên Drive cho dense recovery

```text
MyDrive/ViBioMIR/
  Data.zip
  Data/official/ViBioMIR/
    query.parquet
    links_corpus_vi_en_zh.parquet
  stage9_rerank_colab.zip
  dense_stage8_bge_structure/
    bundles/worker_0.zip ... worker_5.zip
    query_results_v1/results/
      manifest.json
      CORPUS_COVERAGE.json
      candidates/query-1.json ... query-1200.json
```

Data.zip có thể thay file official đã giải nén khi notebook tìm được duy nhất đúng thành viên. stage9_rerank_colab.zip là code/packet S09 có BUNDLE.json, không phải weights. Không dùng artifacts.zip hiện có như structural store nếu nó chỉ chứa cleaning database.

Để dựng từ đầu cần thêm raw shards, audit input_manifest/scan_status/record_audit, document store/cleaning hoặc đủ nguyên liệu tạo lại chúng, tokenizer registry và output embedding/index nếu muốn bỏ qua bước encode. Các file quá lớn không nằm trong ZIP mã nguồn, phải có tại link Drive.

## 5. Artifact phụ thuộc route cuối cùng

| Route | Ngoài source/query/links cần bàn giao |
|---|---|
| dense_v1_bge | Dense V1 candidates, frozen S09 bundle, prepared.zip và worker score/manifest của run dense recovery nếu cần tái hiện đúng bytes |
| submission_multimodel_selection | Baseline ZIP thật, run plan/generation policy, score Qwen/BGE đủ query và REPORT/provenance của lần merge |
| partial_retrieval_fallback | Baseline ZIP, frozen snapshot JSON và PRODUCER_PLAN, fallback PLAN, prepared packets, score đã hoàn tất, QUERY_AUDIT/REPORT của lần export |

Với fallback, chỉ những query đã chấm hợp lệ mới đổi. Vì vậy bàn giao đúng tập score tại thời điểm export là thiết yếu; chạy chấm thêm query rồi export lại có thể tạo bài khác. Cần giữ checkpoint/export gốc bất biến.

Các vector embedding mới dang dở vẫn hữu ích để resume, nhưng không phải điều kiện để BTC kiểm tra lại lựa chọn từ snapshot candidate đã đóng băng. Database BM25 local chưa backup cần được bảo toàn riêng trước khi reset runtime; không coi log “1712 batches” là bằng chứng đã có đủ candidate query.

## 6. Link và kiểm tra bàn giao

LINKS.json là nơi khai báo link Drive/OneDrive thực tế. Chủ đội cần kiểm tra bằng một tài khoản khác quyền tải các artifact bắt buộc, không chỉ quyền xem thư mục trống; xác nhận tên file/path khớp hướng dẫn. Công cụ đóng gói chỉ kiểm tra trường URL và cờ xác nhận, không tự chứng thực quyền truy cập tài khoản BTC.

Đặt FINAL_SUBMISSION.json và REPORT/PLAN của run cuối trong cùng thư mục Drive với ZIP private test để BTC đối chiếu. Không chia sẻ token HF, cookie, credential hoặc thư mục môi trường cá nhân.
