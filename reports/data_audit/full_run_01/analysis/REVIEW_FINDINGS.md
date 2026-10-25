# Source-excerpt review findings

Reviewer: assistant. These are structural/data-quality observations, not human-expert clinical validation.
Reviewed 26 deliberately selected cases across 20 domains. They are not a random error-rate sample.

The 600-record domain-stratified random packet and its targeted examples remain available for independent human review.
No population error-rate, clinical accuracy or 98% main-content QA claim is made from these cases.

## thaythuocvietnam.vn — keep_main_content_trim_ui

Record: `eea96343edfc3426d9126c63daf0b11014aba5ec814585177ac175649efd875a`.
Locator: `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714570674-056.parquet`, row group 0, row offset 184.

The complete short source is a webcast announcement followed by comment/login forms and related titles. Event time and featured expert are not automatically publication date or author.

Remove validated comment/form/related blocks only; tag event/announcement as a type candidate.

Source excerpt (original language):

> Bảo vệ sớm tim – thận trên bệnh nhân đái tháo đường typ 2: Vì sao cần tuân thủ điều thủ điều trị
> Trang chủ
> Chia sẻ trực tuyến
> Bảo vệ sớm tim – thận trên bệnh nhân đái tháo đường typ 2: Vì sao cần tuân thủ điều thủ điều trị
> Bảo vệ sớm tim – thận trên bệnh nhân đái tháo đường typ 2: Vì sao cần tuân thủ điều thủ điều trị
> Mời quý độc giả đón xem phiên 

## thaythuocvietnam.vn — preserve_medical_body_review_repeats

Record: `84283375385a3e9a5e7a9a7522b6c47a19182f8bccd840388f41390d0ea622e9`.
Locator: `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714570674-056.parquet`, row group 0, row offset 21.

Head/tail show numbered medical sections and an explicit end byline. Repetition alone does not distinguish a contents list from medical instructions.

Keep body and byline source; compare repeated block positions before deduplication.

Source excerpt (original language):

> 12 cách chữa viêm da cơ địa tại nhà hiệu quả, an toàn, tiết kiệm
> Viêm da cơ địa từ lâu đã trở thành nỗi ám ảnh do tính chất bệnh dai dẳng, gây bí bách và mất thẩm mỹ cho nhiều người. Đây là bệnh lý thuộc về cơ địa nên việc điều trị gặp nhiều khó khăn. Chữa viêm da cơ địa bằng dân gian là cách được nhiều người áp dụng do cho hiệu quả, an toàn và làn

## thaythuocvietnam.vn — quarantine_binary_as_text

Record: `087eb9b938bdb2a53f38f4909df9f75ba1599519f36a11c8bb0b542f71d78201`.
Locator: `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714570674-056.parquet`, row group 0, row offset 55.

Final URL ends in .jpg and the text excerpt is binary-like replacement characters, not recoverable article prose.

Classify payload/content failure rather than guessing Unicode repairs or stripping arbitrary characters.

Source excerpt (original language):

> ���nw�����w��'hfU���8ּZK�q*���]�hihj7@$-u����/:O�f��1�f{���eH�R��!��
> �4Zu�ҙ�ni��iV1ъO��RF>D�d�c�����U�j�������"���.K�*�&�-���m
> �&,(��&�;K�?�o'M��H���k֎�������I1d$I�0��h��X�ASomsZ��+r0Ǻ�����3���ۙ�է
> �-�G*�6�s)m7=��"�4�e�͟0���9@wMٞ��ŭ���:m��n����4���wF�;��K�d�
> ��Xg�B�g��_A����M%�u���!����;QP�=h�(i��!4S锲�>���S����u��x���ھ�|��j��\9ϒ1�3CC
> �Fb\��g-� ?���Z

## cnkang.com — mismatch_flag_false_positive_in_this_case

Record: `f7bf70c4d9f12bf013bdfc2765ee2f3eb634edc9b7c13a425898948c4b3142a4`.
Locator: `ViBioMIR/shards/C/cnkang.com/part-1792716526532-236.parquet`, row group 0, row offset 41.

Chinese title and body both discuss Helicobacter pylori; exact whole-word overlap misses shared substrings in unsegmented Chinese.

Use CJK-aware comparison and review; never discard using this mismatch heuristic alone.

Source excerpt (original language):

> 胃炎
> 幽门螺杆菌（H pylori，Hp）感染与多种疾病相关，包括胃炎及胃腺癌。关于 Hp 清除是否可预防癌症，还是仅降低其发病风险，目前还无明确的结论。此外，有研究提出幽门螺杆菌感染对机体还可能有一定益处（如降低肥胖或儿童哮喘的风险等），但证据也尚不充分。四川省第四人民医院消化内科常玉英
> 近期发表于 Gastroenterology 的一篇综述，回顾了 Hp 感染带来的影响，并对清除 Hp 带来的益处和挑战进行了总结。
> 一、Hp：胃癌的首要病因
> Hp 感染是胃癌的首要病因，但该细菌本身还不足以引发胃癌，需要其他因素的参与，且 Hp 并不是胃癌的唯一病因，Epstein-Barr 病毒感染、宿主基因异常、自身免疫性胃炎以及食管腺癌相关近端癌症等，也占了胃腺癌的 3%-5%。因此，即使不存在 

## vov.vn — whole_document_block_flag_false_positive

Record: `1b6adc8a2320bdcd583b8e6abcc273b7f0d46545957bbdfe7bc70d2ceef5a4fb`.
Locator: `ViBioMIR/shards/A/vov.vn/part-1792716842933-706.parquet`, row group 0, row offset 32.

A substantive VOV article is followed by an explicit reCAPTCHA/Privacy Policy footer and related stories.

Retain article; remove verified footer regions instead of rejecting the document.

Source excerpt (original language):

> Cứu sống bé sơ sinh bị thủy đậu toàn thân
> VOV.VN - Chiều 9/6, lãnh đạo Khoa hồi sức tích cực chống độc nhi, Bệnh viện đa khoa trung tâm Tiền Giang cho biết, bệnh viện vừa cứu sống 1 bé trai sơ sinh bị viêm phổi nặng trên người bệnh nhân có nhiều thủy đậu.
> Mới đây, bệnh viện đa khoa trung tâm Tiền Giang tiếp nhận điều trị 1 bé trai 13 ngày tuổi, ở x

## thaythuocvietnam.vn — quarantine_binary_as_text

Record: `98827955a5a900b98d8e2bcf305051c3a6fde87bb04294c794251d35d6ef5d85`.
Locator: `ViBioMIR/shards/A/thaythuocvietnam.vn/part-1792714909666-041.parquet`, row group 0, row offset 226.

Excerpt includes Exif/Adobe markers and image-like undecodable bytes. A tag-regex hit is insufficient to diagnose ordinary HTML contamination.

Check binary payload/redirect evidence before generic HTML cleanup.

Source excerpt (original language):

> ����ExifII*��Duckyd��ohttp://ns.adobe.com/xap/1.0/ ��Adobed�������X���
> !1AQa" q��2��B#��R$�b3�r��CS%7
> �c45&v�'���s��DE�deuF���9y !1AQaq"��2��B#���Rb3 ��r$����Cs�5�Sc��4t�%u�67҃�Td�&�D��U��Vv�'8��?����� ����p�9��t���8�S���픨`���)6��B�7$U#��.I����Y�TcU]n�3�=+�X��i?��פ�I#�ZV�HWx��G#��+�0E��9.l��`S�N��;�ہ�j�x�%������m��/v5/�1�u�qU�)��H�+MJd�
> >$�R�I<���_

## vinmec.com — keep_with_footer_review

Record: `8b39ca1e996cc4ec6c2f0ca1579f2b8090f613a29817e6114d9ea9a8f3bea728`.
Locator: `ViBioMIR/shards/B/vinmec.com/part-1792716882112-410.parquet`, row group 0, row offset 831.

Headings and article paragraphs exist; tail has reference and appointment/service boilerplate. Some list introductions have no obvious following list in the excerpts.

Preserve reference information; isolate booking UI; review suspected content gaps without inventing omitted text.

Source excerpt (original language):

> Băng bó tay sau khi bị thương
> Những chấn thương gây ra vết thương ở tay cần được băng bó đúng cách nhằm hạn chế tình trạng sưng nề và bảo vệ xương khớp trong một số trường hợp có tổn thương nặng. Việc băng bó tay sau chấn thương đúng cách là biện pháp hạn chế những tổn thương phát sinh khi di chuyển tới cơ sở y tế trong một số trường hợp.
> 1. Khi nà

## 120ask.com — keep_short_qa

Record: `ff8713219b82213c04b139f4d81627cdd8ba407cebb6f34309206128ef3f849b`.
Locator: `ViBioMIR/shards/B/120ask.com/part-1792685726879-367.parquet`, row group 0, row offset 432.

The 198-character source has a question, clinical context and answers, separated by a standalone complaint-control block. Nonempty stored HTML is available.

Keep Q&A together where budget permits; remove only verified standalone controls and inspect local HTML metadata.

Source excerpt (original language):

> 睡眠脑电图．心理行为评估当天有结果吗？
> 健康咨询描述： 问题描述:儿子两岁八个月了只会说少量简单的双语,如爸爸妈妈.医生建议做心理评估和睡眠脑电图.一般当天可以有结果吗?
> 投诉
> 你好.可以有结果的.
> 做心理评估和睡眠，脑电图测试，这个当天就会出来结果的做完结果就会出来的，宝宝语言发育延迟，这个主要是看宝宝的状态，是否你说什么都能够听懂和动手的能力怎么样？如果这些都正常的话，那么就是多跟宝宝沟通

## cnkang.com — keep_article_remove_related_feed

Record: `f40f20b28ceb6ea74bb9110df9246df4378111363a8fa89ab61eb11d694add3a`.
Locator: `ViBioMIR/shards/C/cnkang.com/part-1792716526532-236.parquet`, row group 0, row offset 803.

Clinical paragraphs and numbered points are followed by a long list of related article titles, including an explicit related-articles marker.

Choose a reviewed article boundary; do not treat all trailing medical-looking titles as evidence paragraphs.

Source excerpt (original language):

> 2型糖尿病
> 哪些是2型糖尿病的症状
> 一担出现了很多的症状就会出现很多的2型糖尿病，这种症状的出现会给患者带来很大的伤害，患者要特别注意症状的出现，那么，哪些特殊症状是2型糖尿病表现?大家一起来了解了解。
> 1、疲劳、体重减轻、虚弱。由于代谢失常，能量利用减少，氮负平衡，失水和电解质，酮症时更严重，患者感疲乏、虚弱无力。以上三点是2型糖尿病的症状中最极易常见的特点，如果知道了2型糖尿病的症状有哪些，就可以直接及时发现；
> 2、多尿、烦渴、多饮，这是2型糖尿病的症状中的三多现象；
> 3、善饥多食；
> 4、视力下降。糖尿病可引起白内障，导致视力下降，进展较快，有时也会引起急性视网膜病变，引起急性视力下降。2型糖尿病的症状有些是明显的，但是有些是不明显的，所以大家要对2型糖尿病的症状引起特别的关注。
> 出现以

## pubmed.ncbi.nlm.nih.gov — exclude_empty_from_content_index

Record: `640e5ca99007fd45937569bca8eb4f73beb9401169de60e8a4ee9255203a4dd8`.
Locator: `ViBioMIR/shards/C/pubmed.ncbi.nlm.nih.gov/part-1792716468718-977.parquet`, row group 0, row offset 35.

Selected record has source identity/status metadata but no title, text, blocks or retained HTML.

Retain availability/provenance and exclude from current content retrieval; acquisition remains deferred.

Source excerpt (original language):

> 

## suckhoecongdongonline.vn — retain_source_type_uncertain

Record: `5f9e0f469abcbaea7699dfe7bcf5ce0740046142754a84b6869ab770a070fb3c`.
Locator: `ViBioMIR/shards/C/suckhoecongdongonline.vn/part-1792716656215-451.parquet`, row group 0, row offset 0.

The excerpt combines exercise/lifestyle content with a vehicle feature. Main text exists; this is not evidence of a failed crawler.

Tag promotional/lifestyle type only with evidence; do not exclude an entire source or infer relevance from domain.

Source excerpt (original language):

> 05 mẹo nhỏ cùng xế yêu duy trì mục tiêu rèn luyện sức khỏe Tết Tân Sửu 2021
> Theo thông tin tổng hợp từ Strava - nền tảng xã hội về sức khỏe và thể thao, ngày 19/1 là mốc thời gian phần lớn mọi người từ bỏ kế hoạch rèn luyện sức khỏe được đặt ra vào đầu năm bởi vô số lý do. Trong khi đó, những chủ xe Ranger có thể tận dụng chính chiếc ô tô của mình 

## suckhoedoisong.vn — retain_with_sponsorship_metadata

Record: `920fd9fa7f4ca1de0e45493afd0a5bc35d46e1379b6486c9a6090a1d47e7425e`.
Locator: `ViBioMIR/shards/C/suckhoedoisong.vn/part-1792716664019-939.parquet`, row group 0, row offset 0.

Article includes a product-oriented nutrition discussion and explicit sponsored-content marker.

Record sponsorship marker separately where present; do not relabel all source articles as sponsored or remove clinical cautions.

Source excerpt (original language):

> 03 phương pháp giúp hỗ trợ bé ăn dặm tăng cân đều đặn mẹ bỏ túi ngay
> Sau 6 tháng tuổi, dinh dưỡng từ sữa mẹ đã không còn đủ cho nhu cầu hàng ngày, đây là lúc trẻ cần được chuyển sang chế độ ăn dặm.
> Tuy nhiên trong giai đoạn này trẻ rất hay gặp phải tình trạng biếng ăn, chậm tăng cân do nhiều nguyên nhân. Cùng JapiFoods tham khảo phương pháp để hỗ t

## thanhnien.vn — preserve_numbers_and_related_link_boundary

Record: `68e25d85ecd8d42a3cdf5096244bb3c2310096ef21b1e013aa96f11a61aa1240`.
Locator: `ViBioMIR/shards/B/thanhnien.vn/part-1792716862874-444.parquet`, row group 0, row offset 0.

Short source has quantitative units and a byline, followed by related links prefixed with >>.

Preserve literal numbers/units, even unusual original claims; strip only verified related-link spans, never medically rewrite evidence.

Source excerpt (original language):

> 1-2 mg độc tố cá nóc có thể gây chết người
> Cá nóc độc tố rất mạnh gây ngộ độc, tử vong. Độc tố có trong cá nóc là Tetrodotoxin, tập trung nhiều ở gan, thận, tụy, cơ quan sinh sản (buồng trứng, túi tinh), mắt, mang, da, máu. Độc tính của độc tố tăng mạnh vào mùa sinh sản của cá (từ tháng 2 đến tháng 7).
> Độc tố này không có trong thịt cá nóc, nhưng v

## laodong.vn — retain_health_news

Record: `d43982ebded08703a815df23f915db1f5f310784efadf6dc673c7ff499027c63`.
Locator: `ViBioMIR/shards/A/laodong.vn/part-1792714418848-545.parquet`, row group 0, row offset 0.

Article concerns hospital service costs and policy; it contains body sections and a final featured-news marker.

Keep as health-system/news content and trim navigation; absence of a diagnosis does not make it an extraction failure.

Source excerpt (original language):

> Giật mình với giá phòng “cắt cổ” của Bệnh viện Nhi Trung ương
> Dù chấp nhận bỏ tiền túi để được khám nhanh và được sử dụng giường, phòng tốt nhất, nhưng nhiều người vẫn không khỏi bất ngờ với giá dịch vụ tại khu tự nguyện A, Bệnh viện Nhi Trung ương.
> Giá phòng hơn cả khách sạn
> Sau 1 tuần điều trị viêm phổi cho con gái 1 tuổi tại khu Điều trị tự nguy

## familydoctor.com.cn — separate_article_date_from_feed_dates

Record: `d747bf33b30de1c7675bc60f529b3ade7b41004459904c2b77fb13ae2795c783`.
Locator: `ViBioMIR/shards/B/familydoctor.com.cn/part-1792685904457-601.parquet`, row group 0, row offset 0.

Header has a 2010 timestamp; later related/latest/hot article feeds contain 2025 dates and unrelated promotional titles. Blocks are all tagged p.

Extract date candidates near the title; avoid the newest-date heuristic; segment by reviewed text boundaries, not heading tags alone.

Source excerpt (original language):

> 新生儿大便异常的8种情况
> 2010-09-09 08:25:40 家庭医生在线
> 举报/反馈
> 新生儿大便次数：新生儿大便几次算正常？
> 注意新生儿大便次数与夜啼问题
> 新生儿大便绿色的原因
> 根据新生儿大便判断健康状况
> 一般而言，只要宝宝正常排便，颜色的深浅变化并不是什么特别严重的问题，便便略干略稀也并不能说明宝宝有什么毛病。但是，如果大便的形态和次数有明显的改变，那就需要妈妈当心了，因为它可能提示宝宝的生理状况有异。
> 新生儿大便异常情况一览
> 大便灰白色，同时宝宝的白眼球和皮肤呈黄色，有可能为胆道梗阻或者是胆汁粘稠，甚至可能是肝炎。
> 大便黑色，可能是胃或肠道上部出血。如果服用了治疗贫血的铁剂药物，也有可能会出现这种情况。
> 大便带有鲜红的血丝，可能是大便干燥，或者是肛门周围皮肤皱裂。
> 大便为赤豆汤样，

## giadinhonline.vn — keep_news_remove_related_teaser

Record: `9c6c1e416c40cf1460efa14d3e9b3b424e62261fa5a4a852dd67158edfb178e0`.
Locator: `ViBioMIR/shards/A/giadinhonline.vn/part-1792715832616-700.parquet`, row group 0, row offset 0.

Foodborne-illness news article ends with a related-question link prefixed by an arrow.

Retain news/reporting content and source conditions; isolate the related teaser without rewriting the article.

Source excerpt (original language):

> 1 người chết, 18 người nhập viện sau khi ăn cỗ ở Thái Bình: Bác sĩ chỉ món khoái khẩu mang ổ bệnh
> Chiều 6/5, Chi cục An toàn vệ sinh thực phẩm tỉnh Thái Bình (thuộc Sở Y tế tỉnh Thái Bình) có thông tin ban đầu về vụ việc nhiều người nhập viện nghi do ngộ độc thực phẩm xảy ra tại phường Hoàng Diệu (thành phố Thái Bình) nghi ngờ có liên quan tới ăn t

## baonghean.vn — handle_noncontiguous_ui_regions

Record: `8817dc030ed71ec0e8563649771879beec1e407786d4fdf17e9e240f53107eb8`.
Locator: `ViBioMIR/shards/A/baonghean.vn/part-1792714424678-697.parquet`, row group 0, row offset 0.

An unrelated teaser occurs near the beginning of a medical screening news article; tail has a byline/related-news marker. tr-tagged blocks are present.

Use block-level source maps for UI removal; do not join disjoint original spans into fabricated continuous evidence or infer table columns.

Source excerpt (original language):

> 1.000 trẻ em Quỳnh Lưu được khám sàng lọc bệnh bẩm sinh
> (Baonghean.vn) - Sáng ngày 2/3, huyện Quỳnh Lưu phối hợp với Quỹ Bảo trợ trẻ em tỉnh và Bệnh viện Hoàn Mỹ (Đà Nẵng) tổ chức chương trình khám sàng lọc miễn phí cho trẻ em mắc các bệnh bẩm sinh trên địa bàn huyện.
> » Linh mục Nguyễn Đình Thục kích động giáo dân
> Có khoảng 1.000 trẻ em từ 1 đến 16

## phunusuckhoe.giadinhonline.vn — remove_unrelated_tail_after_review

Record: `6be25d06452809a6128a9fe29d0a1c37b663801e5b38f2dfd9c41b1d18c86e2b`.
Locator: `ViBioMIR/shards/C/phunusuckhoe.giadinhonline.vn/part-1792716612225-354.parquet`, row group 0, row offset 0.

Beauty article excerpts end with unrelated relationship-story titles. Heading tags are present but are not by themselves evidence of main-content boundaries.

Use domain-specific related-feed boundaries and keep unknown topic labels rather than dropping whole domains.

Source excerpt (original language):

> 1. Công dụng của sữa tươi
> Càng ngày nhu cầu làm đẹp da của chị em ngày càng trở nên quan trọng, nhưng mặc dù vậy thì điều mà nhiều chị em phụ nữ quan tâm nhất vẫn là các phương pháp làm đẹp an toàn, hiệu quả, và tiết kiệm. Trong đó, sữa tươi không đường là nguyên liệu làm trắng da mang lại kết quả nhanh và có cách thức làm cực kì đơn giản.
> Thành ph

## tiemchunglongchau.com.vn — preserve_price_and_schedule_context

Record: `d1ae1dcdfb34eb2128890f0281aa50f199d405e9f6608b2c43da61ba8696b45c`.
Locator: `ViBioMIR/shards/A/tiemchunglongchau.com.vn/part-1792714459953-514.parquet`, row group 0, row offset 0.

Article has multiple headings and a vaccine-price/service discussion, with a final read-more marker.

Do not strip all commercial or price text: it may answer the query. Remove only verified navigation and keep dose/schedule constraints.

Source excerpt (original language):

> Vắc xin phòng viêm màng não do não mô cầu giá bao nhiêu?
> Viêm màng não mô cầu là một bệnh lý nguy hiểm có thể gây ra những biến chứng nghiêm trọng, thậm chí tử vong nếu không được điều trị kịp thời, bệnh lây truyền nhanh qua đường hô hấp, dễ bùng phát thành dịch. Vì vậy, việc tiêm chủng vắc xin viêm não mô cầu đầy đủ và đúng lịch là cách hiệu quả n

## youmed.vn — retain_structure_and_contributor_candidates

Record: `4cc319381394e411b36cc7ace4c13b3a0fe5ff70ab36f38c513407e1097ce0c7`.
Locator: `ViBioMIR/shards/C/youmed.vn/part-1792716592270-150.parquet`, row group 0, row offset 0.

Article has exercise sections and a doctor mention in the introduction, with a final related link.

Keep headings; do not assume every mentioned clinician is the author; retain provenance for contributor candidates.

Source excerpt (original language):

> 10 bài tập tăng chiều cao trước khi đi ngủ
> Nội dung bài viết
> Tập thể dục tăng chiều cao có thể được thực hiện ở nhiều thời điểm ngay cả trước khi ngủ. Hãy cùng Bác sĩ Đoàn Minh Thái tìm hiểu về 10 bài tập tăng chiều cao trước khi đi ngủ dành cho bạn nhé!
> Giấc ngủ ảnh hưởng thế nào đến chiều cao?
> Giấc ngủ ảnh hưởng đến chiều cao của mọi lứa tuổi nhấ

## hellobacsi.com — retain_clinical_cautions

Record: `7ccf62daab078589979534e981d35040226162f250f4fd276aeb5bcce06d5a7d`.
Locator: `ViBioMIR/shards/B/hellobacsi.com/part-1792717258814-229.parquet`, row group 0, row offset 0.

Body contains herbal-use material; tail includes cautions about dose uncertainty, product quality and consultation.

Do not remove medical safety/caution paragraphs as generic disclaimers; distinguish them from site UI.

Source excerpt (original language):

> 5 điều bạn nên biết khi sử dụng nhân sâm Hoa Kỳ
> Là một trong những loại thảo dược phổ biến nhất trên thế giới, nhân sâm Hoa Kỳ đã được sử dụng như một liều thuốc quý trong suốt nhiều thập kỷ để bồi bổ sức khỏe và điều trị bệnh.
> Nhân sâm Hoa Kỳ còn có các tên gọi khác như Hoa Kỳ sâm, Tây Dương sâm, sâm Canada, sâm Bắc Mỹ và sâm Mỹ. Loại sâm này sinh

## test.pmphai.com — recover_heading_structure_from_text

Record: `2b15f7b8f36ecdc3aa0a3380865cf48c2483766fb5eff8a6e0832d78659a5d86`.
Locator: `ViBioMIR/shards/B/test.pmphai.com/part-1792686011400-268.parquet`, row group 0, row offset 0.

Clinical case has explicit Chinese numbered headings, questions and laboratory units, while every block is tagged p.

Detect hierarchical section candidates in text; preserve patient context, Q&A relations and literal laboratory notation. A date in the patient history is not automatically a publication date.

Source excerpt (original language):

> 患者男性，54岁，于2013年3月20日入院。
> 一、主诉
> 间断痰中带血半年余，加重伴胸闷气短2周。
> 二、病史询问
> （一）初步诊断思路及问诊目的
> 中年男性，近半年反复出现痰中带血，有时咯整口鲜血，近2周加重并伴有胸闷气短，初步诊断是咯血原因待查，要注意肺癌、肺泡出血综合征，肺栓塞等疾病。因此，问诊主要围绕咯血的诱因（原因）、咯血的量及性状，发病时主要症状及特点、伴随症状，是否曾就诊，当时的检查结果，以及当时的治疗和疗效如何等问题展开，并兼顾重要鉴别疾病的临床表现，以寻找咯血的原因。
> （二）问诊主要内容及目的
> 1.咯血的量和性状？
> 咯血的病因很多，肺结核、支气管扩张、肺癌是较常见的原因。咯血量的多少有助于病因的判断，临床上一般将24小时内咯血量大于500ml称为大量咯血，属临床急重症。大量咯血常

## msdmanuals.cn — retain_manual_sections

Record: `f5d55140b828351e3fc5d164931a4cbb227965ee56e7a0fe1f8d125e553e8cd8`.
Locator: `ViBioMIR/shards/C/msdmanuals.cn/part-1792716562139-924.parquet`, row group 0, row offset 0.

Manual entry has heading/paragraph blocks and symptom/diagnosis/treatment sections with condition-specific qualifiers.

Use section-aware chunks and preserve lists/negation; do not split away the section heading or infer missing structure.

Source excerpt (original language):

> 过度失血引起的贫血
> 当失血迅速时，血压下降，患者可能出现头晕。
> 当失血缓慢时，患者可能感觉疲劳、气短和苍白。
> 失血部位可能要通过检查粪、尿和影像学来确定。
> 要纠正出血病因，需要时可能要输血及补铁。
> 贫血 的最常见原因是：
> 过度出血
> 失血时，身体迅速将血管外的液体拉入血管内来保持血管充盈。因此，血液被稀释，血细胞比容（红细胞占体内总血量或总血容量的百分比）下降。最终，骨髓红细胞产量增加而纠正贫血。然而，随时间延长，出血导致身体铁的量下降，从而导致骨髓不能增加新红细胞的生成。
> 快速失血
> 症状最初可能比较严重，特别是损伤、手术、分娩、血管破裂等情况造成突然失血而导致贫血迅速发生时。突然大量失血会导致 2 个问题：
> 血管内剩余液量不足可导致 血压下降 。
> 由于携氧红细胞数目快速下降而导致身体的氧供给

## medlatec.vn — english_content_outside_pubmed

Record: `2af11f455c648b27061dd6feb99400ca6af9d5e7e15bb388a5ba74dc659336b2`.
Locator: `ViBioMIR/shards/B/medlatec.vn/part-1792719213707-281.parquet`, row group 0, row offset 876.

The title and opening paragraph are English at a Vietnamese-domain URL; domain/source-language assumptions miss this content.

Keep language detection at document/block level. Do not claim the working corpus contains no English merely because PubMed is empty.

Source excerpt (original language):

> List of MEDLATEC COVID-19 sample collecting locations in Hanoi
> With the implementation of SARS-CoV-2 testing at these locations, MEDLATEC is determined to cooperate with ministries, departments and agencies at all levels by controlling, screening and limiting the spread of the epidemic as well as ensuring the needs of safe travel for people and com

## cnkang.com — english_body_under_chinese_title

Record: `7448bb18926a4d8ea2db368d66945efc16cb0d3616a4c45fe2f5558bf987005d`.
Locator: `ViBioMIR/shards/C/cnkang.com/part-1792717966899-272.parquet`, row group 0, row offset 248.

The complete short audit snippet has a Chinese heading and an English medical paragraph, including numeric comparison conditions.

Support mixed document/block language and preserve the source wording and numeric conditions; do not use a hard domain-language filter.

Source excerpt (original language):

> 腹水白
> The serum-ascitic albumin gradient correlates directly with portal pressure, and patients with gradients greater than or equal to 1.1 g/dl have portal hypertension (transudative ascites) and patients with gradients lesser than 1.1 g/dl do not (exudative ascites). The total protein concentration of ascitic fluid and LDH activity has been traditi

## baonghean.vn — soft_404_despite_http_200

Record: `289c0ba5ebc8afed09ed9fb3ce6bf467df717e6de54a979dd0dc942d570bb09b`.
Locator: `ViBioMIR/shards/A/baonghean.vn/part-1792717012286-244.parquet`, row group 0, row offset 899.

The source reports HTTP 200 but its entire 19-character text is 404 Not Found followed by nginx. Nonempty/2xx is not a valid-article guarantee.

Add exact short error-body counterexamples to offline quality validation; exclude confirmed error placeholders without treating all short text as invalid.

Source excerpt (original language):

> 404 Not Found
> nginx
