"""Build three assistant-drafted evidence comparisons; never mark them human gold."""
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
archive = ROOT / 'submission_17.zip'
with zipfile.ZipFile(archive) as z:
    payload = z.read('submission.json')
    row = next(r for r in json.loads(payload) if r['id'] == 5)
query_rows = json.loads((ROOT / 'reports/query_analysis_20261101/QUERY_FLAGS.json').read_text(encoding='utf-8'))
query = next(q['query'] for q in query_rows if q['id'] == 5)

specs = [
    (0, None, None, 'insufficient_evidence',
     'Chỉ là tiêu đề; có từ khóa lao và phòng tránh nhưng chưa đưa ra nội dung trả lời. '
     'Không suy ra cả tài liệu không liên quan: cùng doc còn có các chunk khác.',
     'Loại chunk tiêu đề này khỏi evidence?'),
    (3, '9. Tuân thủ dùng thuốc', '10. Tránh xa căng thẳng', 'subspan_more_focused',
     'Đoạn con giữ phần văn bản nhắc kháng thuốc và lây truyền. Chunk gốc còn có các mục khác. '
     'Đây là nhận xét về độ tập trung của văn bản, chưa xác nhận tính đúng y khoa hoặc khớp đáp án BTC.',
     'Đoạn con tập trung hơn và vẫn đủ ngữ cảnh so với chunk gốc?'),
    (5, 'Giảm tiếp xúc nguồn lây', '; trong các cơ sở', 'potential_support_needs_review',
     'Đoạn con chứa trực tiếp cụm lao đa kháng thuốc và một biện pháp được tài liệu mô tả. '
     'Cần người review xác nhận đủ ngữ cảnh và phạm vi câu hỏi; không mặc định đây là gold đầy đủ.',
     'Đoạn này hỗ trợ trực tiếp ít nhất một phần câu hỏi?'),
]
examples = []
for index, opening, ending, suggestion, reason, question in specs:
    parent = row['relevant_chunks'][index]
    text = parent['chunk_text']
    a = text.index(opening) if opening else 0
    b = text.index(ending, a) if ending else len(text)
    while b > a and text[b-1].isspace():
        b -= 1
    selected = text[a:b]
    assert selected and selected == text[a:b]
    examples.append(dict(query_id=5, doc_id=parent['doc_id'], parent_chunk_order=index,
        parent_text=text, proposed_text=selected, parent_characters=len(text),
        proposed_characters=len(selected), start=a, end=b,
        parent_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        suggestion=suggestion, rationale=reason, question_for_reviewer=question,
        human_judged=False, human_decision=None, reviewer=None))

out = ROOT / 'reports/label_review_pilot_20261101'
out.mkdir(parents=True, exist_ok=True)
packet = dict(status='assistant_draft_not_gold', query_id=5, query=query,
    example_selection='Explicit illustrative chunk indices 0, 3, 5; not a random quality estimate',
    source_json_sha256=hashlib.sha256(payload).hexdigest(), source_scope='submission_chunks_only',
    original_corpus_verified=False, clinical_correctness_verified=False,
    official_lcs_evaluated=False, examples=examples)
(out/'DRAFT_REVIEW.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines = ['# Mẫu review nhỏ: 1 query, 3 trường hợp\n',
         '**Trạng thái: nhãn nháp do trợ lý đề xuất, chưa phải GT hoặc kết quả Qwen.**\n',
         f'**Query 5:** {query}\n',
         'Ba trường hợp được chọn để minh họa cách review, không phải mẫu ngẫu nhiên để ước lượng chất lượng. '
         'Đây là kiểm tra sự liên quan và ranh giới văn bản, không xác nhận nội dung y khoa.\n',
         'Nếu query này thuộc test, chỉ dùng làm minh họa theo phạm vi BTC cho phép; '
         'không biến nhãn mẫu thành tập tune. GT chính thức nên dùng dữ liệu phát triển được phép.\n',
         '## Cách dùng\n',
         'Đọc nhận xét trước. Nếu muốn kiểm tra, mở phần “Chunk gốc”. '
         'Với mỗi trường hợp chọn: **đồng ý / không đồng ý / chưa đủ cơ sở** và ghi lý do. '
         'Không cần đếm ký tự hoặc tự tính LCS.\n']
for number, e in enumerate(examples,1):
    lines += [f'## Trường hợp {number}: doc {e["doc_id"]}, chunk xuất số {e["parent_chunk_order"]}\n',
              f'**Nhận xét sơ bộ:** {e["rationale"]}\n',
              f'**Câu cần xác nhận:** {e["question_for_reviewer"]}\n',
              f'**Độ dài:** gốc {e["parent_characters"]} ký tự; phần đề xuất {e["proposed_characters"]} ký tự. '
              'Ngắn hơn không chứng minh điểm tốt hơn.\n',
              '**Phần văn bản đang xem xét:**\n',
              '```text\n'+e['proposed_text']+'\n```\n',
              '<details>\n<summary>Xem nguyên chunk gốc</summary>\n\n',
              '```text\n'+e['parent_text']+'\n```\n', '</details>\n',
              '**Người review:** chưa có. **Quyết định:** chưa gán.\n']
lines += ['## Giới hạn\n',
          'Chỉ đối chiếu với chunk trong submission; chưa có region/version toàn văn. '
          'Offsets trong JSON tính theo ký tự Python trong parent chunk. Không phải source offsets của corpus. '
          'Mẫu này chưa dùng để tính F2, chưa xác nhận tính đúng y khoa và chưa phải human gold.\n',
          'File máy đọc: [DRAFT_REVIEW.json](DRAFT_REVIEW.json).\n']
(out/'README.md').write_text('\n'.join(lines),encoding='utf-8')
print(out/'README.md')
