"""Linux integration: adapted dense results through the actual S09 pipeline."""
import unittest

import test_stage9_retrieval as fixtures
from scripts.stage9_dense_recovery import adapt_candidates, canonical, digest, read, signed, write
from vibiomir.retrieval_contracts import chunk_snapshot, source_parts, queries
from vibiomir.io import file_hash
from vibiomir.stage9_pipeline import prepare, rerank_worker, validate_worker, export, validate_results, bundle
from scripts.stage9_expand_support import package


class DensePipelineTest(unittest.TestCase):
    def test_adapter_prepare_worker_resume_validate_package(self):
        f = fixtures.Stage9PipelineTest()
        f.setUp()
        self.addCleanup(f.doCleanups)
        expected = chunk_snapshot(f.chunk_dir)
        plan = signed(dict(source=expected, parts=source_parts(f.chunk_dir, expected)))
        source = f.root / 'dense_v1'
        identity = dict(operation='dense-results', plan_signature=plan['signature'], query_sha256=file_hash(f.queries))
        signature = digest(canonical(identity).encode())
        write(source / 'manifest.json', dict(identity=identity, signature=signature, state='complete',
                                            corpus_complete=True, validation='not_run'))
        for q in queries(f.queries):
            rows = read(f.candidate_dir / f"query-{q['id']}.json")['candidates']
            write(source / 'candidates' / f"query-{q['id']}.json",
                  signed(dict(id=q['id'], query=q['query'], run_signature=signature, candidates=rows)))
        adapted = f.root / 'adapted'
        report = adapt_candidates(source, f.chunk_dir, plan, expected, f.queries, queries(f.queries), adapted)
        self.assertEqual(report['validation'], 'passed')
        f.c['workers'] = 1
        f.c['branches'][0]['candidate_dir'] = str(adapted)
        write(f.config, f.c)
        prepare(f.config)
        bundle(f.prepared, f.root / 'prepared.zip')
        worker = f.workers / 'worker_0'
        result = rerank_worker(f.prepared, worker, 0, stop_after_queries=1,
                              backup_dir=f.root / 'backup', scorer_factory=fixtures.FixtureScorer)
        self.assertEqual(result['state'], 'paused')
        rerank_worker(f.prepared, worker, 0, backup_dir=f.root / 'backup', scorer_factory=fixtures.FixtureScorer)
        validate_worker(f.prepared, worker, 0)
        output = f.root / 'results'
        export(f.prepared, output, mode='reranked', workers_dir=f.workers)
        validate_results(f.prepared, output, chunk_dir=f.chunk_dir, queries_path=f.queries, workers_dir=f.workers)
        archive = package(output, [q['id'] for q in queries(f.queries)], {1, 2, 3}, f.root / 'submission')
        self.assertTrue(archive.is_file())
