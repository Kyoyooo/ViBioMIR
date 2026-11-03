# Mẫu review nhỏ: 1 query, 3 trường hợp

**Trạng thái: nhãn nháp do trợ lý đề xuất, chưa phải GT hoặc kết quả Qwen.**

**Query 5:** Cách phòng tránh nguy cơ lây nhiễm bệnh lao đa kháng là gì?

Ba trường hợp được chọn để minh họa cách review, không phải mẫu ngẫu nhiên để ước lượng chất lượng. Đây là kiểm tra sự liên quan và ranh giới văn bản, không xác nhận nội dung y khoa.

Nếu query này thuộc test, chỉ dùng làm minh họa theo phạm vi BTC cho phép; không biến nhãn mẫu thành tập tune. GT chính thức nên dùng dữ liệu phát triển được phép.

## Cách dùng

Đọc nhận xét trước. Nếu muốn kiểm tra, mở phần “Chunk gốc”. Với mỗi trường hợp chọn: **đồng ý / không đồng ý / chưa đủ cơ sở** và ghi lý do. Không cần đếm ký tự hoặc tự tính LCS.

## Trường hợp 1: doc 43339, chunk xuất số 0

**Nhận xét sơ bộ:** Chỉ là tiêu đề; có từ khóa lao và phòng tránh nhưng chưa đưa ra nội dung trả lời. Không suy ra cả tài liệu không liên quan: cùng doc còn có các chunk khác.

**Câu cần xác nhận:** Loại chunk tiêu đề này khỏi evidence?

**Độ dài:** gốc 29 ký tự; phần đề xuất 28 ký tự. Ngắn hơn không chứng minh điểm tốt hơn.

**Phần văn bản đang xem xét:**

```text
Bệnh lao và cách phòng tránh
```

<details>
<summary>Xem nguyên chunk gốc</summary>


```text
Bệnh lao và cách phòng tránh

```

</details>

**Người review:** chưa có. **Quyết định:** chưa gán.

## Trường hợp 2: doc 538809, chunk xuất số 3

**Nhận xét sơ bộ:** Đoạn con giữ phần văn bản nhắc kháng thuốc và lây truyền. Chunk gốc còn có các mục khác. Đây là nhận xét về độ tập trung của văn bản, chưa xác nhận tính đúng y khoa hoặc khớp đáp án BTC.

**Câu cần xác nhận:** Đoạn con tập trung hơn và vẫn đủ ngữ cảnh so với chunk gốc?

**Độ dài:** gốc 1075 ký tự; phần đề xuất 352 ký tự. Ngắn hơn không chứng minh điểm tốt hơn.

**Phần văn bản đang xem xét:**

```text
9. Tuân thủ dùng thuốc (không bao giờ tự ý dừng thuốc)
Khi người bệnh không tuân thủ điều trị, vi khuẩn lao có thể trở nên nên kháng thuốc. Khi các vi khuẩn kháng thuốc này bắn vào không khí, chúng có thể lây bệnh cho những cá nhân khỏe mạnh. Điều này sẽ dẫn tới tăng số người bị bệnh lao đa kháng thuốc. Do vậy hãy uống thuốc theo chỉ định của bác sĩ.
```

<details>
<summary>Xem nguyên chunk gốc</summary>


```text
Vì phòng bệnh luôn tốt hơn chữa bệnh, nên cách tốt nhất là bạn nên đưa con đi tiêm vắc-xin Bacillus Calmette-Guerin (BCG) để phòng chống bệnh lao. Cách này sẽ đảm bảo phát triển các kháng thể chống Mycobacterium tuberculosis và do vậy làm giảm nguy cơ mắc bệnh lao.
9. Tuân thủ dùng thuốc (không bao giờ tự ý dừng thuốc)
Khi người bệnh không tuân thủ điều trị, vi khuẩn lao có thể trở nên nên kháng thuốc. Khi các vi khuẩn kháng thuốc này bắn vào không khí, chúng có thể lây bệnh cho những cá nhân khỏe mạnh. Điều này sẽ dẫn tới tăng số người bị bệnh lao đa kháng thuốc. Do vậy hãy uống thuốc theo chỉ định của bác sĩ.
10. Tránh xa căng thẳng
Bạn có thể không nhận thức được rằng stress làm tăng nguy cơ mắc nhiều bệnh trong đó có bệnh lao. Do vậy, bạn nên tránh xa mọi căng thẳng.
11. Ngủ đủ giấc hàng ngày
Mất ngủ không phải là chuyện hiếm gặp trong cuộc sống hiện đại. Tuy nhiên, việc ngủ đủ giấc là rất quan trọng để duy trì sức khỏe và ngăn ngừa bệnh tật. Hãy tránh xa những thực phẩm cản trở giấc ngủ, đồng thời cố gắng ăn những thực phẩm giúp phục hồi chu kì thức ngủ.
```

</details>

**Người review:** chưa có. **Quyết định:** chưa gán.

## Trường hợp 3: doc 511609, chunk xuất số 5

**Nhận xét sơ bộ:** Đoạn con chứa trực tiếp cụm lao đa kháng thuốc và một biện pháp được tài liệu mô tả. Cần người review xác nhận đủ ngữ cảnh và phạm vi câu hỏi; không mặc định đây là gold đầy đủ.

**Câu cần xác nhận:** Đoạn này hỗ trợ trực tiếp ít nhất một phần câu hỏi?

**Độ dài:** gốc 1441 ký tự; phần đề xuất 214 ký tự. Ngắn hơn không chứng minh điểm tốt hơn.

**Phần văn bản đang xem xét:**

```text
Giảm tiếp xúc nguồn lây bằng biện pháp cách ly, nên có nơi chăm sóc và điều trị riêng cho người bệnh lao phổi có kết quả xét nhiệm AFB (acid fast bacillus) dương tính, đặc biệt đối với người bệnh lao đa kháng thuốc
```

<details>
<summary>Xem nguyên chunk gốc</summary>


```text
Sử dụng phương tiện phòng hộ cá nhân cho nhân viên y tế là điều bắt buộc, lưu ý khẩu trang thông thường ít có tác dụng bảo vệ phòng nhiễm vi khuẩn lao, ở những nơi có nguy cơ lây nhiễm cao cần mang khẩu trang bảo vệ hô hấp đạt tiêu chuẩn như loại khẩu trang N95 hoặc tương đương trở lên. Giảm tiếp xúc nguồn lây bằng biện pháp cách ly, nên có nơi chăm sóc và điều trị riêng cho người bệnh lao phổi có kết quả xét nhiệm AFB (acid fast bacillus) dương tính, đặc biệt đối với người bệnh lao đa kháng thuốc; trong các cơ sở đặc biệt như trại giam, cơ sở cai nghiện, cơ sở giáo dục, trường giáo dưỡng, cơ sở bảo trợ xã hội có khả năng lây nhiễm rất cao nên cần cách ly tuyệt đối những người bệnh để điều trị mới tránh được các vụ dịch nghiêm trọng; nhân viên y tế cần tuân thủ quy trình khám bệnh và chăm sóc người bệnh, khi khám, hỏi bệnh, thực hiện tư vấn nên để người bệnh quay lưng lại, sự biểu hiện sự thân thiện qua hành động, cử chỉ, lời nói chứ không nhất thiết phải tiếp xúc trực tiếp; để bảo vệ cho người nhiễm HIV đến khám bệnh, cần xác định những người nghi lao qua ho khạc để hướng dẫn họ dùng khẩu trang, giấy che miệng, chuyển đến khu chờ riêng hoặc phòng cách ly nếu có và ưu tiên khám trước để giảm thời gian tiếp xúc.
Phòng bệnh lao bằng giảm nguy cơ nhiễm vi khuẩn lao và nguy cơ chuyển từ nhiễm lao sang bệnh lao
Giảm nguy cơ chuyển từ nhiễm lao sang bệnh lao được thực hiện bằng cách tiêm vắcxin BCG và điều trị lao tiềm ẩn. 
```

</details>

**Người review:** chưa có. **Quyết định:** chưa gán.

## Giới hạn

Chỉ đối chiếu với chunk trong submission; chưa có region/version toàn văn. Offsets trong JSON tính theo ký tự Python trong parent chunk. Không phải source offsets của corpus. Mẫu này chưa dùng để tính F2, chưa xác nhận tính đúng y khoa và chưa phải human gold.

File máy đọc: [DRAFT_REVIEW.json](DRAFT_REVIEW.json).
