# ViBioMIR — Tổng hợp nội dung: Truy hồi thông tin y sinh đa ngôn ngữ

> **Tài liệu gốc:** *Truy hồi thông tin y sinh đa ngôn ngữ (ViBioMIR) — Từ thu thập, chuẩn hóa dữ liệu đến xây dựng hệ thống truy hồi cơ sở*  
> **Người trình bày:** Nguyễn Hoàng Trung — Đại học Công nghệ, Đại học Quốc gia Hà Nội (UET-VNU)  
> **Ngày trình bày:** 12/10/2026  
> **Quy mô tài liệu:** 48 trang trình chiếu  
> **Phạm vi tổng hợp:** Toàn bộ ba phần chính, các ví dụ, số liệu, công thức đánh giá, kiến trúc truy hồi và phụ lục. Các chú thích `[tr. ...]` tương ứng với số trang của PDF gốc.

---

## Mục lục

1. [Tổng quan bài toán ViBioMIR](#1-tổng-quan-bài-toán-vibiomir)
   - [1.1. Mục tiêu truy hồi xuyên ngôn ngữ](#11-mục-tiêu-truy-hồi-xuyên-ngôn-ngữ)
   - [1.2. Đánh giá ở cấp tài liệu và đoạn](#12-đánh-giá-ở-cấp-tài-liệu-và-đoạn)
   - [1.3. Cách xác định một chunk đúng](#13-cách-xác-định-một-chunk-đúng)
2. [Dữ liệu ViBioMIR và quy trình xây dựng corpus](#2-dữ-liệu-vibiomir-và-quy-trình-xây-dựng-corpus)
   - [2.1. Quy mô và phân bố nguồn dữ liệu](#21-quy-mô-và-phân-bố-nguồn-dữ-liệu)
   - [2.2. Những loại nội dung cần xử lý](#22-những-loại-nội-dung-cần-xử-lý)
   - [2.3. Vấn đề khi thu thập trang web](#23-vấn-đề-khi-thu-thập-trang-web)
   - [2.4. Phương pháp thu thập và pipeline](#24-phương-pháp-thu-thập-và-pipeline)
   - [2.5. Chuẩn hóa metadata](#25-chuẩn-hóa-metadata)
   - [2.6. Làm sạch và chuẩn hóa văn bản](#26-làm-sạch-và-chuẩn-hóa-văn-bản)
   - [2.7. Tiền xử lý đa ngôn ngữ](#27-tiền-xử-lý-đa-ngôn-ngữ)
   - [2.8. Chunking và bảo toàn ngữ cảnh](#28-chunking-và-bảo-toàn-ngữ-cảnh)
   - [2.9. Hậu xử lý và kiểm soát chất lượng](#29-hậu-xử-lý-và-kiểm-soát-chất-lượng)
3. [Xây dựng hệ thống baseline](#3-xây-dựng-hệ-thống-baseline)
   - [3.1. Phân nhóm chủ đề bằng ICD-10](#31-phân-nhóm-chủ-đề-bằng-icd-10)
   - [3.2. Các nhóm phương pháp truy hồi](#32-các-nhóm-phương-pháp-truy-hồi)
   - [3.3. Kiến trúc tổng thể](#33-kiến-trúc-tổng-thể)
   - [3.4. Lexical retrieval: TF-IDF và BM25](#34-lexical-retrieval-tf-idf-và-bm25)
   - [3.5. Dense retrieval](#35-dense-retrieval)
   - [3.6. Hybrid retrieval](#36-hybrid-retrieval)
   - [3.7. Reranking](#37-reranking)
   - [3.8. Ví dụ kết hợp các phương pháp](#38-ví-dụ-kết-hợp-các-phương-pháp)
   - [3.9. Từ kết quả truy hồi đến bằng chứng](#39-từ-kết-quả-truy-hồi-đến-bằng-chứng)
   - [3.10. Thiết kế đánh giá thực nghiệm](#310-thiết-kế-đánh-giá-thực-nghiệm)
4. [Phụ lục A — Danh sách các chương ICD-10](#4-phụ-lục-a--danh-sách-các-chương-icd-10)
5. [Phụ lục B — Ôn tập TF-IDF và BM25](#5-phụ-lục-b--ôn-tập-tf-idf-và-bm25)
6. [Tổng kết và các nguyên tắc xuyên suốt](#6-tổng-kết-và-các-nguyên-tắc-xuyên-suốt)
7. [Tài liệu tham khảo được liệt kê trong slide](#7-tài-liệu-tham-khảo-được-liệt-kê-trong-slide)
8. [Bảng đối chiếu phạm vi trang PDF](#8-bảng-đối-chiếu-phạm-vi-trang-pdf)

---

# 1. Tổng quan bài toán ViBioMIR

*Phạm vi: trang 3–7.*

## 1.1. Mục tiêu truy hồi xuyên ngôn ngữ

ViBioMIR đặt ra bài toán **truy hồi thông tin y sinh đa ngôn ngữ**: từ một **truy vấn y khoa bằng tiếng Việt**, hệ thống phải tìm kiếm thông tin liên quan trong kho tài liệu có nhiều ngôn ngữ, cụ thể là **tiếng Việt, tiếng Anh và tiếng Trung**. Kết quả không dừng lại ở việc xác định tài liệu đúng, mà còn phải **định vị đúng phần nội dung gốc** chứa thông tin cần tìm.

Ba thành phần cơ bản:

| Thành phần | Mô tả |
|---|---|
| Đầu vào | Một câu hỏi hoặc truy vấn y khoa bằng tiếng Việt |
| Kho tìm kiếm | Các tài liệu thuộc nhiều nguồn, bằng tiếng Việt, tiếng Anh và tiếng Trung |
| Đầu ra mong muốn | Tài liệu liên quan **và** đoạn nội dung gốc làm bằng chứng cho truy vấn |

Ví dụ truy vấn được đưa ra trong tài liệu:

> Cần làm gì đối với tình trạng tắc nghẽn đường tiết niệu do sỏi thận?

Điểm khó của bài toán là **ngôn ngữ truy vấn có thể khác ngôn ngữ của tài liệu chứa câu trả lời**. Đồng thời, một tài liệu có thể nói đúng bệnh nhưng không bàn đúng nội dung mà câu hỏi yêu cầu. Vì thế phải quan tâm đồng thời đến **mức độ liên quan của tài liệu** và **mức độ liên quan của đoạn bằng chứng**. [tr. 4]

## 1.2. Đánh giá ở cấp tài liệu và đoạn

ViBioMIR đánh giá ở hai cấp độ tách biệt:

### a. Document retrieval — truy hồi tài liệu

Với từng truy vấn, ký hiệu:

- `D`: tập tài liệu mà hệ thống dự đoán/trả về.
- `G`: tập tài liệu tham chiếu đúng (ground truth).
- `D ∩ G`: những tài liệu vừa được dự đoán, vừa nằm trong tập tham chiếu.

Hai đại lượng:

$$
P = \frac{|D \cap G|}{|D|}, \qquad
R = \frac{|D \cap G|}{|G|}.
$$

Trong đó:

- **Precision (P):** Trong những tài liệu hệ thống trả về, bao nhiêu tài liệu là đúng?
- **Recall (R):** Trong những tài liệu đúng cần tìm, hệ thống đã tìm được bao nhiêu?

### b. Chunk retrieval — truy hồi đoạn nội dung

Ở cấp đoạn (*chunk*), tài liệu trình bày ý nghĩa tương ứng:

- **Precision:** tỷ lệ chunk nộp được công nhận là đúng.
- **Recall:** tỷ lệ chunk tham chiếu mà hệ thống tìm được.

Đánh giá ở hai cấp riêng biệt là cần thiết vì tìm đúng tài liệu **không đồng nghĩa** với chỉ ra đúng đoạn chứa bằng chứng.

### c. F2-score và Macro F2

Thước đo trọng tâm là **F2**, được tính riêng cho từng truy vấn:

$$
F_2(q)=\frac{5P(q)R(q)}{4P(q)+R(q)}.
$$

Sau đó lấy trung bình qua tập truy vấn $Q$:

$$
F_2^{\mathrm{macro}}=\frac{1}{|Q|}\sum_{q\in Q}F_2(q).
$$

Phép tính được thực hiện **riêng ở cấp document và cấp chunk**. So với F1, F2 đặt trọng số lớn hơn vào **Recall**, nhưng Precision vẫn quan trọng vì trả về quá nhiều kết quả không liên quan vẫn làm giảm điểm. [tr. 5]

### d. Ví dụ tính F2

Giả sử tập tài liệu tham chiếu:

$$
G=\{d_2,d_5,d_9\}.
$$

| Phương án | Tập tài liệu trả về | Precision | Recall | F2 |
|---|---|---:|---:|---:|
| A — trả 2 tài liệu | $D_A=\{d_2,d_5\}$ | $1$ | $2/3$ | $\approx 0{,}714$ |
| B — trả 5 tài liệu | $D_B=\{d_2,d_5,d_9,d_{11},d_{12}\}$ | $0{,}6$ | $1$ | $\approx 0{,}882$ |

- **Phương án A** không trả nhầm tài liệu nào nên Precision bằng 1, nhưng bỏ sót $d_9$, khiến Recall chỉ đạt $2/3$.
- **Phương án B** trả đủ ba tài liệu tham chiếu nên Recall bằng 1, mặc dù có thêm hai tài liệu không liên quan.
- Do F2 nhấn mạnh Recall, **B đạt điểm cao hơn A**. Tuy nhiên, việc thêm tài liệu không liên quan vẫn làm Precision giảm.

**Bài học:** Không nên đánh đổi Recall bằng cách trả thật nhiều tài liệu mà không kiểm soát chất lượng; cần cân bằng khả năng tìm đủ bằng chứng với độ chính xác của kết quả. [tr. 6]

## 1.3. Cách xác định một chunk đúng

Đánh giá chunk có quy tắc đối chiếu cụ thể:

1. **Cùng truy vấn và cùng `doc_id`:** Một chunk dự đoán chỉ được so sánh với chunk tham chiếu thuộc đúng tài liệu và truy vấn tương ứng.
2. **Chuẩn hóa văn bản trước khi so khớp:** Bao gồm chuẩn hóa Unicode **NFKC**, giải mã **HTML entity**, chuyển **lowercase** và chuẩn hóa **khoảng trắng**.
3. **Đo sự trùng khớp theo chuỗi con chung dài nhất** (*Longest Common Subsequence — LCS*) trên token. LCS bảo toàn thứ tự token nhưng các token không bắt buộc đứng sát nhau.
4. **Ngưỡng đạt:** tỷ lệ overlap tối thiểu là **0,40**.

Với $c$ là chunk dự đoán và $g$ là chunk tham chiếu:

$$
\operatorname{overlap}(c,g)
=\frac{\operatorname{LCS}_{\mathrm{tokens}}(c,g)}{|g|}
\geq 0{,}40.
$$

Ở đây $|g|$ là **số token của đoạn tham chiếu**, không phải số token của đoạn dự đoán.

**Ví dụ:** Một chunk tham chiếu có 100 token; LCS giữa chunk dự đoán và tham chiếu có 45 token. Khi đó:

$$
\operatorname{overlap}=\frac{45}{100}=45\%\geq 40\%.
$$

Chunk thỏa ngưỡng overlap theo tiêu chí nêu trên, với điều kiện đối chiếu đúng truy vấn và `doc_id`. [tr. 7]

---

# 2. Dữ liệu ViBioMIR và quy trình xây dựng corpus

*Phạm vi: trang 8–26.*

## 2.1. Quy mô và phân bố nguồn dữ liệu

### a. Tổng quan corpus và tập truy vấn

| Hạng mục | Thông tin |
|---|---|
| Số lượng URL ban đầu | **4.394.718** |
| Số lượng host | **97** |
| Định danh tài liệu | Mỗi tài liệu có một **ID** và một **URL** |
| Số câu hỏi truy vấn | **1.200** câu hỏi bằng tiếng Việt |
| Độ dài trung vị của truy vấn | **18 từ** |
| Kiểu truy vấn | Có câu hỏi ngắn và câu hỏi gồm nhiều ý |
| Tính chất kho dữ liệu | Đa nguồn, đa ngôn ngữ, nhiều loại tài liệu |

**Cần phân biệt:** 4.394.718 URL là **danh sách điểm xuất phát**, không có nghĩa là đã sở hữu ngần ấy bài viết được trích xuất sạch và có thể lập chỉ mục ngay. Từ danh sách URL, cần tiếp tục thu thập, trích xuất và xử lý nội dung. [tr. 9]

### b. Nguồn dữ liệu tập trung vào một số host

| Host | Số URL |
|---|---:|
| `www.cnkang.com` | 963.438 |
| `www.120ask.com` | 918.479 |
| `www.familydoctor.com.cn` | 447.453 |
| `ask.39.net` | 337.375 |
| `zysjonline.com` | 243.347 |

Hai host đầu tiên chứa khoảng **42,8%** tổng số URL. Phân bố mất cân đối này dẫn đến hai yêu cầu: (1) phải khảo sát kỹ các website lớn do chúng chiếm tỷ trọng đáng kể; (2) vẫn phải nghiên cứu những nguồn nhỏ để không bỏ sót kiểu tài liệu khác biệt. [tr. 10]

## 2.2. Những loại nội dung cần xử lý

Corpus không bao gồm một loại văn bản đồng nhất, mà có các dạng:

| Loại tài liệu | Các thành phần thường gặp | Ý nghĩa đối với trích xuất/chia đoạn |
|---|---|---|
| **Hỏi đáp y khoa** | Câu hỏi, câu trả lời, thông tin người trả lời | Phải giữ mối quan hệ giữa câu hỏi và câu trả lời |
| **Bài viết kiến thức** | Bệnh, triệu chứng, chẩn đoán, điều trị | Phải bảo toàn các đề mục và đoạn nội dung tương ứng |
| **Sách, cẩm nang** | Chương, mục, bảng, tài liệu tham khảo | Cần giữ cấu trúc phân cấp và các phần nội dung có liên hệ |
| **Thuốc, dược liệu, tin tức** | Nhiều hình thức tổ chức và mục đích sử dụng | Không thể giả định một cấu trúc cố định cho mọi trang |

**Nguyên tắc:** Cách trích xuất nội dung và cách chunking phải phụ thuộc **loại tài liệu**, thay vì áp dụng máy móc một quy tắc cho toàn bộ corpus. [tr. 11]

## 2.3. Vấn đề khi thu thập trang web

### a. Tải được HTML chưa chắc đã lấy được nội dung cần tìm

Một trang web có thể chứa đồng thời bài viết chính, menu, quảng cáo, gợi ý bài liên quan và những thành phần giao diện khác. Ngoài ra:

- Trang có thể chỉ trả về một thông báo hoặc màn hình chọn ngôn ngữ.
- Tiêu đề có thể bị trích xuất thành tên website thay vì tên bài viết.
- Tác giả, ngày đăng hoặc thông tin khác có thể thiếu hay không khớp bài.
- Phần nội dung chính và những cấu trúc hữu ích có thể bị mất trong quá trình trích xuất.

Do vậy phải xác minh **đã thu đúng bài viết hay chưa**, **đã loại được nội dung không liên quan hay chưa**, và **đã giữ những thành phần cấu trúc quan trọng hay chưa**. [tr. 12]

### b. Ví dụ 1 — Tải được trang nhưng chưa có bài viết

- **Bài đích:** *Nguyên nhân, triệu chứng và cách điều trị chóng mặt*.
- **URL:** <https://www.vinmec.com/vie/bai-viet/chong-mat-vi>.
- **Thời điểm thu thập minh họa:** 11/10/2026.

Kết quả thu thập minh họa ở slide chỉ có tiêu đề mang tên website và một **thông báo chưa có nội dung tiếng Anh, yêu cầu tiếp tục để đọc tiếng Việt**, chứ không phải toàn văn bài viết về chóng mặt. Nếu lập chỉ mục ngay bản thu thập này, hệ thống sẽ không có kiến thức từ bài viết cần tìm, và thậm chí có thể trả về **thông báo website** như thể đó là bằng chứng y khoa. [tr. 13]

### c. Ví dụ 2 — Lấy đúng vùng bài viết và giữ cấu trúc

- **Nguồn minh họa:** Nhà thuốc Long Châu.
- **Bài viết:** *1 bánh trung thu bằng mấy bát cơm? Góc nhìn dinh dưỡng và sức khỏe.*

Các đề mục được nhắc đến:

1. Thành phần dinh dưỡng trong một bánh Trung thu.
2. Bánh nướng và bánh dẻo.
3. Lượng calo trung bình.
4. Các câu hỏi thường gặp.

Cách xử lý phù hợp:

- Trích xuất **vùng nội dung chính**, không lấy toàn bộ nội dung hiển thị trên trang.
- Giữ tiêu đề của mỗi mục cùng các đoạn văn bên dưới.
- Chia chunk theo mục để truy hồi được đúng thông tin cụ thể của bài.

Hai ví dụ cho thấy vấn đề thu thập dữ liệu không chỉ là **truy cập URL thành công**, mà còn phải xác nhận **đúng nội dung, đúng cấu trúc**. [tr. 14]

## 2.4. Phương pháp thu thập và pipeline

### a. Bốn cách thu thập được đề cập

| Phương pháp | Điểm phù hợp/ưu thế | Hạn chế |
|---|---|---|
| **Đọc HTML trực tiếp** | Nhanh, đơn giản đối với trang tĩnh | Không lấy tốt nội dung cần JavaScript để tải/render |
| **API công khai** | Thường trả dữ liệu có cấu trúc rõ ràng | Không phải nguồn nào cũng có API |
| **Trình duyệt tự động** | Phù hợp với trang phải render trước khi có nội dung | Chậm hơn và tiêu thụ nhiều tài nguyên hơn |
| **Sitemap** | Giúp phát hiện/dò danh sách trang | Thường không cung cấp toàn văn bài viết |

**Cách chọn:** Khảo sát cấu trúc và hành vi từng nguồn trước, chọn phương án thu thập phù hợp, rồi mới triển khai trên quy mô lớn. [tr. 15]

### b. Pipeline từ URL thành dữ liệu có thể tìm kiếm

Trình tự xử lý được mô tả trong slide:

```text
Danh sách URL
    ↓
Thu thập nội dung
    ↓
Trích xuất metadata và văn bản
    ↓
Làm sạch và chuẩn hóa
    ↓
Chia đoạn nội dung (chunking)
    ↓
Lập chỉ mục và truy hồi
```

Ý nghĩa của từng bước:

1. **Danh sách URL:** xác định những địa chỉ có khả năng chứa nội dung cần thu thập.
2. **Thu thập nội dung:** lấy phản hồi và nội dung của từng trang bằng phương pháp phù hợp.
3. **Trích xuất:** nhận diện văn bản chính và metadata có ích.
4. **Làm sạch/chuẩn hóa:** loại bỏ thành phần giao diện, chuẩn hóa biểu diễn mà không làm sai nghĩa.
5. **Chunking:** chia nội dung thành đơn vị truy hồi đủ nhỏ và đủ ngữ cảnh.
6. **Lập chỉ mục và truy hồi:** đưa tài liệu/đoạn đã xử lý vào hệ thống tìm kiếm.

Đây là chuỗi phụ thuộc: nếu một bước đầu làm mất dữ liệu quan trọng thì các bước truy hồi phía sau không thể tự khôi phục phần đã mất. [tr. 16, 26]

## 2.5. Chuẩn hóa metadata

### a. Những nhóm metadata cần lưu

| Nhóm metadata | Các trường được đề cập |
|---|---|
| **Mô tả** | Tiêu đề, tóm tắt, chuyên mục, ngôn ngữ |
| **Thông tin nguồn** | URL, tác giả, ngày đăng, ngày cập nhật |
| **Tổ chức nội dung** | Loại tài liệu, nhóm chủ đề |

Dù mỗi website trình bày các trường khác nhau, thông tin nên được đưa về **một cấu trúc thống nhất** để dễ phân loại, phân tích và tìm kiếm. Metadata cho phép nhận diện đặc điểm tài liệu mà chưa cần đọc toàn văn. [tr. 17]

### b. Ví dụ chuẩn hóa đa ngôn ngữ

Ba tiêu đề cùng chủ đề nhưng ở ba ngôn ngữ khác nhau:

| Ngôn ngữ | Tiêu đề gốc |
|---|---|
| VI | Sỏi thận: triệu chứng và chẩn đoán |
| EN | Kidney stones: symptoms and diagnosis |
| ZH | 肾结石：症状与诊断 |

**Giữ nguyên tiêu đề gốc và nhãn ngôn ngữ**; không thay tất cả tiêu đề bằng một bản dịch chung.

Ba cách viết ngày tháng:

```text
15/09/2026
September 15, 2026
2026 年 9 月 15 日
            ↓
       2026-09-15
```

Một số trường cần giữ riêng: **ID, URL, tiêu đề gốc, ngôn ngữ và ngày đăng**. Nếu thiếu tác giả hoặc ngày đăng thì **để trống**, không tự suy đoán để điền vào. [tr. 18]

## 2.6. Làm sạch và chuẩn hóa văn bản

### a. Phân biệt nội dung giao diện và nội dung bài viết

| Ngôn ngữ | Ví dụ phần giao diện cần loại | Ví dụ nội dung cần giữ |
|---|---|---|
| VI | `Trang chủ / Tìm kiếm` | `Sỏi thận` |
| EN | `Home / Search` | `Kidney stones` |
| ZH | `首页 / 搜索 / 相关文章` | `肾结石` |

Ngoài việc bỏ các mục điều hướng, có thể chuyển cách biểu diễn HTML về văn bản thuần:

```text
<p>Sỏi thận</p>         → Sỏi thận
<p>Kidney stones</p>    → Kidney stones
<p> 肾结石 </p>           → 肾结石
```

Tuy nhiên **không được đánh đổi cấu trúc để lấy văn bản phẳng hoàn toàn**: tiêu đề mục, đoạn văn, danh sách và bảng vẫn có giá trị đối với bước truy hồi. [tr. 19]

### b. Làm sạch định dạng nhưng giữ nguyên ý nghĩa

Ba câu có chung ý phủ định:

| Ngôn ngữ | Ví dụ |
|---|---|
| VI | **Không** ghi nhận sốt. |
| EN | **No** fever reported. |
| ZH | 未报告发热 |

Trong y sinh, loại bỏ từ phủ định có thể **đảo ngược thông tin**. Bài trình bày nhấn mạnh những nguyên tắc:

- Không tự ý xóa từ phủ định.
- Không bỏ dấu tiếng Việt.
- Không mặc định xóa những từ thường được coi là ít quan trọng nếu việc đó làm sai nghĩa.
- Giữ nguyên con số và đơn vị đo: **5 mg khác 50 mg**.
- Lưu **văn bản gốc** để có thể đối chiếu khi cần.

**Nguyên tắc cốt lõi:** Chuẩn hóa **cách biểu diễn**, không tự viết lại **nội dung thông tin**. [tr. 20]

## 2.7. Tiền xử lý đa ngôn ngữ

Đối với truy hồi theo từ khóa, cách tách từ có thể khác nhau giữa các ngôn ngữ:

| Ngôn ngữ | Văn bản | Một cách biểu diễn tách từ |
|---|---|---|
| VI | `triệu chứng sỏi thận` | `triệu_chứng \| sỏi_thận` |
| EN | `kidney stones symptoms` | `kidney \| stones \| symptoms` |
| ZH | `肾结石症状` | `肾结石 \| 症状` |

Các lưu ý được đưa ra:

- **Tiếng Việt:** khoảng trắng có thể xuất hiện giữa các *tiếng* của cùng một từ; do đó tách theo dấu cách thuần túy có thể làm vỡ một đơn vị từ vựng.
- **Tiếng Trung:** nhiều từ viết liền, không có khoảng trắng để phân cách.
- **Dense retrieval:** sử dụng tokenizer đi kèm mô hình mã hóa thay vì mặc định dùng cùng cách tách từ của lexical retrieval.

Một khái niệm có thể tương ứng với nhiều cách thể hiện: **sỏi thận ↔ kidney stones ↔ 肾结石**. Đây chính là thách thức và cũng là mục tiêu của truy hồi xuyên ngôn ngữ. [tr. 21]

## 2.8. Chunking và bảo toàn ngữ cảnh

### a. Ba cách chia tài liệu

| Phương pháp | Cách thực hiện/đặc điểm |
|---|---|
| **Theo độ dài** | Cắt theo kích thước quy định; dễ xây dựng và kiểm soát |
| **Theo cấu trúc** | Dựa trên câu, đoạn văn và tiêu đề mục |
| **Theo ngữ nghĩa** | Gom các câu có cùng chủ đề vào một đoạn |

### b. Các đánh đổi cần lưu ý

- **Chunk quá ngắn:** nguy cơ mất ngữ cảnh, khiến đoạn không đủ thông tin để trả lời câu hỏi.
- **Chunk quá dài:** dễ chứa nhiều nội dung không liên quan, khó định vị bằng chứng chính xác.
- **Chunk có overlap:** giúp giữ ngữ cảnh ở ranh giới giữa các đoạn, nhưng tạo phần nội dung lặp.

Vì vậy, chunking không chỉ nhằm giảm độ dài đầu vào; mục tiêu là **xác định được đoạn bằng chứng cụ thể** thay vì trả cả tài liệu dài. [tr. 22]

### c. Ví dụ chia theo đề mục

Một bài viết có hai mục tương ứng trong ba ngôn ngữ:

| Ngôn ngữ | Tiêu đề bài | Mục 1 | Mục 2 |
|---|---|---|---|
| VI | Sỏi thận | Triệu chứng | Chẩn đoán |
| EN | Kidney stones | Symptoms | Diagnosis |
| ZH | 肾结石 | 症状 | 诊断 |

Cách chia hợp lý:

- **Chunk A:** tên mục *Triệu chứng* và những câu thuộc mục này.
- **Chunk B:** tên mục *Chẩn đoán* và những câu thuộc mục này.

Một câu hỏi hỏi về **triệu chứng** cần ưu tiên chunk A, không phải chỉ cần bất kỳ chunk nào cùng nói về sỏi thận. Mỗi chunk phải giữ **ID tài liệu và vị trí nội dung gốc** để có thể truy vết. [tr. 23]

### d. Ví dụ giữ cặp hỏi đáp trong cùng chunk

**Tiếng Việt**

> Hỏi: Sỏi thận là gì?  
> Đáp: Sỏi thận là các khối rắn hình thành trong thận.

**Tiếng Anh**

> Q: What are kidney stones?  
> A: Kidney stones are solid deposits that form in the kidneys.

**Tiếng Trung**

> 什么是肾结石？  
> 肾结石是在肾脏内形成的固体沉积物。

Với tài liệu hỏi đáp, nên giữ **câu hỏi và câu trả lời chung một chunk nếu độ dài cho phép**; tách rời hai phần có thể khiến đoạn mất ý nghĩa khi được truy hồi độc lập. [tr. 24]

## 2.9. Hậu xử lý và kiểm soát chất lượng

### a. Nhận diện bản trùng

Ví dụ hai URL khác nhau có cùng một đoạn văn tiếng Việt:

```text
A: Sỏi thận là các khối rắn hình thành trong thận.
B: Sỏi thận là các khối rắn hình thành trong thận.
```

Hệ thống cần **nhận diện nội dung trùng**, nhưng vẫn **giữ ánh xạ về các ID và URL gốc** để không mất thông tin nguồn.

### b. Cùng chủ đề không có nghĩa là trùng nội dung

Ba nhan đề `Sỏi thận`, `Kidney stones`, `肾结石` có thể nói về cùng khái niệm, nhưng không đủ cơ sở kết luận ba bài là một. **Không xóa tài liệu chỉ vì tiêu đề gần nghĩa hoặc khác ngôn ngữ.**

### c. Kiểm tra chất lượng sau xử lý

Những lỗi cần kiểm tra bao gồm:

- Trang lỗi hoặc trang không chứa bài đích.
- Nội dung rỗng.
- Chunk thiếu ngữ cảnh do chia đoạn sai.
- Nội dung lặp hoặc có nhiều nhiễu.
- Chunk bị mất thông tin nguồn, không thể trả về bằng chứng ban đầu.

### d. Vì sao chất lượng dữ liệu chi phối chất lượng truy hồi?

Bài trình bày chỉ ra bốn quan hệ trực tiếp:

1. **Không thu được bài liên quan → hệ thống không thể tìm ra bài đó.**
2. **Trích xuất thiếu phần quan trọng → bằng chứng tương ứng biến mất.**
3. **Corpus chứa nhiễu/trùng → tìm đúng kết quả trở nên khó khăn.**
4. **Chia đoạn không phù hợp → mất ngữ cảnh của câu trả lời.**

Kết luận của phần dữ liệu: một mô hình mạnh chỉ có ý nghĩa khi đi kèm **kho dữ liệu đủ độ phủ và đủ chất lượng**. [tr. 25–26]

---

# 3. Xây dựng hệ thống baseline

*Phạm vi: trang 27–43; các chi tiết ICD-10 và TF-IDF/BM25 được bổ sung ở hai phụ lục bên dưới.*

## 3.1. Phân nhóm chủ đề bằng ICD-10

### a. Vai trò của ICD-10

**ICD-10** tổ chức các bệnh và vấn đề sức khỏe theo **hệ phân cấp**. Trong ViBioMIR, bộ phân loại này có thể được dùng để tổ chức các nhóm nội dung y sinh trên nhiều ngôn ngữ theo cùng một hệ quy chiếu.

Cách khởi đầu được đề xuất:

- Bắt đầu bằng những **nhóm bệnh lớn**, ví dụ tuần hoàn, hô hấp, nội tiết, tiêu hóa.
- Dùng **tiêu đề, tóm tắt, chuyên mục** để tìm tín hiệu phân nhóm.
- Cho phép **một tài liệu nằm trong nhiều nhóm** khi nội dung của nó liên quan nhiều chủ đề.

Mục tiêu là quản lý và phân tích tài liệu, **không phải tự suy ra chẩn đoán y khoa chỉ từ một tiêu đề**. [tr. 28]

### b. Hai hướng phân nhóm tài liệu

| Tiêu chí | Từ điển và quy tắc | Mô hình ngôn ngữ |
|---|---|---|
| Cách thực hiện | Khớp tên bệnh, tên gọi tương đương và từ đồng nghĩa | So khớp ngữ nghĩa hoặc phân loại văn bản |
| Điểm mạnh | Dễ giải thích, ít phụ thuộc vào dữ liệu đã gán nhãn | Nhận ra được nhiều cách diễn đạt khác nhau |
| Hạn chế | Dễ bỏ sót cách nói mới, từ mơ hồ/nhập nhằng | Có thể gán nhãn sai; cần kiểm tra độ phù hợp với miền y sinh |

Hướng triển khai được khuyến nghị trong slide là **phân nhóm lớn trước, phối hợp nhiều tín hiệu**, và giữ trạng thái **chưa xác định** nếu chưa có bằng chứng để gán nhãn. [tr. 29]

### c. Ví dụ đa ngôn ngữ

| Ngôn ngữ | Tiêu đề |
|---|---|
| VI | Đái tháo đường type 2 |
| EN | Type 2 diabetes |
| ZH | 2 型糖尿病 |

Các tên gọi này đều có thể được nối về nhóm **E00–E90 — bệnh nội tiết, dinh dưỡng và chuyển hóa**.

- Từ điển đa ngôn ngữ hỗ trợ liên kết các cách gọi tương đương.
- Nếu tiêu đề không đủ rõ, cần kiểm tra thêm tóm tắt hoặc nội dung.
- Tài liệu có nhiều chủ đề có thể mang nhiều nhóm.
- **Không suy chẩn đoán:** chỉ có tiêu đề “đau ngực” không đủ để kết luận tài liệu nói về bệnh mạch vành. [tr. 30]

### d. Dùng nhóm ICD-10 cho mục tiêu gì?

1. **Khám phá dữ liệu:** biết nhóm bệnh nào nhiều tài liệu hoặc đang thiếu dữ liệu.
2. **Phân tích kết quả:** xác định những nhóm mà hệ thống truy hồi làm tốt/chưa tốt.
3. **Hỗ trợ tìm kiếm:** ưu tiên ứng viên có nhóm chủ đề liên quan.
4. **Kết nối đa ngôn ngữ:** liên kết các từ/cách gọi thuộc cùng nhóm bệnh.

**Cảnh báo về lọc cứng:** Câu hỏi có thể liên quan nhiều bệnh, còn nhãn phân nhóm có thể sai. Nếu sử dụng ICD-10 để loại bỏ quá mạnh những tài liệu ngoài nhóm dự đoán, hệ thống có thể bỏ sót bằng chứng hữu ích. [tr. 31]

## 3.2. Các nhóm phương pháp truy hồi

Slide chia hệ thống baseline thành bốn nhóm phương pháp chính:

| Nhóm | Ý tưởng cốt lõi | Ví dụ được nhắc đến |
|---|---|---|
| **Lexical retrieval** | So khớp các từ khóa xuất hiện trong truy vấn và tài liệu | TF-IDF, BM25 |
| **Dense retrieval** | So khớp biểu diễn vector ngữ nghĩa của câu hỏi và đoạn văn | mBERT, BGE-M3 |
| **Hybrid retrieval** | Kết hợp kết quả lexical và dense | Hợp thứ hạng, RRF |
| **Reranking** | Đánh giá kỹ hơn một tập ứng viên đã được truy hồi | Cross-encoder |

Mục tiêu của **baseline** không phải lập tức xây dựng hệ thống phức tạp nhất. Nên bắt đầu bằng phương pháp dễ kiểm soát để hiểu dữ liệu và có mốc so sánh; chỉ thêm thành phần mới khi nó giải quyết được **một hạn chế cụ thể**. [tr. 32]

## 3.3. Kiến trúc tổng thể

Luồng truy hồi được trình bày:

```text
                         CÂU HỎI TIẾNG VIỆT
                                 |
                   +-------------+-------------+
                   |                           |
             Lexical retrieval           Dense retrieval
             (tìm theo từ khóa)          (tìm theo ngữ nghĩa)
                   |                           |
                   +-------------+-------------+
                                 |
                    Hợp tài liệu và chunk ứng viên
                                 |
                          Reranking
                 (xếp hạng theo độ liên quan)
                                 |
                 Trả tài liệu và đoạn bằng chứng gốc
```

Hai nhánh truy hồi ban đầu có chức năng **tìm rộng** theo hai kiểu tín hiệu bổ sung nhau. Tập ứng viên hợp nhất được **xếp hạng lại**, cuối cùng trả về thông tin đúng phần hỏi và có thể truy ngược về **nguồn gốc**. [tr. 33]

## 3.4. Lexical retrieval: TF-IDF và BM25

### a. TF-IDF

TF-IDF ưu tiên các từ **xuất hiện nhiều trong một tài liệu nhưng tương đối hiếm trong toàn corpus**.

- **TF (*Term Frequency*):** thể hiện mức xuất hiện của từ trong tài liệu.
- **IDF (*Inverse Document Frequency*):** thể hiện mức độ phân biệt của từ dựa trên độ hiếm trong tập tài liệu.
- Đây là cách khởi đầu **dễ hiểu, dễ triển khai và dễ dùng làm mốc so sánh**.
- Hạn chế được slide lưu ý: chưa xử lý tốt tác động của **độ dài tài liệu** và **việc một từ được lặp đi lặp lại**. [tr. 34]

### b. BM25

So với cách biểu diễn TF-IDF cơ bản, BM25 bổ sung các điều chỉnh:

- **Bão hòa tần suất từ:** một từ xuất hiện thêm quá nhiều lần không làm điểm liên quan tăng mãi theo cùng một mức.
- **Điều chỉnh độ dài tài liệu:** hữu ích khi tài liệu có độ dài khác nhau.
- Vẫn phụ thuộc vào việc từ khóa tương ứng có xuất hiện trong văn bản. [tr. 34]

### c. Điểm mạnh chung của lexical retrieval

- Nhanh, tương đối tiết kiệm tài nguyên.
- Kết quả có khả năng giải thích tốt theo từ khóa khớp.
- Hữu ích khi cần xác định **thuật ngữ hoặc thực thể chính xác**.

### d. Hạn chế chung

- Khó nhận diện **từ đồng nghĩa** hoặc hai cách diễn đạt khác nhau.
- Bất lợi khi truy vấn và tài liệu **khác ngôn ngữ**.
- Không biểu diễn đầy đủ ngữ cảnh hay mục đích của câu hỏi.

Vì thế lexical retrieval vừa là **baseline đơn giản**, vừa là thành phần bổ sung khớp thuật ngữ chính xác trong hệ thống kết hợp. [tr. 35]

## 3.5. Dense retrieval

### a. Nguyên lý

Dense retrieval đưa **truy vấn** và **đoạn văn** về các biểu diễn vector, sau đó tìm những đoạn có vector gần với vector truy vấn.

Ý tưởng là những biểu đạt có nghĩa gần nhau có thể nằm gần nhau trong không gian biểu diễn, cho dù chúng sử dụng từ vựng khác nhau hoặc khác ngôn ngữ.

Ví dụ ba cách nói có thể cùng chỉ một khái niệm:

```text
sỏi thận  ↔  kidney stones  ↔  肾结石
```

Các mô hình được nhắc đến: **mBERT** và **BGE-M3**. [tr. 36]

### b. Điểm mạnh

- Có khả năng so khớp nhiều cách diễn đạt gần nghĩa.
- Có thể hỗ trợ tìm kiếm **xuyên ngôn ngữ**, giảm khoảng cách giữa truy vấn tiếng Việt và tài liệu tiếng Anh/tiếng Trung.

### c. Điểm yếu

- Tốn chi phí mã hóa dữ liệu thành vector và lưu trữ chỉ mục vector.
- Có thể nhầm hai đoạn **gần nghĩa nhưng sai chi tiết** cần hỏi.
- Chất lượng phụ thuộc **mô hình và mức độ phù hợp của mô hình với miền y sinh**.

Lưu ý quan trọng của tài liệu: **mBERT cần có cách tạo vector và phương pháp huấn luyện phù hợp** khi đưa vào retrieval; trong khi **BGE-M3 đã được huấn luyện cho nhiệm vụ retrieval**. Không nên coi việc có sẵn một mô hình ngôn ngữ là đồng nghĩa với có một bộ truy hồi tốt. [tr. 37]

## 3.6. Hybrid retrieval

Hybrid retrieval kết hợp **lexical** và **dense** để tận dụng hai nguồn tín hiệu:

- **Khớp từ khóa chính xác** giúp nhận diện thuật ngữ y khoa, thực thể cụ thể.
- **Khớp ngữ nghĩa** giúp phát hiện các cách biểu đạt khác nhau, bao gồm khác ngôn ngữ.

### Ưu điểm

- Thu thập ứng viên từ cả hai hướng tìm kiếm.
- Giảm phụ thuộc vào duy nhất một cách biểu diễn.
- Phù hợp với dữ liệu y sinh, nơi vừa cần đúng thuật ngữ vừa cần hiểu biến thể ngôn ngữ.

### Hạn chế

- Cần vận hành **nhiều chỉ mục**.
- Phải lựa chọn cơ chế **hợp nhất kết quả**.
- Không bảo đảm cải thiện nếu các nhánh truy hồi tạo ra quá nhiều ứng viên nhiễu.

Hai hướng hợp được nêu trong slide: **RRF (*Reciprocal Rank Fusion*)** — hợp thứ hạng, và **kết hợp điểm sau chuẩn hóa**. Slide không đưa công thức RRF cụ thể. [tr. 38]

## 3.7. Reranking

Reranking nhận một **tập ứng viên tương đối nhỏ** do bước retrieval tìm được, sau đó đánh giá kỹ hơn mức độ phù hợp giữa **câu hỏi** và **từng đoạn**.

Đặc điểm:

- Xem xét câu hỏi và đoạn ứng viên **đồng thời**, thường qua cách tiếp cận **cross-encoder**.
- So khớp ngữ cảnh chi tiết hơn so với việc chỉ dựa vào từ khóa hoặc khoảng cách vector ban đầu.
- Mục tiêu là đưa các đoạn **thực sự trả lời đúng câu hỏi** lên những vị trí đầu.

### Hạn chế

- Chậm và tốn tính toán hơn các bước retrieval ban đầu.
- Thực tế chỉ phù hợp với tập ứng viên nhỏ, không phải toàn bộ corpus.
- **Không thể phục hồi bằng chứng đã bị bỏ sót** ở bước truy hồi ứng viên.

Thông điệp của phần này: **retrieval tìm rộng, reranking chọn kỹ**. Mô hình reranker đa ngôn ngữ được nhắc đến là **BGE-reranker-v2-m3**. [tr. 39]

## 3.8. Ví dụ kết hợp các phương pháp

**Truy vấn tiếng Việt:**

> Triệu chứng của sỏi thận là gì?

Ba ứng viên minh họa:

| Ứng viên | Ngôn ngữ/chủ đề của đoạn | Mức liên quan kỳ vọng |
|---|---|---|
| **A (VI)** | Chẩn đoán sỏi thận | Cùng bệnh, **khác ý hỏi** |
| **B (EN)** | *Symptoms of kidney stones* | **Đúng ý triệu chứng** |
| **C (ZH)** | *肾结石: 症状* | **Đúng ý triệu chứng** |

Vai trò của mỗi phương pháp trong ví dụ:

1. **Lexical:** có thể nhận ra những từ tiếng Việt ở A nhưng bỏ sót B, C vì khác ngôn ngữ.
2. **Dense đa ngôn ngữ:** có thể đưa cả A, B, C vào tập ứng viên nhờ tín hiệu ngữ nghĩa.
3. **Reranker:** cần ưu tiên **B và C**, vì chúng nói về *triệu chứng* — nội dung câu hỏi thực sự yêu cầu — bất chấp khác ngôn ngữ.

Bài học: **Cùng bệnh chưa đủ; phải đúng phương diện của bệnh được hỏi.** [tr. 40]

## 3.9. Từ kết quả truy hồi đến bằng chứng

Sau khi có danh sách ứng viên và điểm liên quan, hệ thống còn phải xử lý đầu ra:

1. **Gom các chunk thuộc cùng tài liệu** để xác định tài liệu nguồn tương ứng.
2. **Loại các chunk bị lặp** do kỹ thuật chia đoạn có overlap.
3. **Chọn những đoạn liên quan đến từng ý** của câu hỏi, đặc biệt khi một truy vấn chứa nhiều yêu cầu.
4. **Trả nội dung gốc và tài liệu nguồn**, giúp đối chiếu kết quả với bằng chứng thực tế.

Đích đến không phải danh sách những văn bản nghe có vẻ liên quan mà là **đúng tài liệu, đúng phần nội dung cần tìm, đúng nguồn**. [tr. 41]

## 3.10. Thiết kế đánh giá thực nghiệm

Để so sánh công bằng giữa TF-IDF, BM25, dense, hybrid và reranking, tài liệu đưa ra bốn yêu cầu:

| Khía cạnh | Cách đánh giá |
|---|---|
| **Điều kiện thí nghiệm** | Cùng corpus, cùng bộ câu hỏi, cùng quy tắc chia đoạn |
| **Chất lượng truy hồi** | Precision, Recall, F2 ở cả cấp **tài liệu** và **chunk** |
| **Chi phí triển khai** | Thời gian tìm kiếm và tài nguyên sử dụng |
| **Phân tích theo nhóm** | Ngôn ngữ tài liệu, nhóm bệnh, loại câu hỏi |

Cần trả lời ba câu hỏi khi thêm một thành phần mới:

- **Cải thiện ở đâu?** Ví dụ cải thiện loại truy vấn hoặc nhóm tài liệu nào.
- **Vẫn sai ở đâu?** Những trường hợp chưa xử lý tốt cần được phân tích riêng.
- **Có đáng với chi phí bổ sung không?** So sánh lợi ích truy hồi với thời gian và tài nguyên bỏ ra.

Chỉ nên chọn kiến trúc phức tạp hơn nếu thí nghiệm cho thấy nó giải quyết **hạn chế có thật** với mức chi phí chấp nhận được. [tr. 42]

---

# 4. Phụ lục A — Danh sách các chương ICD-10

*Phạm vi: trang 45–46. Bảng dưới đây giữ nguyên nhóm mã và phạm vi chủ đề như phần phụ lục của slide.*

| Chương | Mã ICD-10 | Nhóm bệnh/vấn đề sức khỏe |
|---|---|---|
| I | A00–B99 | Nhiễm trùng / ký sinh |
| II | C00–D48 | Khối u |
| III | D50–D89 | Máu / miễn dịch |
| IV | E00–E90 | Nội tiết / chuyển hóa |
| V | F00–F99 | Tâm thần / hành vi |
| VI | G00–G99 | Thần kinh |
| VII | H00–H59 | Mắt |
| VIII | H60–H95 | Tai |
| IX | I00–I99 | Tuần hoàn |
| X | J00–J99 | Hô hấp |
| XI | K00–K93 | Tiêu hóa |
| XII | L00–L99 | Da |
| XIII | M00–M99 | Cơ xương / mô liên kết |
| XIV | N00–N99 | Tiết niệu / sinh dục |
| XV | O00–O99 | Thai sản |
| XVI | P00–P96 | Chu sinh |
| XVII | Q00–Q99 | Dị tật bẩm sinh |
| XVIII | R00–R99 | Triệu chứng / dấu hiệu |
| XIX | S00–T98 | Chấn thương / ngộ độc |
| XX | V01–Y98 | Nguyên nhân bên ngoài |
| XXI | Z00–Z99 | Yếu tố sức khỏe / tiếp xúc dịch vụ |
| XXII | U00–U85 | Mã mục đích đặc biệt |

Tài liệu nhắc đến cách phân lớp này với hai mục tiêu chính: **tổ chức corpus y sinh theo nhóm chủ đề nhất quán** và **phân tích chất lượng truy hồi theo từng nhóm**. Không nên sử dụng nhãn suy ra từ văn bản như một kết luận chẩn đoán. [tr. 28–31, 45–46]

---

# 5. Phụ lục B — Ôn tập TF-IDF và BM25

*Phạm vi: trang 47; liên hệ với trang 34–35.*

## 5.1. Công thức trọng số TF-IDF

Phụ lục biểu diễn trọng số của từ $t$ đối với tài liệu $d$ như sau:

$$
w_{t,d}=\operatorname{tf}(t,d)\,\operatorname{idf}(t).
$$

Trong đó:

- $\operatorname{tf}(t,d)$ cho biết mức độ xuất hiện của từ $t$ trong tài liệu $d$.
- $\operatorname{idf}(t)$ thể hiện mức độ phân biệt của từ đó trên toàn corpus.
- Có thể **so sánh biểu diễn tài liệu/truy vấn bằng cosine**, như phụ lục đề cập.

**Lưu ý:** PDF đưa ra công thức trọng số dạng tổng quát trên, không nêu cách định nghĩa TF/IDF chi tiết, cũng không trình bày công thức cosine cụ thể.

## 5.2. BM25 điều chỉnh gì?

Phụ lục liệt kê ba ý:

1. Vẫn sử dụng **mức độ hiếm của từ** trong corpus.
2. **Giảm lợi ích biên của việc lặp một từ quá nhiều lần** trong tài liệu.
3. **Chuẩn hóa theo độ dài tài liệu** để tránh so sánh lệch khi văn bản có độ dài khác nhau.

Điểm khác biệt được nhấn mạnh là BM25 có cơ chế **bão hòa tần suất từ** và **điều chỉnh độ dài**, thay vì chỉ sử dụng các trọng số TF-IDF cơ bản. **PDF không cung cấp công thức tính điểm BM25 đầy đủ.**

---

# 6. Tổng kết và các nguyên tắc xuyên suốt

*Phần này hệ thống hóa các kết luận từ trang 16, 26, 33, 41–43, không thêm đề xuất mô hình ngoài tài liệu.*

## 6.1. Ba điểm quan trọng nhất

Bài trình bày kết lại bằng ba trụ cột:

1. **Dữ liệu:** phải thu thập đủ và giữ đúng nội dung trước khi triển khai tìm kiếm.
2. **ICD-10:** cung cấp cách phân nhóm chủ đề để hiểu thành phần của corpus và xác định hệ thống hoạt động tốt/chưa tốt trên nhóm bệnh nào.
3. **Phương pháp:** **lexical** khớp từ, **dense** khớp nghĩa, **hybrid** kết hợp hai kiểu truy hồi và **reranker** lựa chọn ứng viên kỹ hơn. [tr. 43]

## 6.2. Luồng nhìn xuyên suốt từ dữ liệu thô tới bằng chứng

```text
4.394.718 URL thuộc 97 host
               |
               v
    Khảo sát cấu trúc từng nguồn
               |
               v
       Thu thập nội dung thật
               |
               v
    Trích xuất bài viết + metadata
               |
               v
   Làm sạch, chuẩn hóa, kiểm tra
               |
               v
    Chia chunk giữ ngữ cảnh, nguồn
               |
               v
     Gán/phân tích nhóm ICD-10
               |
               v
           LẬP CHỈ MỤC
        /               \
    Lexical            Dense
        \               /
         Hợp tập ứng viên
               |
               v
            Reranking
               |
               v
   Gộp chunk, loại trùng đầu ra
               |
               v
   Tài liệu + đoạn bằng chứng gốc
               |
               v
     Đánh giá P / R / F2 macro
   ở cấp document và cấp chunk
```

Sơ đồ này **ghép hai quy trình đã xuất hiện riêng trong slide** (xử lý dữ liệu tại trang 16 và truy hồi tại trang 33) cùng các bước gán nhóm, hậu xử lý và đánh giá được trình bày ở những trang khác; đây không phải một sơ đồ nguyên bản duy nhất của PDF.

## 6.3. Các nguyên tắc để ghi nhớ khi thực hiện ViBioMIR

| Giai đoạn | Điều cần bảo đảm | Rủi ro nếu bỏ qua |
|---|---|---|
| Thu thập | Có nội dung bài thực, không chỉ trạng thái truy cập URL thành công | Thiếu kiến thức cần truy hồi |
| Trích xuất | Nhận diện đúng nội dung chính, bảo toàn đề mục và bảng | Chỉ mục chứa menu/thông báo hoặc mất cấu trúc |
| Chuẩn hóa | Thống nhất biểu diễn nhưng không thay đổi nghĩa | Sai thông tin y khoa, sai số/đơn vị hoặc phủ định |
| Đa ngôn ngữ | Tách từ và biểu diễn phù hợp từng ngôn ngữ | Bỏ sót tài liệu vì khác từ ngữ |
| Chunking | Đoạn đủ ngữ cảnh, có vị trí và ID nguồn | Không định vị được bằng chứng |
| Khử trùng | Nhận diện trùng thật và giữ ánh xạ URL/ID | Lặp nội dung hoặc đánh mất nguồn |
| Phân nhóm ICD-10 | Gán nhãn thận trọng, cho phép nhiều nhóm | Lọc nhầm tài liệu có ích |
| Truy hồi | Cân nhắc lexical, dense, hybrid và reranking | Chỉ tìm đúng bệnh nhưng sai ý cần hỏi |
| Đầu ra | Chọn đoạn trả lời đúng và trả bằng chứng nguyên gốc | Thiếu tính truy vết |
| Thực nghiệm | Cùng điều kiện đánh giá, xét cả chất lượng và chi phí | Không biết cải tiến thực sự có hiệu quả không |

**Thông điệp cuối cùng:** Chọn phương pháp theo **đặc điểm thực tế của dữ liệu** và **hạn chế cần giải quyết**, rồi **kiểm chứng bằng thực nghiệm**. Một hệ thống truy hồi tốt phải kết hợp chất lượng corpus, khả năng khớp xuyên ngôn ngữ, lựa chọn đúng đoạn trả lời và khả năng đối chiếu về nguồn. [tr. 43]

---

# 7. Tài liệu tham khảo được liệt kê trong slide

*Trang 48 liệt kê các nhóm nguồn tham khảo, không kèm đường dẫn cụ thể cho hầu hết mục.*

1. **ViBioMIR:** README và báo cáo kiểm kê dữ liệu.
2. **Cuộc thi R2AI2026:** mô tả nhiệm vụ và phương pháp đánh giá.
3. **WHO ICD-10:** hệ phân loại bệnh và vấn đề sức khỏe.
4. **Introduction to Information Retrieval:** kiến thức về lexical retrieval.
5. **Model cards:** mBERT, BGE-M3 và BGE-reranker.
6. **Nguồn thuật ngữ y sinh minh họa:** NIDDK, MSD Manuals và WHO.

Các mục trên được giữ theo cách liệt kê của PDF; **không bổ sung liên kết hoặc thông tin thư mục chưa có trong tài liệu gốc**.

---

# 8. Bảng đối chiếu phạm vi trang PDF

Bảng này giúp kiểm tra rằng nội dung của **toàn bộ 48 trang** đã được đưa vào phần tổng hợp hoặc được ghi nhận là trang chuyển mục.

| Trang PDF | Nội dung gốc | Nơi được tổng hợp |
|---|---|---|
| 1–2 | Trang bìa, thông tin người trình bày, mục lục | Phần đầu tài liệu và mục lục |
| 3 | Trang mở đầu: Tổng quan bài toán | Mục 1 |
| 4 | Một truy vấn, nhiều ngôn ngữ | 1.1 |
| 5 | Đánh giá ở hai cấp độ; Precision, Recall, F2 | 1.2 |
| 6 | Ví dụ tính điểm F2 | 1.2 |
| 7 | Tiêu chí chunk đúng, chuẩn hóa và LCS | 1.3 |
| 8 | Trang mở đầu: Dữ liệu ViBioMIR | Mục 2 |
| 9 | Thống kê corpus và tập truy vấn | 2.1 |
| 10 | Phân bố URL theo host | 2.1 |
| 11 | Các loại tài liệu trong corpus | 2.2 |
| 12 | Khó khăn khi trích xuất website | 2.3 |
| 13 | Ví dụ trang Vinmec chưa thu được bài viết | 2.3 |
| 14 | Ví dụ chọn vùng bài viết Long Châu | 2.3 |
| 15 | HTML, API, browser automation, sitemap | 2.4 |
| 16 | Pipeline từ URL tới truy hồi | 2.4, 6.2 |
| 17 | Các nhóm metadata | 2.5 |
| 18 | Metadata đa ngôn ngữ, chuẩn hóa ngày | 2.5 |
| 19 | Loại bỏ giao diện, làm sạch HTML | 2.6 |
| 20 | Bảo toàn phủ định, số và đơn vị | 2.6 |
| 21 | Tách từ Việt – Anh – Trung | 2.7 |
| 22 | Cách chunking và những đánh đổi | 2.8 |
| 23 | Chunking theo đề mục | 2.8 |
| 24 | Giữ câu hỏi và câu trả lời chung đoạn | 2.8 |
| 25 | Khử trùng lặp, giữ ánh xạ nguồn | 2.9 |
| 26 | Vì sao dữ liệu quyết định kết quả | 2.9, 6 |
| 27 | Trang mở đầu: Xây dựng baseline | Mục 3 |
| 28 | Nhóm chủ đề ICD-10 | 3.1 |
| 29 | Phân nhóm bằng quy tắc và mô hình | 3.1 |
| 30 | Ví dụ ICD-10 đa ngôn ngữ | 3.1 |
| 31 | Ứng dụng và rủi ro lọc cứng ICD-10 | 3.1 |
| 32 | Bốn nhóm phương pháp baseline | 3.2 |
| 33 | Sơ đồ hệ thống truy hồi | 3.3, 6.2 |
| 34 | TF-IDF và BM25 | 3.4 |
| 35 | Ưu, nhược điểm của lexical | 3.4 |
| 36 | Nguyên lý dense retrieval | 3.5 |
| 37 | Ưu, nhược điểm của dense | 3.5 |
| 38 | Hybrid, RRF và hợp điểm | 3.6 |
| 39 | Reranking và BGE-reranker-v2-m3 | 3.7 |
| 40 | Ví dụ so sánh ứng viên VI/EN/ZH | 3.8 |
| 41 | Gộp, loại lặp và xuất bằng chứng | 3.9 |
| 42 | Đánh giá thực nghiệm có kiểm soát | 3.10 |
| 43 | Ba điểm cần nhớ, thông điệp cuối | 6 |
| 44 | Trang kết thúc: Cảm ơn và trao đổi | Được ghi nhận tại đây (không có nội dung kỹ thuật mới) |
| 45–46 | Các chương ICD-10 từ I đến XXII | 4 |
| 47 | Phụ lục TF-IDF và BM25 | 5 |
| 48 | Danh mục tài liệu tham khảo | 7 |

---

*Bản Markdown này là **bản tổng hợp diễn giải có tổ chức** từ PDF đính kèm, không phải bản chép nguyên từng dòng của slide. Số liệu, thuật ngữ, công thức, ví dụ và các giới hạn thông tin được giữ theo tài liệu gốc; phần diễn giải chỉ nhằm liên kết và làm rõ mạch nội dung.*
