"""Verify review-packet locators and evidence spans against original local rows."""
from collections import defaultdict
import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .audit import sha256_file
from .audit_checks import digest
from .common import normalize,write_csv,write_json
from .corpus import valid_id


def check_samples(audit_dir, output_dir):
    source,out=Path(audit_dir),Path(output_dir)
    manifest=json.loads((source/'input_manifest.json').read_text())
    root=Path(manifest['data_dir'])
    out=out.resolve()
    if out==root or root in out.parents:
        raise ValueError('Sample-check output must be outside the source data directory')
    samples={}; kinds=defaultdict(set); duplicate_conflicts=set()
    for line in (source/'review_samples.jsonl').read_text().splitlines():
        sample=json.loads(line);record=sample['record'];key=record['record_id']
        if key in samples and samples[key]!=record:
            duplicate_conflicts.add(key)
        samples[key]=record;kinds[key].add(sample['sample_kind'])
    groups=defaultdict(list)
    for r in samples.values():groups[(r['shard'],r['row_group'])].append(r)
    files={r['path']:r for r in manifest['files'] if r['selected']}
    checked_files=set();results=[]
    for (shard,group_id),records in sorted(groups.items()):
        path=root/shard;item=files[shard]
        if shard not in checked_files:
            if sha256_file(path)!=item['sha256']:raise ValueError('Source changed: '+shard)
            checked_files.add(shard)
        columns=[c for c in ('id','url','text','blocks','html_trim','final_url') if c in item['columns']]
        table=pq.ParquetFile(path).read_row_group(group_id,columns=columns)
        selected=table.take(pa.array([r['row_offset'] for r in records],type=pa.int64())).to_pylist()
        for r,row in zip(records,selected):
            text=row.get('text') if isinstance(row.get('text'),str) else ''
            expected=digest(text) if text.strip() else None
            spans=[span for signal in r.get('signals',{}).values() for span in signal.get('examples',[])]
            spans_ok=all(isinstance(x.get('start'),int) and isinstance(x.get('end'),int)
                         and 0<=x['start']<=x['end']<=len(text) and text[x['start']:x['end']]==x['text'] for x in spans)
            record_id=digest(f"{item['sha256']}\0{shard}\0{group_id}\0{r['row_offset']}")
            joined=None
            try:
                blocks=json.loads(row['blocks']) if isinstance(row.get('blocks'),str) else row.get('blocks')
                if isinstance(blocks,list) and all(isinstance(b,list) and len(b)==2 and all(isinstance(v,str) for v in b) for b in blocks):
                    joined='\n'.join(b[1] for b in blocks)
            except (ValueError,TypeError):pass
            source_id=row.get('id')
            expected_id=source_id if valid_id(source_id) and -(2**63)<=source_id<2**63 else None
            result={'record_id':r['record_id'],'domain':r['domain'],'sample_kinds':';'.join(sorted(kinds[r['record_id']])),
                'shard':shard,'row_group':group_id,'row_offset':r['row_offset'],
                'record_id_matches':record_id==r['record_id'],'doc_id_matches':expected_id==r.get('doc_id'),
                'verbatim_hash_matches':expected==r.get('verbatim_sha256'),'signal_spans_valid':spans_ok,
                'signal_examples_checked':len(spans),'raw_text_equals_block_join':None if joined is None else text==joined,
                'normalized_text_matches_blocks':None if joined is None else normalize(text)==normalize(joined),
                'source_has_nonempty_html':bool(isinstance(row.get('html_trim'),str) and row['html_trim'].strip()),
                'review_method':'automated_source_consistency','human_expert_validated':False}
            result['packet_copies_consistent']=r['record_id'] not in duplicate_conflicts
            result['checks_pass']=all(result[k] for k in ['record_id_matches','doc_id_matches','verbatim_hash_matches','signal_spans_valid','packet_copies_consistent'])
            results.append(result)
    out.mkdir(parents=True,exist_ok=True)
    if results:write_csv(out/'sample_consistency_checks.csv',list(results[0]),([r[k] for k in results[0]] for r in results))
    summary={'unique_sample_records_checked':len(results),'source_files_checked':len(checked_files),
        'random_sample_records_checked':sum('domain_stratified_random' in r['sample_kinds'] for r in results),
        'records_failing_consistency':sum(not r['checks_pass'] for r in results),
        'packet_duplicate_conflict_records':len(duplicate_conflicts),
        'signal_examples_checked':sum(r['signal_examples_checked'] for r in results),
        'exact_block_join_matches':sum(r['raw_text_equals_block_join'] is True for r in results),
        'normalized_block_join_matches':sum(r['normalized_text_matches_blocks'] is True for r in results),
        'interpretation':'Source consistency only; not main-content correctness, human review or clinical validation.'}
    write_json(out/'sample_consistency_summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=check_samples(args.audit_dir,args.output_dir)
    print(json.dumps(result,indent=2))
    if result['records_failing_consistency']:raise SystemExit(1)


if __name__=='__main__':main()
