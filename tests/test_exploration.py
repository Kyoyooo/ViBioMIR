"""Small integration fixtures for the metrics that affect downstream retrieval."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from exploration.common import language_hint, normalize
from exploration.corpus import profile_corpus
from exploration.inventory import inventory
from exploration.references import profile_references, resolve_file
from exploration.report import build_report


class ExplorationTest(unittest.TestCase):
    def test_normalization_order_and_language_ambiguity(self):
        self.assertEqual(normalize(" Ａ\t&amp;\n THẬN  "), "a & thận")
        self.assertEqual(normalize("&nbsp; X"), "x")
        self.assertEqual(language_hint("kidney stone"), "latin_undetermined")
        self.assertEqual(language_hint("sỏi thận"), "vi_diacritics")
        self.assertEqual(language_hint("肾结石治疗"), "cjk_likely_zh")

    def test_end_to_end_counts_mapping_and_optional_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "Data", Path(tmp) / "report"
            shard = root / "ViBioMIR" / "shards" / "A" / "example.org"
            shard.mkdir(parents=True)
            texts = ["Ｓỏi &amp; THẬN", "sỏi & thận", "", "Nội dung khác"]
            data = []
            for i, (doc_id, text) in enumerate(zip([1, 2, 3, 1], texts)):
                data.append({"id": doc_id, "url": f"https://example.org/{doc_id}",
                             "domain": "example.org", "status": "403" if doc_id == 3 else "200",
                             "text": text, "text_len": len(text), "title": "title",
                             "blocks": "not json" if i == 0 else '[["p", "abc"]]'})
            pq.write_table(pa.Table.from_pylist(data), shard / "part.parquet")
            state = root / "ViBioMIR" / "state_A.sqlite3"
            with sqlite3.connect(state) as db:
                db.execute("CREATE TABLE crawl_state(id INTEGER PRIMARY KEY, domain TEXT, status TEXT)")
                db.executemany("INSERT INTO crawl_state VALUES (?,?,?)",
                               [(1, "example.org", "200"), (2, "example.org", "200"),
                                (3, "example.org", "403"), (4, "example.org", "200")])
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            inventory(root, out)
            summary = profile_corpus(root, out, batch_size=2, inspect_blocks=True)
            self.assertEqual(summary["rows"], 4)
            self.assertEqual(summary["unique_doc_ids"], 3)
            self.assertEqual(summary["duplicates"]["normalized_text_sha256"], {"groups": 1, "extra_rows": 1})
            self.assertEqual(summary["duplicates"]["id"]["groups"], 1)
            self.assertEqual(summary["ids_with_conflicting_text"], 1)
            self.assertEqual(summary["quality_flags"]["invalid_blocks"], 1)
            self.assertEqual(summary["quality_flags"]["empty_text"], 1)
            reconciliation = summary["crawler_reconciliation"][0]
            self.assertEqual(reconciliation["state_ids_without_shard"], 1)
            self.assertEqual(reconciliation["state_2xx_ids_without_nonempty_2xx_text"], 1)
            missing = profile_references(root, out)
            self.assertEqual(missing["queries"]["status"], "missing")
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
            self.assertEqual(set(before), {p for p in root.rglob("*") if p.is_file()})

            pq.write_table(pa.table({"id": [10, 11, 12], "query": [" Sỏi thận ", "sỏi thận", ""]}), root / "query.parquet")
            pq.write_table(pa.table({"id": [1, 2, 4], "url": [f"https://example.org/{i}" for i in [1, 2, 4]]}),
                           root / "links_corpus.parquet")
            refs = profile_references(root, out)
            self.assertEqual(refs["queries"]["duplicate_normalized_query_groups"], 1)
            self.assertEqual(refs["queries"]["quality_flags"]["empty_query"], 1)
            self.assertEqual(refs["official_links"]["official_ids_with_nonempty_2xx_text"], 2)
            self.assertEqual(refs["official_links"]["official_ids_without_shard"], 1)
            self.assertEqual(refs["official_links"]["shard_ids_outside_official_corpus"], 1)
            self.assertEqual(refs["official_links"]["official_ids_with_only_mismatched_source_urls"], 0)
            self.assertTrue((out / "official_coverage_by_domain.csv").exists())
            pq.write_table(pa.table({"id": [1, 2, 4], "url": ["https://example.org/1",
                           "https://example.org/changed", "https://example.org/4"]}), root / "links_corpus.parquet")
            mismatched = profile_references(root, out)
            self.assertEqual(mismatched["official_links"]["official_ids_with_only_mismatched_source_urls"], 1)
            build_report(out)
            self.assertTrue((out / "REPORT.md").exists())
            self.assertTrue((out / "top_domains.svg").exists())
            with self.assertRaises(FileExistsError):
                profile_corpus(root, out)

    def test_partial_scan_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "Data", Path(tmp) / "out"
            root.mkdir()
            for i in range(2):
                pq.write_table(pa.table({"id": [i], "text": ["abc"]}), root / f"{i}.parquet")
            result = profile_corpus(root, out, max_files=1)
            self.assertTrue(result["partial"])
            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["available_corpus_files"], 2)

    def test_metadata_only_shard_is_counted_as_missing_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "Data", Path(tmp) / "out"
            shard = root / "shards" / "B" / "example.org"
            shard.mkdir(parents=True)
            pq.write_table(pa.table({"id": [42], "status": ["403"],
                                    "url": ["https://example.org/42"]}), shard / "failed.parquet")
            result = profile_corpus(root, out)
            self.assertFalse(result["partial"])
            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["quality_flags"]["missing_text_column"], 1)
            self.assertEqual(result["quality_flags"]["empty_text"], 1)

    def test_official_filename_and_ambiguous_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            official = root / "links_corpus_vi_en_zh.parquet"
            legacy = root / "links_corpus.parquet"
            aliases = (official.name, legacy.name)
            official.touch()
            self.assertEqual(resolve_file(root, None, aliases), official)
            legacy.touch()
            with self.assertRaises(ValueError):
                resolve_file(root, None, aliases)
            self.assertEqual(resolve_file(root, legacy, aliases), legacy)


if __name__ == "__main__":
    unittest.main()
