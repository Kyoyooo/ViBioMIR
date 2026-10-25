"""Optional REAL tokenizer integration; entirely local, never downloads files."""
import os
from pathlib import Path
import unittest

from vibiomir.chunk_models import load_tokenizer
from vibiomir.chunking import chunk_document, settings
from vibiomir.chunk_pipeline import verify_chunk
from test_stage5_chunking import packet


@unittest.skipUnless(os.environ.get("VIBIOMIR_TOKENIZER_DIR"),
                     "Set VIBIOMIR_TOKENIZER_DIR after the manual tokenizer download")
class RealTokenizerTest(unittest.TestCase):
    def test_original_unicode_offsets_hard_budget_and_coverage(self):
        tokenizer, signature = load_tokenizer(Path(os.environ["VIBIOMIR_TOKENIZER_DIR"]))
        config = settings()
        for text in ["Tiếng Việt: không sốt, không dùng quá 5 mg.\n" * 150,
                     "😀肾结石，不建议自行用药。剂量5毫克。\n" * 150,
                     "No fever &amp; no pain. Dose 0.5 mg; do not exceed.\n" * 150]:
            source = packet(text)
            for strategy in ("fixed", "structure", "semantic"):
                with self.subTest(strategy=strategy, text=text[:15]):
                    row = chunk_document(source, tokenizer, config, strategy, signature,
                                         encoder=lambda items: [[1., 0.]] * len(items))
                    intervals = []
                    for chunk in row["chunks"]:
                        verify_chunk(chunk, source, source["regions"][0], tokenizer, config, signature, strategy)
                        intervals.append((chunk["region_start"], chunk["region_end"]))
                    end = 0
                    for start, stop in sorted(intervals):
                        self.assertFalse(text[end:start].strip())
                        end = max(end, stop)
                    self.assertFalse(text[end:].strip())


if __name__ == "__main__":
    unittest.main()
