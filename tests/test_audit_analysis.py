"""Check full-audit interpretation without confusing records with documents."""
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from exploration.audit import run_audit
from exploration.audit_analysis import analyze_audit
from exploration.audit_sample_check import check_samples


class AuditAnalysisTest(unittest.TestCase):
    def test_document_counts_versions_and_metadata_denominators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'Data'; source=Path(tmp)/'audit'; out=Path(tmp)/'analysis'
            folder=root/'shards'/'A'/'example.org';folder.mkdir(parents=True)
            text='Không dùng 50 mg. Preserve the dose and source evidence. '*8
            rows=[{'id':1,'url':'https://example.org/1','status':'200','text':text,'title':'Source evidence',
                   'blocks':json.dumps([['p',text]]),'html_trim':''},
                  {'id':1,'url':'https://example.org/1','status':'200','text':'Different original text. '*12,
                   'title':'Original text','blocks':None,'html_trim':''},
                  {'id':2,'url':'https://example.org/2','status':'404','text':'','title':None,'blocks':None,'html_trim':''}]
            pq.write_table(pa.Table.from_pylist(rows),folder/'a.parquet')
            status=run_audit(root,source,sample_size=3)
            self.assertFalse(status['partial'])
            summary=analyze_audit(source,out)
            self.assertEqual(summary['records'],3)
            self.assertEqual(summary['distinct_doc_ids'],2)
            self.assertEqual(summary['doc_ids_with_text'],1)
            self.assertEqual(summary['doc_ids_without_text'],1)
            self.assertEqual(summary['doc_ids_with_multiple_records'],1)
            self.assertEqual(summary['doc_ids_with_conflicting_normalized_text'],1)
            self.assertEqual(summary['doc_ids_with_text_and_matching_blocks'],1)
            self.assertEqual(summary['rows_with_nonempty_html'],0)
            self.assertEqual(summary['metadata_states']['authors']['missing_column'],3)
            self.assertTrue((out/'document_quality_by_domain.csv').exists())
            consistency=check_samples(source,out)
            self.assertEqual(consistency['random_sample_records_checked'],3)
            self.assertEqual(consistency['records_failing_consistency'],0)
            packet=source/'review_samples.jsonl'
            samples=[json.loads(line) for line in packet.read_text().splitlines()]
            damaged=next(s for s in samples if s['record']['signals'].get('number_unit',{}).get('examples'))
            damaged['record']['signals']['number_unit']['examples'][0]['text']='incorrect span'
            packet.write_text('\n'.join(json.dumps(s,ensure_ascii=False) for s in samples)+'\n')
            bad=check_samples(source,out)
            self.assertGreater(bad['records_failing_consistency'],0)
            with self.assertRaises(FileExistsError):analyze_audit(source,out)

    def test_partial_audit_is_not_reported_as_full_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'audit';source.mkdir()
            (source/'scan_status.json').write_text(json.dumps({'partial':True,'run_state':'finished'}))
            out=Path(tmp)/'analysis'
            with self.assertRaises(ValueError):analyze_audit(source,out)
            self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
