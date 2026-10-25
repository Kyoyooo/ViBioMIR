"""Read a completed full audit and derive document-level processing decisions."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from .common import write_csv, write_json
from .report import table


def export_query(db, out, name, sql):
    cursor = db.execute(sql)
    write_csv(out / name, [d[0] for d in cursor.description], (tuple(r) for r in cursor))


def analyze_audit(audit_dir, output_dir):
    source, out = Path(audit_dir), Path(output_dir)
    status = json.loads((source / 'scan_status.json').read_text())
    if status['partial'] or status['run_state'] != 'finished':
        raise ValueError('Analysis requires a completed full audit, not a partial run')
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('Choose a new analysis output directory')
    out.mkdir(parents=True, exist_ok=True)
    flag_names = sorted(status['quality_flags'])
    if len(flag_names) > 62:
        raise ValueError('Too many quality flags for the compact analysis bitset')
    bits = {name: 1 << i for i, name in enumerate(flag_names)}
    columns = ['record_id', 'doc_id', 'domain', 'status', 'n_chars', 'text_state', 'disposition',
               'language_hint', 'block_state', 'blocks_text_match', 'html_state', 'has_recovery_material',
               'n_blocks', 'flags_json', 'structure_json', 'normalized_sha256']
    db = sqlite3.connect(out / 'analysis.sqlite3')
    block_kinds = Counter()
    try:
        db.execute('PRAGMA cache_size=-32768')
        db.execute('PRAGMA temp_store=FILE')
        db.execute('''CREATE TABLE facts(record_id TEXT PRIMARY KEY,doc_id INTEGER,domain TEXT,status TEXT,
          n_chars INTEGER,text_present INTEGER,disposition TEXT,language_hint TEXT,blocks_valid INTEGER,
          blocks_match INTEGER,html_available INTEGER,recovery_material INTEGER,n_blocks INTEGER,
          flags INTEGER,normalized_sha256 TEXT)''')
        count = 0
        with db:
            for batch in pq.ParquetFile(source / 'record_audit.parquet').iter_batches(batch_size=512, columns=columns):
                values = []
                for row in batch.to_pylist():
                    flags = json.loads(row['flags_json']) or []
                    structure = json.loads(row['structure_json']) or {}
                    block_kinds.update(structure.get('block_kinds', {}))
                    values.append((row['record_id'], row['doc_id'], row['domain'], row['status'], row['n_chars'],
                        int(row['text_state'] == 'present'), row['disposition'], row['language_hint'],
                        int(row['block_state'] == 'valid'), row['blocks_text_match'],
                        int(row['html_state'] in ('parsed', 'unstructured')), row['has_recovery_material'],
                        row['n_blocks'], sum(bits[name] for name in flags), row['normalized_sha256']))
                db.executemany('INSERT INTO facts VALUES (' + ','.join('?' for _ in range(15)) + ')', values)
                count += len(values)
        if count != status['records_read']:
            raise ValueError('Parquet rows do not reconcile with scan_status.json')
        for key in ['doc_id', 'domain', 'normalized_sha256']:
            db.execute(f'CREATE INDEX facts_{key} ON facts({key})')
        db.execute('''CREATE TABLE documents AS SELECT doc_id,COUNT(*) AS records,MIN(domain) AS domain,
          MAX(text_present) AS any_text,SUM(text_present) AS text_versions,
          COUNT(DISTINCT normalized_sha256) AS normalized_versions,
          MAX(disposition IN ('usable_as_is','needs_cleaning')) AS candidate_version,
          MAX(disposition='needs_review') AS review_version,
          MAX(disposition='needs_reextraction') AS recovery_version,
          COALESCE(MAX(blocks_valid AND blocks_match AND text_present),0) AS matched_blocks_version
          FROM facts WHERE doc_id IS NOT NULL GROUP BY doc_id''')
        db.commit()
        scalar = lambda sql: db.execute(sql).fetchone()[0]
        summary = {'source_audit': str(source.resolve()), 'records': count,
            'distinct_doc_ids': scalar('SELECT COUNT(*) FROM documents'),
            'doc_ids_with_text': scalar('SELECT COUNT(*) FROM documents WHERE any_text=1'),
            'doc_ids_without_text': scalar('SELECT COUNT(*) FROM documents WHERE any_text=0'),
            'doc_ids_with_multiple_records': scalar('SELECT COUNT(*) FROM documents WHERE records>1'),
            'doc_ids_with_conflicting_normalized_text': scalar('SELECT COUNT(*) FROM documents WHERE normalized_versions>1'),
            'doc_ids_with_automatic_candidate_version': scalar('SELECT COUNT(*) FROM documents WHERE candidate_version=1'),
            'doc_ids_with_text_but_all_versions_need_review': scalar('SELECT COUNT(*) FROM documents WHERE any_text=1 AND candidate_version=0'),
            'doc_ids_with_text_and_matching_blocks': scalar('SELECT COUNT(*) FROM documents WHERE matched_blocks_version=1'),
            'rows_with_nonempty_html': scalar('SELECT COUNT(*) FROM facts WHERE html_available=1'),
            'rows_with_valid_blocks': scalar('SELECT COUNT(*) FROM facts WHERE blocks_valid=1'),
            'total_blocks': scalar('SELECT COALESCE(SUM(n_blocks),0) FROM facts'),
            'block_kinds': dict(block_kinds), 'quality_flags': status['quality_flags'],
            'automatic_dispositions': status['dispositions'], 'language_hints': status['language_hints'],
            'interpretation': 'Working-corpus candidates only; no medical relevance or human QA guarantee.'}
        assert summary['distinct_doc_ids'] == status['distinct_doc_ids']
        export_query(db, out, 'document_quality_by_domain.csv', '''SELECT domain,COUNT(*) AS doc_ids,
          SUM(any_text) AS doc_ids_with_text,SUM(1-any_text) AS doc_ids_without_text,
          SUM(records>1) AS doc_ids_with_retries,SUM(normalized_versions>1) AS conflicting_text_ids,
          SUM(candidate_version) AS automatic_candidate_ids,
          SUM(any_text=1 AND candidate_version=0) AS text_ids_requiring_review,
          SUM(matched_blocks_version) AS ids_with_matched_blocks
          FROM documents GROUP BY domain ORDER BY doc_ids DESC,domain''')
        export_query(db, out, 'content_structure_by_domain.csv', '''SELECT domain,COUNT(*) AS records,
          SUM(text_present) AS text_records,SUM(blocks_valid) AS valid_block_records,
          SUM(blocks_match=1) AS matching_text_block_records,SUM(html_available) AS html_records,
          SUM(n_blocks) AS blocks,AVG(n_chars) AS mean_chars,MAX(n_chars) AS max_chars,
          AVG(n_blocks) AS mean_blocks FROM facts GROUP BY domain ORDER BY records DESC,domain''')
        export_query(db, out, 'version_selection_priority.csv', '''SELECT doc_id,domain,records,text_versions,
          normalized_versions,candidate_version,review_version,recovery_version
          FROM documents WHERE records>1 ORDER BY normalized_versions DESC,records DESC,doc_id''')
        for flag, bit in bits.items():
            if status['quality_flags'][flag] != scalar(f'SELECT COUNT(*) FROM facts WHERE flags & {bit} != 0'):
                raise ValueError('Flag count mismatch: ' + flag)
        domain_issues = []
        for flag, bit in bits.items():
            for domain, rows, ids in db.execute(f'''SELECT domain,COUNT(*),COUNT(DISTINCT doc_id) FROM facts
              WHERE flags & {bit} != 0 GROUP BY domain ORDER BY COUNT(*) DESC,domain'''):
                domain_issues.append((domain, flag, rows, ids))
        write_csv(out / 'issues_by_domain.csv', ['domain','flag','records','distinct_doc_ids'], domain_issues)
        metadata_counts = Counter()
        with (source / 'metadata_profile.csv').open(encoding='utf-8-sig') as stream:
            for row in csv.DictReader(stream):
                metadata_counts[(row['field'],row['state'])] += int(row['rows'])
        write_csv(out / 'metadata_availability.csv', ['field','state','records','all_records'],
                  ((*key,n,count) for key,n in sorted(metadata_counts.items())))
        metadata_totals = Counter()
        for (field,state), n in metadata_counts.items():
            metadata_totals[field] += n
        assert all(n == count for n in metadata_totals.values())
        summary['metadata_states'] = {field:{state:n for (f,state),n in metadata_counts.items() if f==field}
                                      for field in sorted(metadata_totals)}
        export_query(db, out, 'language_structure_slices.csv', '''SELECT language_hint,COUNT(*) AS records,
          COUNT(DISTINCT doc_id) AS doc_ids,SUM(text_present) AS text_records,
          SUM(blocks_match=1) AS matching_text_blocks,AVG(n_chars) AS mean_chars
          FROM facts GROUP BY language_hint ORDER BY records DESC''')
        write_json(out / 'summary.json', summary)
        paragraphs = ['# Full-audit processing decisions', '',
          'Derived from the completed automatic audit. Source examples and assistant review are reported separately.', '',
          table(['Metric','Value'],[(k,summary[k]) for k in [
            'records','distinct_doc_ids','doc_ids_with_text','doc_ids_without_text',
            'doc_ids_with_multiple_records','doc_ids_with_conflicting_normalized_text',
            'doc_ids_with_text_and_matching_blocks','rows_with_nonempty_html','total_blocks']]), '',
          '## Priority order', '',
          '1. Build versioned documents and keep official IDs, aliases, source hashes and row locators.',
          '2. Choose among retry/content versions using structure and reviewed quality; do not keep the longest blindly.',
          '3. Extract only metadata supported by existing columns/blocks/text or retained HTML; leave unknown fields null.',
          '4. Pilot domain-specific UI/template removal on original block spans with before/after regression.',
          '5. Preserve medical lists, repeated dosing instructions, negation, tables and Q&A; lexical noise markers are insufficient for deletion.',
          '6. Use validated headings/paragraphs for baseline structural chunks; retain fixed-window fallback and source offsets.',
          '7. Freeze a QA-approved working subset, then build lexical/dense indexes. No recrawl prerequisite.', '',
          'The automatic candidate count is not a final count of clinically valid or relevant documents.',
          'Do not infer exact embedding-token budgets from character or whitespace counts.', '']
        (out / 'PROCESSING_DECISIONS.md').write_text('\n'.join(paragraphs),encoding='utf-8')
        return summary
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=analyze_audit(args.audit_dir,args.output_dir)
    print(json.dumps({k:result[k] for k in ['records','distinct_doc_ids','doc_ids_with_text','rows_with_nonempty_html']},indent=2))


if __name__=='__main__':
    main()
