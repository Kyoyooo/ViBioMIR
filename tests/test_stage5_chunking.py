"""Offline S05 fixtures. Run manually; no GPU, network or model download."""
import copy
import unittest

from vibiomir.chunking import chunk_document, settings, structure_units
from vibiomir.chunk_pipeline import prepare_packet, verify_chunk
from vibiomir.io import canonical_json, text_hash


class CharacterTokenizer:
    """Intentionally counts code points, including spaces; not a production tokenizer."""
    is_fast = True

    def encode(self, text, add_special_tokens=True, truncation=False):
        return ([0] if add_special_tokens else []) + list(range(len(text))) + ([1] if add_special_tokens else [])

    def __call__(self, text, **kwargs):
        return {"offset_mapping": [(i, i + 1) for i in range(len(text))]}


def packet(text="Không sốt; liều 5 mg.\n肾结石，不建议自行用药。\nNo fever &amp; no pain.", doc_id=1):
    region = {"region_id": "r", "text": text, "source_kind": "stage3_text",
              "source_locator_json": '{"column":"text"}', "source_start": 12,
              "source_end": 12 + len(text), "kind": "legacy_text"}
    region["units"] = structure_units(region)
    return {"doc_id": doc_id, "derived_version_id": "clean-v1", "parent_version_id": "raw-v1",
            "availability": "content_candidate", "title": "Clinical title", "language_hint": "mixed",
            "unmapped_blocks": 0, "regions": [region]}


CONFIG = settings(dict(target_tokens=40, max_tokens=64, overlap_tokens=8,
                       min_tokens=16, context_tokens=8))


class ChunkingTest(unittest.TestCase):
    def setUp(self):
        self.tokenizer = CharacterTokenizer()

    def chunk(self, source, strategy="structure", config=CONFIG, encoder=None):
        return chunk_document(source, self.tokenizer, config, strategy, "identity", encoder)

    def test_three_chunkers_unicode_exact_slices_and_hard_caps(self):
        source = packet(("Tiếng Việt 😀 &amp; Không dùng 5 mg. 中文用药。\n" * 12))
        calls = []
        def encoder(texts):
            calls.append(len(texts))
            return [[1.0, 0.0] if i % 2 else [0.0, 1.0] for i, _ in enumerate(texts)]
        for strategy in ("fixed", "structure", "semantic"):
            with self.subTest(strategy=strategy):
                result = self.chunk(source, strategy, encoder=encoder)
                self.assertGreater(len(result["chunks"]), 1)
                covered = set()
                for chunk in result["chunks"]:
                    verify_chunk(chunk, source, source["regions"][0], self.tokenizer, CONFIG, "identity", strategy)
                    self.assertLessEqual(chunk["token_count"], 64)
                    self.assertEqual(chunk["chunk_text"], source["regions"][0]["text"][chunk["region_start"]:chunk["region_end"]])
                    covered.update(range(chunk["region_start"], chunk["region_end"]))
                self.assertTrue(all(i in covered for i, c in enumerate(source["regions"][0]["text"]) if not c.isspace()))
        self.assertTrue(calls)

    def test_short_question_answer_is_one_verbatim_chunk(self):
        source = packet("Question: Fever?\nAnswer: No fever; 5 mg.")
        for strategy in ("fixed", "structure", "semantic"):
            config = settings(dict(target_tokens=100, max_tokens=128, overlap_tokens=8, min_tokens=16, context_tokens=8))
            result = self.chunk(source, strategy, config=config, encoder=lambda texts: [[1., 0.]] * len(texts))
            self.assertEqual(len(result["chunks"]), 1)
            self.assertEqual(result["chunks"][0]["chunk_text"], source["regions"][0]["text"])

    def test_question_answer_between_target_and_maximum_stays_together(self):
        source = packet("Question: Fever?\nAnswer: No fever; dose 5 mg; hydrate.")
        for strategy in ("fixed", "structure", "semantic"):
            result = self.chunk(source, strategy, encoder=lambda texts: [[1., 0.]] * len(texts))
            self.assertEqual(len(result["chunks"]), 1)
            self.assertGreater(result["chunks"][0]["token_count"], CONFIG["target_tokens"])
            self.assertLessEqual(result["chunks"][0]["token_count"], CONFIG["max_tokens"])

    def test_long_question_answer_retains_question_as_context(self):
        source = packet("Question: Fever?\n" + "No fever. Original clinical answer.\n" * 10)
        source["title"] = None
        result = self.chunk(source)
        self.assertGreater(len(result["chunks"]), 1)
        prefix = result["chunks"][-1]["context_prefix"].strip()
        self.assertTrue(prefix)
        self.assertTrue("Question: Fever?".startswith(prefix))
        self.assertNotIn("Question:", result["chunks"][-1]["chunk_text"])

    def test_headings_and_table_rows_remain_in_section_and_source(self):
        text = "Treatment\n" + "Dose | Value\n5 mg | Do not exceed\n" * 5 + "Prevention\nNo smoking.\n" * 3
        source = packet(text)
        from vibiomir.document_contracts import mapped_blocks
        blocks = [["h", "Treatment"], ["tr", "Dose | Value"], ["tr", "5 mg | Do not exceed"], ["h", "Prevention"]]
        source["regions"][0]["source_start"] = 0
        source["regions"][0]["source_end"] = len(text)
        source["regions"][0]["units"] = structure_units(source["regions"][0], list(mapped_blocks(text, blocks)))
        boundary = text.index("Prevention")
        for chunk in self.chunk(source)["chunks"]:
            self.assertFalse(chunk["region_start"] < boundary < chunk["region_end"])
        # Long individual table rows still split under the hard token cap.
        long_row = packet("Row | " + "Long content " * 100)
        long_row["regions"][0]["units"][0]["kind"] = "tr"
        self.assertTrue(all(c["token_count"] <= 64 for c in self.chunk(long_row)["chunks"]))

    def test_does_not_cross_removed_gaps_and_distinguishes_versions_and_ids(self):
        source = packet("Original answer before UI.")
        other = copy.deepcopy(source["regions"][0])
        other.update(region_id="r2", source_start=100, source_end=100 + len(other["text"]))
        source["regions"].append(other)
        chunks = self.chunk(source)["chunks"]
        self.assertEqual({c["region_id"] for c in chunks}, {"r", "r2"})
        self.assertEqual(chunks, self.chunk(source)["chunks"])
        changed = copy.deepcopy(source)
        changed["doc_id"] = 2
        self.assertNotEqual(chunks[0]["chunk_id"], self.chunk(changed)["chunks"][0]["chunk_id"])
        changed = copy.deepcopy(source)
        changed["derived_version_id"] = "clean-v2"
        self.assertNotEqual(chunks[0]["chunk_id"], self.chunk(changed)["chunks"][0]["chunk_id"])

    def test_one_character_offset_version_and_token_tampering_rejected(self):
        source = packet()
        chunk = self.chunk(source)["chunks"][0]
        for key, value in [("chunk_text", "X" + chunk["chunk_text"][1:]),
                           ("source_start", chunk["source_start"] + 1), ("parent_version_id", "wrong"),
                           ("token_count", 999), ("retrieval_text", "invented")]:
            with self.subTest(key=key):
                bad = {**chunk, key: value}
                with self.assertRaises(ValueError):
                    verify_chunk(bad, source, source["regions"][0], self.tokenizer, CONFIG, "identity", "structure")

    def test_missing_semantic_encoder_and_bad_configuration_fail_early(self):
        with self.assertRaisesRegex(ValueError, "GPU encoder"):
            self.chunk(packet(), "semantic")
        for config in ({"overlap_tokens": 384}, {"max_tokens": 20}, {"target_tokens": True},
                       {"semantic_threshold": float("nan")}, {"max_token": 512}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                settings(config)

    def test_prepare_packet_recovers_exact_legacy_block_and_checks_source(self):
        parent = {"doc_id": 1, "version_id": "raw", "blocks_raw_json": '[["p","No fever."]]', "evidence_text": ""}
        selection = {"selected_derived_version_id": "derived", "selected_parent_id": "raw", "availability": "content_candidate"}
        version = {"doc_id": 1, "derived_version_id": "derived", "parent_version_id": "raw", "availability": "content_candidate", "metadata_json": '{"fields":{}}'}
        region = {**{key: version[key] for key in ("doc_id", "derived_version_id", "parent_version_id")},
                  "region_id": "r", "kind": "p", "source_kind": "legacy_block", "source_locator_json": canonical_json({"index": 0}),
                  "source_start": 0, "source_end": 9, "text": "No fever.", "text_sha256": text_hash("No fever.")}
        result = {"version": version, "regions": [region]}
        self.assertEqual(prepare_packet(1, selection, result, parent)["regions"][0]["text"], "No fever.")
        result["regions"][0]["text"] = "Yes fever"
        with self.assertRaisesRegex(ValueError, "slice/hash"):
            prepare_packet(1, selection, result, parent)


if __name__ == "__main__":
    unittest.main()
