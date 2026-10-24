"""Produce a readable Markdown report and standalone SVG charts from metrics."""
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import read_only_db


def table(columns, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(columns) + " |",
                       "| " + " | ".join("---" for _ in columns) + " |"] +
                     ["| " + " | ".join(cell(x) for x in row) + " |" for row in rows])


def chart(csv_path, svg_path, title, label_column, top=15):
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))[:top]
    width, height = 960, 90 + 30 * len(rows)
    maximum = max((int(r["rows"]) for r in rows), default=1) or 1
    items = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
             f'<title>{html.escape(title)}</title>', '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="24" y="32" font-family="sans-serif" font-size="20">{html.escape(title)}</text>']
    for i, row in enumerate(rows):
        y, value = 65 + 30 * i, int(row["rows"])
        label = html.escape(row[label_column][:43])
        items.extend([f'<text x="24" y="{y + 15}" font-family="sans-serif" font-size="12">{label}</text>',
                      f'<rect x="345" y="{y}" width="{480 * value / maximum:.1f}" height="21" fill="#2563eb"/>',
                      f'<text x="{355 + 480 * value / maximum:.1f}" y="{y + 15}" font-family="sans-serif" font-size="12">{value:,}</text>'])
    svg_path.write_text("\n".join(items + ["</svg>"]), encoding="utf-8")


def build_report(output_dir):
    out = Path(output_dir)
    def load(name):
        p = out / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    inv, corpus, refs = (load(n) for n in ["inventory.json", "corpus_summary.json", "references_summary.json"])
    sections = ["# Báo cáo khám phá dữ liệu ViBioMIR", "",
                f"Thời điểm sinh (giờ Việt Nam): {datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(timespec='seconds')}", "",
                "Phân tích dữ liệu cục bộ; không tải dữ liệu, không gọi mô hình hoặc API.", ""]
    if inv:
        sections += ["## Inventory", "", f"Nguồn: `{inv['data_dir']}`.", "",
            table(["Chỉ số", "Giá trị"], [("Tệp Parquet", inv["parquet_files"]),
                ("Dung lượng Parquet (byte)", inv["parquet_bytes"]),
                ("Tổng dòng theo metadata (mọi Parquet)", inv["parquet_rows"]),
                ("Biến thể schema", len(inv["schemas"])), ("Lỗi inventory", len(inv["errors"]))]), "",
            "Schema và lỗi chi tiết: [inventory.json](inventory.json); danh sách tệp: [files.csv](files.csv).", ""]
    if corpus:
        sections += ["## Corpus", "", "**PHÂN TÍCH CHƯA ĐẦY ĐỦ**" if corpus["partial"] else "Đã quét toàn bộ shard corpus phát hiện được.", "",
            table(["Chỉ số", "Giá trị"], [("Shard đã chọn", corpus["selected_corpus_files"]),
                ("Shard phát hiện được", corpus["available_corpus_files"]), ("Dòng đã đọc thành công", corpus["rows"]),
                ("ID tài liệu khác nhau", corpus["unique_doc_ids"]),
                ("ID có nhiều nội dung khác nhau", corpus["ids_with_conflicting_text"]),
                ("Lỗi đọc shard", len(corpus["errors"]))]), "",
            "### Chất lượng (các cờ có thể chồng lấp)", "",
            table(["Dấu hiệu", "Số dòng"], corpus["quality_flags"].items()), "",
            "`short_text` dùng số ký tự thô của văn bản không rỗng, ngưỡng " + str(corpus["short_chars"]) + ". "
            "`text_len_mismatch` so sánh `text_len` với `len(text)` Python. Cờ HTML/trang chặn là heuristic, cần xem lại mẫu.", "",
            "Ví dụ để kiểm tra lại bản gốc: [quality_examples.csv](quality_examples.csv).", "",
            "### Phân bố độ dài", "",
            table(["Đơn vị", "Min", "P50", "P90", "P99", "Max", "Trung bình"],
                  [(name, *[corpus[key][k] for k in ["min", "p50", "p90", "p99", "max", "mean"]])
                   for name, key in [("Ký tự", "characters"), ("Đơn vị tách khoảng trắng", "whitespace_units")]]), "",
            "Các phân vị dùng phần tử ở vị trí floor((n−1)×q) trong dãy đã sắp xếp. Đơn vị tách khoảng trắng "
            "**không phải token mô hình/LCS**, và không dùng để so sánh lượng thông tin giữa tiếng Trung với tiếng Việt/Anh.", "",
            "### Bản ghi trùng", "", table(["Khóa", "Nhóm trùng", "Dòng dư"],
                [(k, v["groups"], v["extra_rows"]) for k, v in corpus["duplicates"].items()]), "",
            "Dòng dư = tổng (kích thước nhóm − 1). Hash SHA-256 tính trên toàn bộ văn bản sau "
            "NFKC → HTML unescape → lowercase → gộp khoảng trắng. Bỏ qua nội dung rỗng khi đếm trùng nội dung. "
            "URL được so sánh nguyên văn, chưa chuẩn hóa URL. Đây là trùng chính xác sau chuẩn hóa, chưa phải near-duplicate. "
            "Các CSV nhóm trùng chỉ xuất tối đa 1.000 nhóm; tổng đếm trong JSON là đầy đủ cho phạm vi đã quét.", "",
            "### Dấu hiệu ngôn ngữ", "", table(["Heuristic", "Số dòng"], corpus["language_hints"].items()), "",
            "Heuristic xem tối đa 4.000 ký tự đầu: tỷ lệ chữ CJK ≥20% → `cjk_likely_zh`; "
            "có dấu đặc trưng tiếng Việt → `vi_diacritics`; nhiều chữ Latin ASCII → `latin_undetermined`. "
            "CJK không khẳng định tiếng Trung; Latin không khẳng định tiếng Anh. "
            "Không có mô hình nhận diện ngôn ngữ, không dùng tên miền làm nhãn ngôn ngữ.", "",
            "### Đối chiếu trạng thái crawl", "",
            table(["Nhóm", "ID trạng thái", "ID trạng thái thiếu shard", "ID shard thiếu trạng thái", "ID HTTP 2xx thiếu text 2xx không rỗng"],
                  [(r["member"], r["state_ids"], r["state_ids_without_shard"], r["shard_ids_without_state"],
                    r["state_2xx_ids_without_nonempty_2xx_text"]) for r in corpus["crawler_reconciliation"]]), "",
            "Đối chiếu ID trong cùng nhóm A/B/C. SQLite trạng thái không phải danh sách tài liệu chính thức; "
            "HTTP 2xx không tự chứng minh nội dung đúng. Nếu chỉ quét mẫu shard thì số thiếu shard chưa phản ánh corpus đầy đủ. "
            "Log `last_run` chỉ là lần chạy gần nhất.", ""]
        if corpus["inspect_blocks"]:
            with read_only_db(out / "metrics.sqlite3") as db:
                block_stats = db.execute(
                    "SELECT COUNT(n_blocks),SUM(n_blocks),AVG(n_blocks),MAX(n_blocks) FROM documents"
                ).fetchone()
            sections += ["### Cấu trúc khối nội dung", "",
                table(["Chỉ số", "Giá trị"], zip(
                    ["Dòng có blocks hợp lệ", "Tổng khối", "Số khối trung bình/dòng có blocks hợp lệ", "Số khối tối đa"],
                    block_stats)), "",
                "Khối nội dung từ crawler chưa mặc định là chunk truy hồi. Cấu trúc hợp lệ là danh sách "
                "các cặp chuỗi `[loại_khối, văn_bản]`; EDA không thay đổi văn bản khối.", ""]
        for csv_name, svg_name, title, label in [
            ("by_domain.csv", "top_domains.svg", "Top domains by corpus rows", "domain"),
            ("by_status.csv", "status.svg", "HTTP status by corpus rows", "status"),
            ("length_histogram.csv", "lengths.svg", "Text length in characters", "character_range"),
        ]:
            chart(out / csv_name, out / svg_name, title, label)
            sections += [f"![{title}]({svg_name})", ""]
    sections += ["## Truy vấn và danh sách tài liệu chính thức", ""]
    official_source = refs.get("official_source")
    if official_source:
        sections += [f"Nguồn: [{official_source['repository']}]"
                     f"(https://huggingface.co/datasets/{official_source['repository']}); "
                     f"revision `{official_source['revision']}`. "
                     f"Thời điểm tải: {official_source['retrieved_at_vietnam']}. "
                     f"Manifest cục bộ: `{official_source['manifest']}`.", ""]
    for key, title in [("queries", "Truy vấn"), ("official_links", "Danh sách tài liệu")]:
        value = refs.get(key, {})
        sections += [f"### {title}", ""]
        if not value or value.get("status") == "missing":
            sections += [f"Chưa có `{value.get('expected_file', key)}`. Chưa thể kiểm tra phần này.", ""]
        else:
            sections += [table(["Chỉ số", "Giá trị"], [(k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v)
                                                       for k, v in value.items()]), ""]
    official_links = refs.get("official_links", {})
    if "nonempty_2xx_id_coverage" in official_links:
        ratio = official_links["nonempty_2xx_id_coverage"]
        if ratio is not None:
            sections += [f"Độ phủ ID có ít nhất một bản text HTTP 2xx không rỗng: **{ratio:.2%}**. "
                         "Đây là độ phủ thu thập; chưa xác nhận văn bản đúng nội dung và không phải Recall truy hồi.", ""]
    coverage_path = out / "official_coverage_by_domain.csv"
    if coverage_path.exists() and official_links.get("status") != "missing":
        with coverage_path.open(encoding="utf-8-sig", newline="") as stream:
            coverage = list(csv.DictReader(stream))
        missing = [r for r in coverage if int(r["ids_without_shard"]) > 0][:15]
        sections += ["### Tên miền thiếu ID so với danh sách chính thức", "",
            table(["Tên miền", "ID chính thức", "ID chưa có shard", "ID có text HTTP 2xx không rỗng"],
                  [(r["domain"], r["official_ids"], r["ids_without_shard"],
                    r["ids_with_nonempty_2xx_text"]) for r in missing]), "",
            "Tối đa 15 tên miền, xếp theo số ID thiếu. Toàn bộ thống kê: "
            "[official_coverage_by_domain.csv](official_coverage_by_domain.csv). "
            "Các URL nguồn không khớp nguyên văn: [official_url_mismatch_examples.csv](official_url_mismatch_examples.csv).", ""]
    query_units = refs.get("queries", {}).get("whitespace_units")
    if query_units:
        sections += ["Độ dài truy vấn ở đây dùng `str.split()`, phân vị theo phần tử floor((n−1)×q). "
                     "Dataset card công bố min–max số từ là 5–283; lượt phân tích tệp đang đọc cho "
                     f"{query_units['min']}–{query_units['max']} "
                     "theo cách đếm trên. Chưa xác định cách tách từ của thống kê công bố; không thay đổi văn bản truy vấn.", ""]
    references_ready = all(refs.get(k, {}).get("file") for k in ["queries", "official_links"])
    next_step = ("- Kiểm tra các ID còn thiếu và các nguồn chưa có text thành công trong thống kê độ phủ chính thức."
                 if references_ready else
                 "- Bổ sung `query.parquet` và `links_corpus_vi_en_zh.parquet` để xác nhận truy vấn và độ phủ ID chính thức.")
    sections += ["## Giới hạn và bước tiếp theo", "",
        "- Xem các cờ chất lượng theo domain trước khi quyết định lọc; mã EDA không loại/sửa bản ghi nguồn.",
        next_step,
        "- Giữ ID gốc khi xử lý nội dung trùng giữa nhiều tài liệu.",
        "- Chưa có nhãn tham chiếu nên không tính Precision/Recall/F2, không kết luận chất lượng truy hồi.",
        "- Nếu bật `--inspect-blocks`, có thêm số khối và cờ JSON/structure lỗi; khối trống không được coi là JSON lỗi.", "",
        "Dữ liệu chi tiết: [corpus_summary.json](corpus_summary.json), "
        "[references_summary.json](references_summary.json), [metrics.sqlite3](metrics.sqlite3).", ""]
    (out / "REPORT.md").write_text("\n".join(sections), encoding="utf-8")
