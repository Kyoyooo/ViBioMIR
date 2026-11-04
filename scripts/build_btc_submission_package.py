"""Package the actual workspace source and submission documentation, without data/credentials."""
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT/'docs/btc_submission_20261104'
OUT = ROOT/'deliverables/r2ai_stage3_20261104'
SOURCE_DIRS = ('vibiomir', 'scripts', 'configs', 'colab', 'tests', 'docs', 'reports', 'exploration', 'taxonomy', 'native', 'sharing')
SUFFIXES = {'.py', '.ipynb', '.json', '.md', '.txt', '.csv', '.tsv', '.svg', '.log', '.cpp', '.h', '.hpp', '.toml', '.yaml', '.yml', '.sh', '.sql'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def inline(text):
    text = html.escape(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: '<a href="'+html.escape(
        m[2] if '://' in m[2] else 'docs/btc_submission_20261104/'+m[2], quote=True)+'">'+m[1]+'</a>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)


def diagram():
    labels = [('Corpus / audit','Dữ liệu và checksum'), ('Document store','Phiên bản và provenance'),
        ('Cleaning','Mapping nguồn'), ('Structural chunks','5.512.624 đoạn'), ('BGE-M3 embedding','Vector 1.024 chiều'),
        ('FAISS IVF-PQ','Chỉ mục corpus'), ('Query tiếng Việt','1.200 query'), ('BGE query embedding','Cùng checkpoint'),
        ('Dense retrieval','Top 500'), ('Pool + kiểm tra nguồn','Top 400'), ('BGE reranker','Chấm mức liên quan'),
        ('Chọn và xuất ZIP','120 docs / 120 chunks')]
    parts = ['<svg role="img" aria-label="Kiến trúc dense BGE" viewBox="0 0 1000 245" xmlns="http://www.w3.org/2000/svg"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#315b75"/></marker></defs>']
    for index, (title, desc) in enumerate(labels):
        x, y = 10+(index % 6)*165, 12+(index//6)*145
        parts.append(f'<rect x="{x}" y="{y}" width="150" height="67" rx="7" fill="#edf5f9" stroke="#315b75"/>')
        parts.append(f'<text x="{x+75}" y="{y+27}" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" fill="#15394f">{html.escape(title)}</text>')
        parts.append(f'<text x="{x+75}" y="{y+47}" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" fill="#315b75">{html.escape(desc)}</text>')
        if index % 6 != 5:
            parts.append(f'<path d="M{x+150},{y+33} H{x+163}" stroke="#315b75" marker-end="url(#arrow)"/>')
    parts.append('<path d="M910,79 V118 H415 V154" fill="none" stroke="#315b75" marker-end="url(#arrow)"/>')
    return ''.join(parts)+'</svg>'


def render_markdown(text):
    lines, output, index = text.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        if line.startswith('```'):
            language = line[3:].strip()
            body = []
            index += 1
            while index < len(lines) and not lines[index].startswith('```'):
                body.append(lines[index]); index += 1
            output.append(diagram() if language == 'mermaid' else '<pre><code>'+html.escape('\n'.join(body))+'</code></pre>')
        elif line.startswith('|') and index+1 < len(lines) and re.match(r'^\|[\s:|\-]+\|$', lines[index+1]):
            headings = [v.strip() for v in line.strip('|').split('|')]
            output.append('<table><thead><tr>'+''.join('<th>'+inline(v)+'</th>' for v in headings)+'</tr></thead><tbody>')
            index += 2
            while index < len(lines) and lines[index].startswith('|'):
                output.append('<tr>'+''.join('<td>'+inline(v.strip())+'</td>' for v in lines[index].strip('|').split('|'))+'</tr>')
                index += 1
            output.append('</tbody></table>')
            continue
        elif line.startswith('#'):
            level = min(len(line)-len(line.lstrip('#')), 6)
            output.append(f'<h{level}>'+inline(line[level:].strip())+f'</h{level}>')
        elif line.strip():
            if line.startswith('> '):
                output.append('<blockquote>'+inline(line[2:])+'</blockquote>')
            elif re.match(r'^(?:- |\d+\. )', line):
                output.append('<p class="list">'+inline(line)+'</p>')
            else:
                output.append('<p>'+inline(line)+'</p>')
        index += 1
    return '\n'.join(output)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    links, final = read(DOCS/'LINKS.json'), read(DOCS/'FINAL_SUBMISSION.json')
    missing, warnings = [], []
    for key in ('team_name', 'representative_name', 'data_and_artifacts_url'):
        if not links.get(key): missing.append('LINKS.'+key)
    if not final.get('confirmed_by_owner'): missing.append('FINAL_SUBMISSION.confirmed_by_owner')
    if not final.get('submission_route'): missing.append('FINAL_SUBMISSION.submission_route')
    selected = final.get('submission_zip_path')
    audit = None
    if selected:
        path = Path(selected)
        path = path if path.is_absolute() else ROOT/path
        zip_raw = path.read_bytes()
        with zipfile.ZipFile(path) as z:
            if z.namelist() != ['submission.json']: raise ValueError('Final submission ZIP schema differs')
            raw = z.read('submission.json')
        rows = json.loads(raw)
        if len(rows) != 1200 or [row['id'] for row in rows] != list(range(1,1201)):
            raise ValueError('Final submission query coverage differs')
        for row in rows:
            docs = row['relevant_docs']
            if any(type(d) is not int for d in docs) or len(docs) != len(set(docs)):
                raise ValueError('Invalid document IDs')
            for index, chunk in enumerate(row['relevant_chunks']):
                if chunk['doc_id'] not in docs or not chunk['chunk_text'].strip() or chunk['chunk_order'] != index:
                    raise ValueError('Invalid final evidence')
        for key, value in [('submission_zip_sha256',sha(zip_raw)), ('submission_json_sha256',sha(raw))]:
            if final.get(key) and final[key] != value: raise ValueError('Final submission hash changed: '+key)
            final[key] = value
        write(DOCS/'FINAL_SUBMISSION.json', final)
        audit = dict(file=path.name, bytes=len(zip_raw), queries=len(rows),
            document_occurrences=sum(len(row['relevant_docs']) for row in rows),
            chunks=sum(len(row['relevant_chunks']) for row in rows), zip_sha256=sha(zip_raw),
            json_sha256=sha(raw), validation='local_schema_coverage_and_hash_passed', relevance_measured=False)
        report_path = DOCS/'evidence/REPORT.json'
        if report_path.exists():
            producer = read(report_path)
            if producer['zip_sha256'] != sha(zip_raw) or producer['submission_sha256'] != sha(raw):
                raise ValueError('Producer REPORT does not match selected submission')
            audit['matches_drive_producer_report'] = True
        write(DOCS/'evidence/FINAL_INPUT_AUDIT.json', audit)
    else:
        missing.append('FINAL_SUBMISSION.submission_zip_path')
    if not links.get('data_access_verified_by_owner'):
        warnings.append('Đội cần xác nhận tải artifact lớn bằng tài khoản BTC/khách; connector read không thay thế kiểm tra này.')
    if links.get('root_permission_observed') == 'anyone:writer':
        warnings.append('Drive root đang anyone:writer; cân nhắc đổi Viewer trước khi gửi. Công cụ chưa sửa quyền.')
    if not links.get('repository_access_verified_by_owner'):
        warnings.append('Chưa xác minh quyền truy cập GitHub; ZIP đính kèm đã chứa mã nguồn workspace.')
    if not final.get('competition_submission_id'):
        warnings.append('Chưa có submission ID/receipt BTC; hồ sơ không xác nhận điểm private.')
    # Exact observed source/data link is included; do not embed credentials or raw data.
    chapters = ('THUYET_MINH_SAN_PHAM.md', 'DU_LIEU_VA_ARTIFACT.md', 'MO_HINH.md', 'TAI_HIEN.md')
    sections = ['<section>'+render_markdown((DOCS/name).read_text(encoding='utf-8'))+'</section>' for name in chapters]
    css = '''@page{size:A4;margin:16mm}body{font-family:Arial,Helvetica,sans-serif;color:#142e3f;max-width:1000px;margin:30px auto;line-height:1.55;padding:0 20px}h1{font-size:25px;color:#143d59;border-bottom:3px solid #208ca0;padding-bottom:12px}h2{font-size:19px;margin-top:28px}h3{font-size:16px}p{margin:9px 0}a{color:#145b83;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:12px;margin:15px 0}td,th{border:1px solid #b9cbd7;padding:7px;vertical-align:top;overflow-wrap:anywhere}th{background:#e7f1f6;text-align:left}thead{display:table-header-group}pre{background:#f3f6f8;border-left:3px solid #208ca0;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;color:#233e4e}code{font-family:Consolas,monospace;font-size:.93em;overflow-wrap:anywhere}blockquote{background:#f1f6f9;padding:12px;border-left:3px solid #208ca0;margin:10px 0}.list{margin-left:12px}svg{width:100%;height:auto}section{break-before:page}section:first-child{break-before:auto}h1,h2,h3{break-after:avoid}tr{break-inside:avoid}@media print{body{margin:0;padding:0;max-width:none;font-size:11pt}h1{font-size:20pt}h2{font-size:15pt}pre{font-size:8pt}table{font-size:8.5pt}}'''
    html_raw = ('<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>onlyrelu — ViBioMIR — Hồ sơ Stage 3</title><style>'+css+'</style><body>'+''.join(sections)+'</body></html>').encode()
    html_name = 'onlyrelu_ViBioMIR_ThuyetMinh.html'
    (OUT/html_name).write_bytes(html_raw)
    team, representative = links.get('team_name') or '[TÊN ĐỘI]', links.get('representative_name') or '[ĐẠI DIỆN]'
    archive_name = 'onlyrelu_ViBioMIR_R2AI_Stage3_HoSo_MaNguon.zip'
    email = f'''Người nhận: hr@aiguru.com.vn
Tiêu đề: [ROAD TO AI 2026 – Stage 3] Hồ sơ thuyết minh ViBioMIR – {team}

Kính gửi Ban Tổ chức ROAD TO AI 2026,

Đội {team}, đại diện {representative}, xin gửi hồ sơ thuyết minh sản phẩm ViBioMIR tham gia Stage 3.

Hồ sơ đính kèm gồm thuyết minh kiến trúc/phương pháp, mô tả dữ liệu, checkpoint model, README hướng dẫn tái hiện và mã nguồn/cấu hình kèm checksum.

Dữ liệu và artifact: {links.get('data_and_artifacts_url')}
Repository: {links.get('repository_url')}
Gói mã nguồn/hồ sơ đính kèm: {archive_name}
Bài private được đội lựa chọn: {selected}
SHA256 ZIP: {final.get('submission_zip_sha256')}

Trong hồ sơ, bài private được đối chiếu với export Dense V1 + BGE rerank; các nhánh Qwen/E5/fallback được ghi rõ là thử nghiệm riêng. Link checkpoint mã nguồn mở và các bước tái hiện nằm trong bộ tài liệu đính kèm.

Đại diện liên hệ: {representative}
Địa chỉ phản hồi: {links.get('representative_email') or 'Địa chỉ email gửi hồ sơ này'}

Trân trọng,
{team} — {representative}
'''
    (DOCS/'EMAIL_DRAFT.txt').write_text(email, encoding='utf-8')
    (OUT/'EMAIL_DRAFT.txt').write_text(email, encoding='utf-8')
    package_readme = f'''# {team} — ViBioMIR — ROAD TO AI 2026 Stage 3

Đại diện: {representative}. Bài private được đội chọn: {selected}.

Đọc [README hồ sơ](docs/btc_submission_20261104/README.md) để cài đặt/tái hiện.
Mở [{html_name}]({html_name}) để đọc bản thuyết minh đầy đủ có sơ đồ minh họa và in PDF.
Các tài liệu riêng trong docs/btc_submission_20261104 gồm tổng quan, dữ liệu, model, hướng dẫn tái hiện, LINKS và FINAL_SUBMISSION.

Mã nguồn đầy đủ nằm tại vibiomir/, scripts/, exploration/, native/; configs/, requirements-stage*.txt và colab/ chứa cấu hình/workflow. Tests và reports đi kèm. README cũ của repository được giữ tại README_REPOSITORY.md.

SOURCE_MANIFEST.json ghi hash từng file xuất trong gói và git HEAD tham chiếu. Gói chứa workspace hiện tại, bao gồm thay đổi chưa commit; không tự coi HEAD là hash của toàn bộ nội dung đã sửa.

Dữ liệu thô, index, database, weights và ZIP private không nằm trong gói mã nguồn; tải từ link dữ liệu trong LINKS.json. Chính xác bài private được khóa hash tại FINAL_SUBMISSION.json. Công cụ chưa gửi email, upload hồ sơ hay thay quyền chia sẻ.
'''.encode()
    payloads = {'README.md':package_readme, 'README_REPOSITORY.md':(ROOT/'README.md').read_bytes(), html_name:html_raw}
    pdf_path = OUT/'onlyrelu_ViBioMIR_ThuyetMinh.pdf'
    receipt_path = OUT/'PDF_RENDER_RECEIPT.json'
    if pdf_path.exists() and receipt_path.exists():
        receipt = read(receipt_path)
        if receipt.get('html_sha256') == sha(html_raw) and receipt.get('pdf_sha256') == sha(pdf_path.read_bytes()):
            payloads[pdf_path.name] = pdf_path.read_bytes()
            payloads[receipt_path.name] = receipt_path.read_bytes()
        else:
            warnings.append('PDF cũ khác HTML mới; cần render lại. PDF không được đưa vào ZIP.')
    for directory in SOURCE_DIRS:
        for path in sorted((ROOT/directory).rglob('*')):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES or any(
                name in path.parts for name in ('__pycache__', '.ipynb_checkpoints', '.cache', '.git', '.env', '.codex', '.agents', '.aws')):
                continue
            raw = path.read_bytes()
            if path.suffix == '.ipynb':
                notebook = json.loads(raw)
                for cell in notebook['cells']:
                    if cell['cell_type'] == 'code': cell.update(outputs=[], execution_count=None)
                notebook.get('metadata', {}).pop('widgets', None)
                raw = (json.dumps(notebook, ensure_ascii=False, indent=1)+'\n').encode()
            payloads[path.relative_to(ROOT).as_posix()] = raw
    for path in sorted(ROOT.glob('requirements*.txt')):
        payloads[path.name] = path.read_bytes()
    for name in ('LICENSE', '.gitignore', '261012_R2_AI_S3_MentorDay1_summary.md'):
        if (ROOT/name).is_file(): payloads[name] = (ROOT/name).read_bytes()
    commit = subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, capture_output=True, text=True)
    manifest = dict(date='2026-11-04', team=team, representative=representative,
        reference_git_head=commit.stdout.strip() if commit.returncode == 0 else None,
        content_scope='current workspace; notebook outputs stripped; no raw data/credentials/weights/large runtime artifacts',
        files={name:dict(bytes=len(raw), sha256=sha(raw)) for name, raw in sorted(payloads.items())})
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode()
    payloads['SOURCE_MANIFEST.json'] = manifest_raw
    (OUT/'SOURCE_MANIFEST.json').write_bytes(manifest_raw)
    archive = OUT/archive_name
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, raw in sorted(payloads.items()): z.writestr(name, raw)
    with zipfile.ZipFile(archive) as z:
        if len(z.namelist()) != len(set(z.namelist())) or z.testzip() is not None: raise ValueError('ZIP integrity failed')
        for name, meta in manifest['files'].items():
            if sha(z.read(name)) != meta['sha256']: raise ValueError('Source manifest mismatch: '+name)
    report = dict(status='built_with_missing_required_fields' if missing else 'built_required_fields_present',
        missing_required_fields=missing, owner_review_notes=warnings, archive=str(archive.relative_to(ROOT)),
        archive_bytes=archive.stat().st_size, archive_sha256=sha(archive.read_bytes()), files=len(payloads),
        source_manifest_verification='passed', final_submission_audit=audit,
        data_link_scope='connector read verified; all large payload access not independently tested',
        sent_email=False, uploaded=False)
    write(OUT/'PACKAGE_REPORT.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
