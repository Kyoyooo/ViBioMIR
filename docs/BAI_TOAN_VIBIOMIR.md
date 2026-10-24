# ViBioMIR: Truy hồi thông tin y sinh đa ngôn ngữ

Tài liệu tổng hợp đặc tả do người dùng cung cấp, được bổ sung từ dataset card,
metadata repository và các tệp Parquet chính thức trên Hugging Face ngày
24/10/2026. Phần đánh giá, hạn chế mô hình và submission vẫn dựa trên đặc tả
người dùng; dataset card không mô tả đầy đủ các quy định này. Khi có cập nhật,
ưu tiên thông báo và bộ chấm chính thức của Ban Tổ chức (BTC).

## 1. Bối cảnh

Tri thức y sinh phân bố không đồng đều giữa các ngôn ngữ. Nguồn tiếng Việt đang
mở rộng nhưng còn hạn chế về chiều sâu và chuyên môn so với nguồn tiếng Anh và
tiếng Trung. Điều này tạo khoảng cách thông tin y tế đối với người dùng tiếng Việt.

Cuộc thi yêu cầu xây dựng hệ thống truy hồi thông tin y sinh đa ngôn ngữ: đầu vào
là truy vấn tiếng Việt, đầu ra là các tài liệu và đoạn nội dung liên quan, bất kể
nguồn viết bằng tiếng Việt, tiếng Anh hay tiếng Trung. Đây là nhiệm vụ **truy hồi**;
không yêu cầu sinh câu trả lời y khoa.

## 2. Hình thức hóa và mục tiêu

Cho tập truy vấn tiếng Việt `Q = {q₁, q₂, …, qₙ}` và các nguồn y sinh được BTC cung
cấp/chỉ định. Với mỗi truy vấn, hệ thống tìm và xếp hạng tài liệu hoặc nội dung
liên quan từ ba ngôn ngữ Việt–Anh–Trung.

Các đội tự thiết kế quy trình thu thập → tiền xử lý → phân đoạn → embedding/lập
chỉ mục → truy hồi → reranking/hậu xử lý. BTC không cung cấp pipeline embedding
hoặc indexing sẵn có.

Ba yêu cầu chính:

1. **Cấp tài liệu:** xác định đúng tài liệu liên quan; truy hồi xuyên ngôn ngữ,
   vượt ra ngoài so khớp từ khóa.
2. **Cấp đoạn nội dung:** xác định phần thực sự chứa thông tin liên quan. Đội thi
   chủ động thiết kế cách phân đoạn và đơn vị truy hồi.
3. **Hiểu truy vấn y khoa tiếng Việt:** xử lý thuật ngữ, khái niệm y khoa/lâm sàng
   và câu hỏi có nhiều thông tin hoặc nhiều ý.

## 3. Dữ liệu chính thức

Nguồn: [Hugging Face — AIGuruTinix/ViBioMIR](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR).

### 3.1. Truy vấn: `query.parquet`

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `id` | integer | Mã định danh truy vấn |
| `query` | string | Câu hỏi y khoa tiếng Việt |

Ví dụ:

```json
{"id": 1, "query": "Cần làm gì đối với tình trạng tắc nghẽn đường tiết niệu do sỏi thận?"}
```

Nộp kết quả cho **toàn bộ 1.200 truy vấn đã phát hành ở cả hai giai đoạn**. Hệ
thống tự chọn truy vấn thuộc public/private phase để chấm; đội thi không cần biết
trước cách chia phase.

### 3.2. Danh sách tài liệu: `links_corpus_vi_en_zh.parquet`

**Tên tệp thực tế trên Hugging Face là `links_corpus_vi_en_zh.parquet`**;
`links_corpus.parquet` là tên được nhắc trong đặc tả ban đầu của cuộc thi.
Mã EDA hỗ trợ cả hai tên, nhưng yêu cầu chỉ định đường dẫn khi tìm thấy nhiều
tệp để tránh chọn nhầm phiên bản. Các tham chiếu `links_corpus.parquet` trong
phần quy định bên dưới giữ cách gọi của đặc tả ban đầu.

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `id` | integer | ID tài liệu chính thức, dùng để chấm |
| `url` | string | Liên kết bài viết gốc cần thu thập |

Ví dụ:

```json
{"id": 1, "url": "https://example.com/article"}
```

Đội thi tự crawl, xử lý, phân đoạn và xây dựng cơ sở tri thức. Trong phiên bản
đánh giá này, **mọi nhãn tài liệu đều dùng `id` trong `links_corpus.parquet`**.
Tài liệu ngoài danh sách không được tính là nhãn dương. Phải bảo toàn ánh xạ
`id → URL → văn bản → chunk`, kể cả khi URL chuyển hướng hoặc nội dung trùng nhau.

### 3.3. Thống kê do dataset card công bố

| Chỉ số | Giá trị |
| --- | ---: |
| Truy vấn | 1.200 |
| Số từ truy vấn: trung vị / P95 | 18 / 84 |
| Số từ truy vấn: min–max | 5–283 |
| Tổng liên kết | 1.220.598 |
| Liên kết tiếng Việt | 740.598 |
| Liên kết tiếng Anh | 240.000 |
| Liên kết tiếng Trung | 240.000 |

Nguồn tiếng Anh gồm tài liệu PubMed. Các số theo ngôn ngữ và số từ ở bảng này
là **thống kê công bố**, không phải kết quả nhận diện ngôn ngữ của EDA. Hai tệp
Parquet chỉ có các trường đã mô tả, không có cột nhãn ngôn ngữ/intent/chủ đề/năm
để gán chính xác từng tài liệu theo các nhóm trên. Dataset card có các hình
thống kê theo thể loại, năm và độ dài; không suy ra rằng các nhãn đó được phát hành.

Nguồn: [dataset card ở revision đã tải](https://huggingface.co/datasets/AIGuruTinix/ViBioMIR/blob/71be0e1239a19dfd7a717e2bc13e3bec21bd0334/README.md).

Kiểm tra trực tiếp query bằng `str.split()` cho min/P50/P95/max lần lượt là
**5/18/84/281**. Max khác số công bố 283; chưa có mô tả cách đếm từ để giải thích
chênh lệch này. Giữ riêng thống kê công bố và thống kê EDA, không sửa câu hỏi.

### 3.4. Config và split trên Hugging Face

| Config | Split | Tệp |
| --- | --- | --- |
| `query` | `train` | `query.parquet` |
| `corpus` | `train` | `links_corpus_vi_en_zh.parquet` |

`train` ở đây là tên split kỹ thuật trong metadata Hugging Face. Nó không xác
nhận đây là tập huấn luyện có nhãn, cũng không cung cấp cách chia public/private
phase. Repository ở revision đã kiểm tra không phát hành qrels hoặc nhãn chunk.
Schema đọc trực tiếp: query có `id: int64 not null`, `query: string not null`;
corpus có `id: int64`, `url: string`.

### 3.5. Giấy phép và trích dẫn

Dataset card nêu giấy phép **CC BY-NC 4.0** và yêu cầu ghi công khi sử dụng.
Điều khoản của từng website nguồn vẫn cần được xem riêng khi thu thập/sử dụng
nội dung. Đây là thông tin giấy phép ghi trên dataset card, không phải xác nhận
giấy phép của mọi bài viết liên kết.

Trích dẫn được đề nghị trong dataset card:

```bibtex
@online{medkb,
  title   = {MedKB -- Medical Knowledge Base},
  url     = {https://medkb.tinix.ai/},
  urldate = {2026-09-29}
}
```

Ngày trong BibTeX là giá trị nguồn đề nghị; ngày tải thực tế được lưu riêng trong
manifest. [Giấy phép được dataset card liên kết](https://creativecommons.org/licenses/by-nc/4.0/).

### 3.6. Phiên bản và dữ liệu đã bổ sung cục bộ

Repository: `AIGuruTinix/ViBioMIR`; commit đã tải:
`71be0e1239a19dfd7a717e2bc13e3bec21bd0334`.

```text
Data/official/ViBioMIR/
├── README.md
├── query.parquet
├── links_corpus_vi_en_zh.parquet
└── provenance.json
```

Đã kiểm tra metadata: query có 1.200 dòng; danh sách corpus có 1.220.598 dòng.
`provenance.json` lưu URL theo commit, thời điểm tải giờ Việt Nam, kích thước,
SHA-256, schema và số dòng từng Parquet. Tệp chính thức được lưu riêng với các
shard đã crawl. Script tải có thể chạy lại với output mới:

```bash
python -m exploration.download_official --output-dir Data/official/ViBioMIR_copy
```

Việc tải không thu thập lại các URL bài viết. Các bước EDA vẫn chạy cục bộ;
script tải riêng là bước cần mạng.

## 4. Dữ liệu ngoài và mô hình được phép

- Được dùng dữ liệu bên ngoài, nhưng phải trích dẫn rõ nguồn gốc, cung cấp đủ
  thông tin để BTC có thể kiểm tra/xác minh.
- Được dùng PLM và LLM có dữ liệu huấn luyện và/hoặc mô hình được công khai, ví dụ
  trên Hugging Face hoặc nền tảng tương tự.
- **Không được dùng LLM mô hình đóng**, ví dụ GPT-4o, Gemini.
- Mô hình phải được phát hành **trước ngày 01/08/2026, theo giờ Việt Nam** và có
  kích thước **không quá 15B tham số**. Ngưỡng thời gian là trước ngày đó, không
  phải bao gồm cả ngày 01/08.
- Có thể lựa chọn embedding đa ngôn ngữ phù hợp y học, thiết kế indexing/search,
  reranking/hậu xử lý và dùng dữ liệu y học mở hợp pháp để huấn luyện/đánh giá bổ trợ.

Nên lưu manifest nguồn dữ liệu, URL, ngày thu thập, phiên bản, giấy phép nếu có;
với mô hình, lưu tên, revision, số tham số và bằng chứng ngày phát hành. Đây là
đề xuất quản lý provenance, không phải trường bắt buộc của file submission.

## 5. Đánh giá

Đánh giá riêng ở **cấp document** và **cấp chunk** bằng Precision, Recall, F2.
Tính các chỉ số cho **từng truy vấn**, sau đó lấy trung bình cộng trên toàn bộ
truy vấn (**macro-average**).

### 5.1. Cấp tài liệu

Với một truy vấn, gọi `D` là tập tài liệu dự đoán, `G` là tập tài liệu liên quan
theo nhãn tham chiếu:

```text
Precision_doc = |D ∩ G| / |D|
Recall_doc    = |D ∩ G| / |G|
```

`D` và `G` là tập hợp: không nên nộp ID lặp trong `relevant_docs`.

### 5.2. Quy tắc khớp chunk

Chunk dự đoán chỉ đối chiếu với các chunk tham chiếu **cùng câu hỏi và cùng
`doc_id`**. Chuẩn hóa văn bản hai phía theo thứ tự:

1. Chuẩn hóa Unicode NFKC.
2. Giải mã HTML entity.
3. Chuyển chữ thường.
4. Gộp khoảng trắng liên tiếp và bỏ khoảng trắng đầu/cuối.

Với chunk dự đoán `c` và chunk tham chiếu `g` sau chuẩn hóa:

```text
overlap(c, g) = LCS_tokens(c, g) / |g|
```

`LCS_tokens` là độ dài **dãy con chung dài nhất** của hai chuỗi token: giữ thứ tự
nhưng không yêu cầu liên tiếp. Mẫu số là số token của **chunk tham chiếu**.
Một chunk dự đoán đúng nếu khớp **ít nhất một** chunk tham chiếu với overlap
**≥ 0,40**.

### 5.3. Cấp đoạn nội dung

```text
Precision_chunk = số chunk dự đoán được tính đúng / tổng số chunk dự đoán
Recall_chunk    = số chunk tham chiếu đã được tìm thấy / tổng số chunk tham chiếu
```

Hai tử số khác nhau: một phía đếm chunk dự đoán đúng, một phía đếm chunk tham
chiếu được phủ. Không mặc định rằng việc đối chiếu là ghép cặp một-một.

### 5.4. F2 và macro-average

Ở mỗi cấp và với từng truy vấn:

```text
F2 = 5 × Precision × Recall / (4 × Precision + Recall)
macro_P  = (Σ Pᵢ) / n
macro_R  = (Σ Rᵢ) / n
macro_F2 = (Σ F2ᵢ) / n
```

F2 ưu tiên Recall hơn Precision. **Không** tính macro_F2 bằng cách thay macro_P
và macro_R vào công thức F2. Cần cân bằng độ phủ và số kết quả trả về; chưa có
quy định top-k tối đa trong thông tin được cung cấp.

### 5.5. Các chi tiết chưa được đặc tả

Thông tin hiện tại chưa xác định tokenizer dùng cho LCS (đặc biệt với tiếng
Trung), quy ước mẫu số bằng 0/tập nhãn rỗng, cách xử lý chunk trùng và mọi chi
tiết của bộ chấm. Không tự coi các giả định triển khai là quy định chính thức.
Chưa có nhãn tham chiếu trong mô tả dữ liệu được cấp; EDA không thể tính điểm
Precision/Recall/F2 thực tế nếu thiếu nhãn.

## 6. Submission

File JSON là một mảng, mỗi phần tử ứng với một câu hỏi:

```json
[
  {
    "id": 1,
    "relevant_docs": [101, 202],
    "relevant_chunks": [
      {
        "doc_id": 101,
        "chunk_text": "Đoạn nguyên văn trích từ tài liệu có id 101.",
        "chunk_order": 0
      },
      {
        "doc_id": 202,
        "chunk_text": "Đoạn nguyên văn trích từ tài liệu có id 202."
      }
    ]
  }
]
```

Ví dụ minh họa cấu trúc; các ID/văn bản trên không phải kết quả truy hồi thật.

| Trường | Yêu cầu |
| --- | --- |
| `id` | ID câu hỏi, integer |
| `relevant_docs` | Danh sách ID tài liệu liên quan |
| `relevant_chunks` | Danh sách chunk liên quan |
| `doc_id` | ID tài liệu gốc trong `links_corpus.parquet` |
| `chunk_text` | Nội dung trích xuất từ tài liệu gốc, **không viết lại hoặc sinh mới** |
| `chunk_order` | Tùy chọn, integer không âm; bỏ qua thì dùng vị trí chunk trong mảng |

Đặc tả có điểm chưa thống nhất về kiểu ID tài liệu: mẫu/ví dụ ban đầu dùng
chuỗi (`"doc_id1"`), nhưng mô tả phiên bản hiện tại nói ID là integer từ
`links_corpus.parquet`. Ví dụ trên dùng integer theo mô tả hiện tại; cần đối
chiếu schema/validator chính thức trước khi nộp, không thay ID bằng URL hoặc ID tự tạo.

Nếu không có dự đoán ở một cấp, vẫn cung cấp trường tương ứng với `[]`. Bài
nộp phải có đủ 1.200 truy vấn; thiếu câu hoặc sai định dạng sẽ không được đánh giá.

### Quy trình nộp

1. Sinh file `.json` tuân thủ schema và chứa toàn bộ câu hỏi.
2. Nén thành ZIP **chỉ chứa duy nhất một file JSON ở thư mục gốc**, không có thư mục con.
3. Tải ZIP tại **My Submissions** trên
   [leaderboard.aiguru.com.vn](https://leaderboard.aiguru.com.vn/).

Mỗi đội bị giới hạn số lần nộp mỗi ngày để đảm bảo công bằng và tránh dò đáp án;
chưa có con số cụ thể trong thông tin cung cấp. Bài thiếu file/thiếu câu không
được đánh giá và không bị tính vào số lần nộp tối đa cho phép.

## 7. Dữ liệu đã crawl và lịch sử bổ sung

Khảo sát ban đầu tại `Data/ViBioMIR/`, trước khi tải tệp chính thức:

```text
Data/ViBioMIR/
├── shards/{A,B,C}/<domain>/part-*.parquet
├── state_A.sqlite3
├── state_B.sqlite3
├── state_C.sqlite3
├── last_run_A.json
└── last_run_C.json
```

- Có 1.195 shard Parquet, tổng 2.428.459.143 byte tại thời điểm khảo sát.
- Metadata ghi nhận tổng cộng 1.105.160 dòng. Có hai biến thể schema: 1.169
  shard đầy đủ và 26 shard chỉ có metadata crawl, thiếu các cột nội dung.
- Mẫu schema: `id`, `domain`, `url`, `status`, `final_url`, `ts`, `text_len`,
  `ver`, `source`, `title`, `desc`, `canonical`, `method`, `blocks`, `text`, `html_trim`.
- `blocks` trong mẫu là chuỗi JSON chứa danh sách `[loại_khối, văn_bản]`.
- SQLite chứa `crawl_state(id, url, domain, status, final_url, ts)` và `state_meta`.
- A/B/C là nhãn thư mục/nhóm thu thập; **không có bằng chứng đây là train/dev/test
  hoặc phân chia public/private**.
- `last_run_*.json` chỉ mô tả lần chạy gần nhất; không dùng làm tổng thống kê crawl.
- Lượt khảo sát đầu tiên chưa có query/danh sách corpus chính thức. Hai tệp đã
  được bổ sung ở `Data/official/ViBioMIR/` như mục 3.6; EDA truy vấn và độ phủ
  chính thức hiện được xuất trong [báo cáo](../reports/eda/REPORT.md).

### Đối chiếu sau khi bổ sung tệp chính thức

| Chỉ số | Giá trị |
| --- | ---: |
| Query hợp lệ, ID khác nhau | 1.200 |
| ID tài liệu chính thức khác nhau | 1.220.598 |
| ID đã có shard | 855.745 |
| ID có text HTTP 2xx không rỗng | 852.995 |
| Độ phủ text HTTP 2xx không rỗng | 69,88% |
| ID chính thức chưa có shard | 364.853 |
| ID trong shard ngoài danh sách chính thức | 0 |
| ID đã crawl nhưng không có URL nguồn khớp nguyên văn | 0 |

Danh sách chính thức gồm 94 tên miền. Phần thiếu tập trung ở 6 tên miền;
riêng `pubmed.ncbi.nlm.nih.gov` có 240.000 ID chính thức nhưng chỉ 43 ID có
bản ghi shard và **0 ID có text HTTP 2xx không rỗng**. Hai nguồn chưa có shard
nào là `nhathuoclongchau.com.vn` (79.598 ID) và `ask.39.net` (32.772 ID).
Đây là kết quả đối chiếu dữ liệu cục bộ, không phải số liệu do dataset card
công bố, cũng không phải Recall truy hồi.

Chi tiết: [references_summary.json](../reports/eda/references_summary.json) và
[độ phủ theo tên miền](../reports/eda/official_coverage_by_domain.csv).

## 8. Phạm vi triển khai hiện tại

Chỉ tổng hợp bài toán và khám phá dữ liệu đã thu thập: inventory/schema, trạng
thái crawl, độ dài/chất lượng văn bản, trùng ID/URL/nội dung, dấu hiệu ngôn ngữ,
thống kê khối nội dung tùy chọn, EDA 1.200 truy vấn, đối chiếu ID và URL nguồn với
danh sách chính thức, thống kê độ phủ theo tên miền. Chưa huấn luyện, embedding, indexing, truy hồi,
reranking hoặc tạo/nộp submission.

Hướng dẫn chạy và diễn giải giới hạn: [README](../README.md).
