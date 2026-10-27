"""Owner-run CPU fixture/regression suite with durable, source-bound evidence."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vibiomir.io import file_hash, write_json, writer_lock


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.success_ids = []

    def addSuccess(self, test):
        self.success_ids.append(test.id())
        super().addSuccess(test)


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/stage5_review_tests")
    parser.add_argument("--tokenizer-dir", type=Path, default=ROOT / "artifacts/tokenizers/bge_m3_stage5")
    args = parser.parse_args()
    os.environ.update(USE_TORCH="0", USE_TF="0", USE_FLAX="0", TOKENIZERS_PARALLELISM="false", VIBIOMIR_RUN_CUDA_TESTS="0")
    if (args.tokenizer_dir / "REGISTRY.json").exists():
        os.environ["VIBIOMIR_TOKENIZER_DIR"] = str(args.tokenizer_dir.resolve())
    else:
        os.environ.pop("VIBIOMIR_TOKENIZER_DIR", None)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with writer_lock(args.output_dir):
        index = 1
        while (args.output_dir / f"run-{index:04d}").exists():
            index += 1
        run = args.output_dir / f"run-{index:04d}"
        run.mkdir()
        with (run / "tests.log").open("w", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_stage5*.py")
            ids = list(test_ids(suite))
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
        meaning = [name for name in ids if name.startswith("test_stage5_chunking.ChunkingTest.")]
        report = {"successful": result.wasSuccessful(), "run_directory": str(run), "run": result.testsRun,
                  "passed": len(result.success_ids), "failed": len(result.failures), "errors": len(result.errors),
                  "skipped": [{"test": t.id(), "reason": reason} for t, reason in result.skipped],
                  "meaning_preservation_fixtures_passed": bool(meaning) and set(meaning) <= set(result.success_ids),
                  "meaning_fixture_ids": meaning, "gpu_used": False,
                  "source_code": {p.name: file_hash(p) for p in sorted((ROOT / "vibiomir").glob("*.py"))},
                  "test_code": {p.name: file_hash(p) for p in sorted((ROOT / "tests").glob("test_stage5*.py"))}}
        write_json(run / "RESULTS.json", report)
        write_json(args.output_dir / "LATEST.json", report)
    print(json.dumps({k: report[k] for k in ("successful", "run_directory", "run", "passed", "failed", "errors")}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
