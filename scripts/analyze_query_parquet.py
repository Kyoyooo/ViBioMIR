"""Descriptive query audit; regex flags are review aids, not clinical labels."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
import unicodedata


INTENTS = {
    'explanation': r'\b(là gì|là sao|như thế nào|giải thích|có nghĩa|nghĩa là)\b',
    'diagnosis_cause': r'\b(nguyên nhân|do đâu|bệnh gì|bị gì|tại sao|vì sao|có phải|vì đâu)\b',
    'management_treatment': r'\b(điều trị|chữa|phẫu thuật|can thiệp|phải làm sao|làm thế nào|nên làm gì|xử lý|khắc phục)\b',
    'prognosis_risk': r'\b(nguy hiểm|có nặng|biến chứng|khỏi|ung thư|có sao không|làm sao không|ảnh hưởng|khả năng)\b',
    'prevention_transmission': r'\b(phòng tránh|phòng ngừa|lây|lây nhiễm|lây truyền|tiêm chủng|tiêm phòng|vắc xin|vaccine)\b',
    'medication_use': r'\b(thuốc|liều|uống|bôi|nhỏ mắt|tác dụng phụ|kháng sinh)\b',
    'test_result': r'\b(xét nghiệm|chỉ số|kết quả|siêu âm|nội soi|chụp|beta[- ]?hcg|scc)\b',
    'diet_lifestyle': r'\b(ăn|kiêng|dinh dưỡng|tập luyện|thể dục|sinh hoạt|rượu|bia|hút thuốc)\b',
}
FEATURES = {
    'digits': r'\d',
    'numeric_age': r'\b\d+(?:[.,]\d+)?\s*(?:tuổi|tháng tuổi|ngày tuổi|tuan tuoi|thang tuoi)\b',
    'time_expression': r'\b(?:\d+|một|hai|ba|vài|mấy|nhiều)\s*(?:ngày|tuần|tháng|năm|giờ)\b',
    'pregnancy_lactation': r'\b(mang thai|thai kỳ|có thai|bầu|cho con bú|cho bé bú|sau sinh|sinh em bé)\b',
    'child_context': r'\b(trẻ|bé|con em|con tôi|sơ sinh|trẻ em)\b',
    'explicit_negation': r'\b(không bị|không có|không sốt|không đau|không dùng|chưa từng|âm tính)\b',
    'question_khong': r'\bkhông\s*[?？.]?\s*$',
    'uncertainty_alternative': r'\b(có phải|không biết|hay là|liệu|nghi ngờ)\b',
    'unit_measurement': r'(?i)\b(?:mg|mcg|µg|ml|mmol/l|mmhg|ng/ml|u/l|iu/l|kg|cm|mm|bpm)\b|\d\s*%',
    'url': r'https?://|www\.',
}


def percentile(values, p):
    ordered = sorted(values)
    point = (len(ordered) - 1) * p / 100
    lo = int(point)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo), 2)


def stats(values):
    return dict(min=min(values), median=statistics.median(values), mean=round(statistics.mean(values), 2),
                p90=percentile(values, 90), p95=percentile(values, 95), p99=percentile(values, 99), max=max(values))


def analyze(path):
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    rows = table.to_pylist()
    require_columns = {'id', 'query'} <= set(table.column_names)
    if not require_columns or not rows:
        raise ValueError('Expected nonempty id/query table')
    raw_duplicates, normalized_duplicates = defaultdict(list), defaultdict(list)
    output = []
    for row in rows:
        text = row['query']
        if not isinstance(text, str):
            raise ValueError(f'Nonstring query: {row["id"]}')
        normalized = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text).strip()).casefold()
        raw_duplicates[text].append(row['id'])
        normalized_duplicates[normalized].append(row['id'])
        language = ('contains_cjk' if re.search(r'[\u3400-\u9fff]', text) else
                    'vietnamese_diacritics' if re.search(r'[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]', text) else
                    'latin_or_other_unclassified')
        intents = [name for name, pattern in INTENTS.items() if re.search(pattern, normalized)]
        features = [name for name, pattern in FEATURES.items() if re.search(pattern, normalized)]
        output.append(dict(id=row['id'], query=text, characters=len(text), whitespace_units=len(text.split()),
                           question_marks=text.count('?') + text.count('？'), language_heuristic=language,
                           intent_hints=intents, feature_hints=features, empty=not text.strip(),
                           nfc_differs=text != unicodedata.normalize('NFC', text),
                           replacement_character='\ufffd' in text, surrounding_whitespace=text != text.strip()))
    chars = [r['characters'] for r in output]
    units = [r['whitespace_units'] for r in output]
    summary = dict(path=str(path), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(), rows=len(rows),
        schema=str(table.schema), unique_ids=len({r['id'] for r in rows}),
        id_min=min(r['id'] for r in rows), id_max=max(r['id'] for r in rows),
        ids_in_order=[r['id'] for r in rows] == list(range(1, len(rows)+1)),
        characters=stats(chars), whitespace_units=stats(units),
        length_bands={label: sum(a <= u < b for u in units) for label,a,b in
                      [('1-20',1,21),('21-50',21,51),('51-100',51,101),('101-200',101,201),('201+',201,float('inf'))]},
        language_heuristics=dict(Counter(r['language_heuristic'] for r in output)),
        intent_hints=dict(Counter(k for r in output for k in r['intent_hints'])),
        feature_hints=dict(Counter(k for r in output for k in r['feature_hints'])),
        two_or_more_intent_hints=sum(len(r['intent_hints']) >= 2 for r in output),
        no_intent_hint=sum(not r['intent_hints'] for r in output),
        two_or_more_question_marks=sum(r['question_marks'] >= 2 for r in output),
        zero_question_marks=sum(r['question_marks']==0 for r in output),
        integrity={k:sum(r[k] for r in output) for k in
                   ['empty','nfc_differs','replacement_character','surrounding_whitespace']},
        exact_duplicate_groups=[v for v in raw_duplicates.values() if len(v)>1],
        normalized_duplicate_groups=[v for v in normalized_duplicates.values() if len(v)>1],
        longest_ids=[r['id'] for r in sorted(output,key=lambda x:-x['whitespace_units'])[:10]],
        caveats=['Whitespace units are not model tokens or Vietnamese words.',
                 'Intent/feature/language hints are overlapping regex flags, not verified labels.',
                 'Query-only analysis cannot measure retrieval quality or answer-span lengths.'])
    return summary, output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path('query/query.parquet'))
    parser.add_argument('--output', type=Path, default=Path('reports/query_analysis_20261101'))
    args = parser.parse_args()
    summary, rows = analyze(args.input)
    args.output.mkdir(parents=True, exist_ok=True)
    for name,value in [('SUMMARY.json',summary),('QUERY_FLAGS.json',rows),('RULES.json',dict(intent_hints=INTENTS,feature_hints=FEATURES))]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    for query_id in summary['longest_ids'][:4]:
        row=next(r for r in rows if r['id']==query_id)
        print('LONG EXAMPLE:',row['id'],row['whitespace_units'],row['query'])


if __name__ == '__main__':
    main()
