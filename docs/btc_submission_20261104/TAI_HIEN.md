# Cài đặt, vận hành và tái hiện bài nộp

## 1. Chọn đúng mức tái hiện

**Bài đội chọn nộp:** submission_17.zip. Hash local đã biết khớp REPORT export dense V1 trên Drive. Các nhánh Qwen/E5/corpus retrieval/fallback không phải thành phần của file này.

| Mục tiêu | Cách làm |
|---|---|
| Kiểm tra đúng file đã nộp | Tải ZIP từ link, đối chiếu FINAL_SUBMISSION.json và audit schema |
| Tái xuất đúng kết quả đã có | Khôi phục prepared.zip, worker score và REPORT/EXPERIMENT của run dense V1 |
| Chạy lại reranker từ candidate cũ | Notebook stage9_dense_recovery_colab.ipynb, khôi phục nguồn và Dense V1 candidates |
| Xây lại phương pháp từ raw data | Các lệnh phần 5; candidate output là contract native của repository, có thể khác bytes/thứ tự so với artifact Dense V1 lịch sử |

Không sửa manifest producer để biến candidate native thành dense-results. Notebook recovery nhận producer lịch sử dense-results/dense-v2-cached-candidates; CLI dense_index.search xuất dense-candidates, được S09 nhận trực tiếp. Hai đường được giữ riêng. Muốn kiểm tra **đúng bài nộp theo byte**, dùng artifact lịch sử thay vì tự thay producer rồi yêu cầu hash giống hệt.

## 2. Môi trường và phụ thuộc

Linux hoặc WSL cho pipeline đầy đủ vì một số writer lock dùng fcntl. Python 3.10/3.11 là môi trường đề xuất cho cài lại; code có log đã chạy trên Colab Python 3.13 nhưng không coi mọi combination package là đã được kiểm thử.

| Phần | Requirements / thư viện |
|---|---|
| Audit | requirements-eda.txt; PyArrow |
| Import/cleaning | requirements-stage3.txt, requirements-stage4.txt; PyArrow22 |
| Chunking | requirements-stage5.txt; Transformers4.57.1, tokenizers0.22.1, huggingface-hub0.35.3 |
| Lexical cũ tùy chọn | requirements-stage7.txt; PyVi0.1.1, jieba0.42.1, NumPy |
| Dense/index | requirements-stage8.txt, requirements-stage8-colab.txt; NumPy, FAISS CPU1.12.0, PyTorch |
| S09 | requirements-stage9.txt, requirements-stage9-colab.txt; Transformers4.57.1, tokenizers0.22.1, safetensors |
| Thử nghiệm mới | Dependencies được pin trong notebook; giữ PyTorch phù hợp GPU/driver |

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-stage8.txt -r requirements-stage9.txt
python -m pip install -r requirements-stage8-colab.txt -r requirements-stage9-colab.txt
```

Lệnh GPU để pip chọn wheel phụ thuộc nền tảng; trên Colab giữ torch có sẵn nếu tương thích. Không cố dùng torch CUDA13 build cho driver không hỗ trợ. Lưu `python -m pip freeze`, `python --version`, `nvidia-smi` cùng run để ghi nhận môi trường thực tế.

## 3. Kiểm tra nhanh file private

Lấy file được đại diện xác nhận từ [submission_17.zip trên Drive](https://drive.google.com/file/d/1TNABd0SSOcA1M_lsojkWGfZ-ejVFVL2h/view), đặt ở repository root. Tải query chính thức, đặt query/query.parquet hoặc Data/official/ViBioMIR/query.parquet.

```bash
python -m pip install pyarrow==22.0.0
python scripts/analyze_submission_zip.py --input submission_17.zip --query query/query.parquet --output reports/submission17_reproduction_audit
```

Đối chiếu: 1.200 query đúng ID/thứ tự, 144.000 chunk, ZIP chỉ có submission.json; SHA256 ZIP `0963641cb422e4223d6fc4b0c22eb09938f8b03c8227053cc7a398bf33e25dbd`, JSON `24e4e6490afc53f2a08af229b54c354a7d47436b96f00f92efb1ebf6ba671e32`.

Audit ZIP không tự đo relevance hoặc đối chiếu toàn bộ corpus. Bằng chứng source validation của export gốc nằm tại evidence/REPORT.json và evidence/VALIDATION.json. Các cờ historical official_submission_ready=False / pending S11 được giữ nguyên; đây là trạng thái gate nội bộ lúc export, không được sửa thành chứng nhận của BTC.

## 4. Tái hiện từ artifact đã hoàn thành

### 4.1 Đường Colab

Tải/giữ cấu trúc Drive theo DU_LIEU_VA_ARTIFACT.md. Mở colab/stage9_dense_recovery_colab.ipynb trong runtime mới. Cấu hình chính: DENSE_VERSION=query_results_v1, RERANK_TOP_K=400, LIMITS=120/120/6/0.8. Chạy từng cell:

1. Mount Drive, tải requirements, giải nén **code bundle đã kiểm tra BUNDLE.json**.
2. Kiểm tra source sáu ZIP và 1.200 candidate files.
3. Restore source, kiểm tra query/links, adapt candidate bằng đối chiếu source.
4. Restore đúng prepared.zip nếu có; không rebuild gzip packet khi cần reuse score cũ.
5. Khôi phục worker theo namespace workers_by_packets có packet hash đúng. Nếu đã complete, không cần inference lại.
6. Nếu cần chấm lại, chạy pilot5 query, sau đó STOP_AFTER_QUERIES=None và rerun riêng worker cell.
7. Validate worker/source rồi package. Đối chiếu hash kết quả với bài gốc.

Run gốc: [stage9_dense_query_results_v1_k400_120_120_v1](https://drive.google.com/drive/folders/1r0xka2F-afmQ4c22mLNbU_A0mrv-kwBe). Export khớp hash: [submission_20261031T065454786235Z](https://drive.google.com/drive/folders/1VjmZpAQKFawzrD0IbrwCIwi6HkX7P1u_).

### 4.2 Kiểm chứng bằng packet và score

Sau khi restore nguyên prepared bundle và score, các CLI của frozen producer tương ứng:

```bash
python -m vibiomir.stage9_pipeline validate-worker --prepared-dir PATH_TO_PREPARED --output-dir PATH_TO_WORKER_0 --worker-id 0
python -m vibiomir.stage9_pipeline export --prepared-dir PATH_TO_PREPARED --output-dir NEW_RESULTS --mode reranked --workers-dir PATH_TO_WORKERS_PARENT
python -m vibiomir.stage9_pipeline validate --prepared-dir PATH_TO_PREPARED --output-dir NEW_RESULTS --workers-dir PATH_TO_WORKERS_PARENT --chunk-dir PATH_TO_STRUCTURAL_STORE --queries Data/official/ViBioMIR/query.parquet
```

PATH_TO_WORKERS_PARENT phải chứa worker_0; thay các ký hiệu PATH bằng vị trí thật đã tải. Dùng đúng source modules của bundle tạo packet; không trộn PYTHONPATH với source khác phiên bản. Gzip timestamp có thể làm packet hash thay đổi dù văn bản giống; restore đúng bytes mới tái sử dụng score được.

## 5. Xây từ dữ liệu gốc

Các lệnh sau là đường rebuild phương pháp từ repository; chạy từ project root với data snapshot đúng và output chưa tồn tại. Các bước lớn có thể kéo dài nhiều ngày, cần local SSD/RAM và GPU. Nếu artifact đã đầy đủ thì ưu tiên phần 4 để kiểm tra bài gốc.

### 5.1 Audit và import version

```bash
python -m pip install -r requirements-eda.txt -r requirements-stage3.txt
python -m exploration.audit --data-dir Data --output-dir reports/data_audit/full_run_01 --batch-size 32 --sample-size 600
python -m vibiomir prepare-documents --data-dir Data --audit-dir reports/data_audit/full_run_01 --output-dir artifacts/document_store_stage3_full_v2 --config configs/document_store_stage3.json
python -m vibiomir validate-documents --store-dir artifacts/document_store_stage3_full_v2 --data-dir Data
```

Data/ViBioMIR/shards phải chứa đúng raw shard của audit snapshot. Các input audit manifest/record_audit phải được cung cấp nếu bỏ qua audit. Hướng dẫn chi tiết và resume tại docs/DATA_AUDIT.md và docs/DOCUMENT_STORE_STAGE3.md.

### 5.2 Cleaning và structural chunks

```bash
python -m pip install -r requirements-stage5.txt
python -m vibiomir clean-documents --store-dir artifacts/document_store_stage3_full_v2 --data-dir Data --output-dir artifacts/cleaning_stage4_full_apply --config configs/cleaning_stage4_reviewed.json --mode apply
python -m vibiomir validate-cleaned --output-dir artifacts/cleaning_stage4_full_apply --store-dir artifacts/document_store_stage3_full_v2 --data-dir Data
python -m vibiomir prepare-chunk-inputs --cleaned-dir artifacts/cleaning_stage4_full_apply --store-dir artifacts/document_store_stage3_full_v2 --data-dir Data --output-dir artifacts/stage5_inputs_full --batch-documents 500
python -m vibiomir download-chunk-tokenizer --output-dir artifacts/tokenizers/bge_m3_stage5
python -m vibiomir build-chunks --input-dir artifacts/stage5_inputs_full --output-dir artifacts/chunks_stage5_full_structure --tokenizer-dir artifacts/tokenizers/bge_m3_stage5 --strategy structure --config configs/chunking_stage5.json
python -m vibiomir validate-chunks --input-dir artifacts/stage5_inputs_full --output-dir artifacts/chunks_stage5_full_structure --tokenizer-dir artifacts/tokenizers/bge_m3_stage5
```

Không dùng config cleaning chưa review để thay reviewed config. Bất kỳ source/config/model thay đổi nào đều tạo run khác; không kỳ vọng source manifest hash trùng nếu bytes hoặc môi trường producer khác. Xem docs/CLEANING_STAGE4.md và docs/CHUNKING_STAGE5.md để review policy/limitations.

### 5.3 BGE embedding, merge, chỉ mục và candidate native

```bash
python -m pip install -r requirements-stage8-colab.txt
python -m vibiomir.dense_stage8 plan --config configs/dense_stage8.json
python -m vibiomir.dense_stage8 bundle --config configs/dense_stage8.json --queries Data/official/ViBioMIR/query.parquet
```

Chạy colab/stage8_dense_worker.ipynb cho WORKER_ID=0..5, theo docs/RETRIEVAL_STAGES7_8.md. Có thể chạy tuần tự sáu logical worker nếu chỉ có một GPU. Worker0 tạo query_vectors. Sau đó merge và index:

```bash
python -m vibiomir.dense_stage8 merge --plan artifacts/dense_stage8_bge_structure/plan.json --chunk-dir artifacts/chunks_stage5_full_structure --workers-dir artifacts/dense_stage8_bge_structure --output-dir artifacts/dense_stage8_bge_structure/merged
python -m vibiomir.dense_index build --vector-dir artifacts/dense_stage8_bge_structure/merged --output-dir artifacts/dense_stage8_bge_structure/index_ivfpq
python -m vibiomir.dense_index search --vector-dir artifacts/dense_stage8_bge_structure/merged --index-dir artifacts/dense_stage8_bge_structure/index_ivfpq --query-dir artifacts/dense_stage8_bge_structure/query_vectors --output-dir artifacts/candidates_stage8_bge --top-k 500
```

Chỉ mục được train trên corpus vectors, không dùng relevance labels. Native candidate được đưa thẳng vào S09 ở bước sau. Producer Dense V1 lịch sử trên Drive có manifest/coverage và candidate đã có sẵn; code đóng gói không giả định việc đổi tên native output là đủ tái tạo producer đó.

### 5.4 BGE rerank top400 và xuất

configs/submission_17_rebuild.json là config CLI hợp lệ cho đường native candidate, cùng các giới hạn chính của phương pháp dense recovery; không thay thế prepared identity lịch sử.

```bash
python -m pip install -r requirements-stage9-colab.txt
python -m vibiomir.stage9_pipeline prepare --config configs/submission_17_rebuild.json
python -m vibiomir.stage9_pipeline rerank-worker --prepared-dir artifacts/retrieval_submission17_rebuild --output-dir artifacts/submission17_rebuild_workers/worker_0 --worker-id 0 --batch-size 8 --padded-tokens 8192
python -m vibiomir.stage9_pipeline export --prepared-dir artifacts/retrieval_submission17_rebuild --output-dir artifacts/submission17_rebuild_results --mode reranked --workers-dir artifacts/submission17_rebuild_workers
python -m vibiomir.stage9_pipeline validate --prepared-dir artifacts/retrieval_submission17_rebuild --output-dir artifacts/submission17_rebuild_results --workers-dir artifacts/submission17_rebuild_workers --chunk-dir artifacts/chunks_stage5_full_structure --queries Data/official/ViBioMIR/query.parquet
```

Để package với allowlist sau validation, gọi scripts/stage9_expand_support.package với results dir, danh sách ID query theo file official, tập ID integer từ official links và một thư mục output mới. Notebook dense recovery đã bao gồm bước package/đối chiếu links, là cách vận hành khuyến nghị cho việc này. Model inference và ANN có sai khác số học giữa môi trường; không bảo đảm hash rebuild bằng hash bài lịch sử.

## 6. Các route thử nghiệm khác

- Hybrid baseline cũ: configs/retrieval_stage9.json và docs/RETRIEVAL_STAGE9.md. Config gốc top200 và selection20/40/4, khác submission17.
- Chấm lại ZIP bằng Qwen/BGE: docs/COLAB_MULTIMODEL_EVIDENCE.md; không có retrieval mới.
- Bốn nhánh corpus mới: docs/COLAB_CORPUS_MULTIRETRIEVAL.md; chưa hoàn thành theo log nộp cho hồ sơ.
- Fallback: docs/COLAB_PARTIAL_RETRIEVAL_FALLBACK.md; nhập checkpoint dang dở và baseline, chấm theo thời gian rồi export đủ query. Không phải producer của submission17 được đại diện xác nhận.

## 7. Kiểm thử và ràng buộc tái hiện

```bash
python -m unittest discover -s tests -p 'test_stage9*.py' -v
python -m unittest tests.test_dense_recovery -v
python -m unittest tests.test_multimodel_evidence -v
python -m unittest tests.test_corpus_multiretrieval -v
python -m unittest tests.test_partial_retrieval_fallback.FallbackTest -v
```

Một số test tùy chọn cần đúng dependencies/tokenizer local; suite nguyên repository chưa được chạy lại toàn bộ khi đóng hồ sơ này. Hồ sơ giữ báo cáo lịch sử và test source, không gán một số test pass của phiên cũ cho toàn bộ code mới. Không có GT chính thức trong gói để tính F2/LCS tại local.

Nếu run báo thiếu nguồn, candidate producer khác, packet hash khác, hoặc thiếu query, dừng và dùng đúng artifact. Không sửa SHA/state để vượt kiểm tra. Dataset/API có thể cần cấp quyền phù hợp; mọi URL thực tế nằm ở LINKS.json.
