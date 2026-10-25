"""Source-region, recovery and medical-meaning counterexamples; execute manually."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from exploration.audit_checks import inspect_record
from vibiomir.cleaning_rules import load_cleaning_config, failure_reason, title_body_diagnostic
from vibiomir.cleaning_source import ArticleHTML, html_units
from vibiomir.cleaning_transform import process_version
from vibiomir.documents import load_config, prepare_record
from vibiomir.validate_cleaning import verify_result
from test_stage3_documents import record

ROOT = Path(__file__).resolve().parents[1]


def config(approved=False):
    value = load_cleaning_config(ROOT / "configs/cleaning_stage4.json")
    if approved:
        for rule in value["rules"]:
            rule["review_status"] = "approved"
    return value


def parent(text="Original medical text.", domain="example.org", **changes):
    row = record(**{"text": text, "url": "https://" + domain + "/article", **changes})
    diagnostic = inspect_record(row, "fixture.parquet", "a" * 64, 0, 0)[0]
    diagnostic["text_sha256"] = diagnostic["verbatim_sha256"]
    diagnostic["flags_json"] = json.dumps(diagnostic["flags"])
    input_row, value, _ = prepare_record(row, diagnostic, "fixture.parquet", "a" * 64, 0, 0, load_config())
    value["representative_record_id"] = input_row["record_id"]
    return value


class SourceRegionTest(unittest.TestCase):
    def test_inline_teaser_creates_separate_exact_regions_and_preserves_clinical_values(self):
        before = "Không dùng ５０ mg; 1-2 mg; glucose 5 mmol/L; ngày 20/03/2013.\n" * 3
        teaser = "» Unrelated story\n"
        after = "Không tự ngừng thuốc. 肾结石无发热。\n"
        source = parent(before + teaser + after, "baonghean.vn")
        saved = copy.deepcopy(source)
        result = process_version(source, config())
        self.assertEqual([r["text"] for r in result["regions"]], [before, after])
        self.assertEqual(result["regions"][1]["source_start"], len(before + teaser))
        self.assertEqual(result["version"]["removed_chars"], len(teaser))
        self.assertEqual(result["transformations"][0]["decision"], "proposed_removal")
        self.assertEqual(source, saved)
        verify_result(result, source, config(), None)

    def test_short_qa_clinical_repetitions_cautions_prices_and_tables_are_kept(self):
        text = "问：无发热，如何处理？\n答：不要自行停药。\n不宜使用 50 mg。\n不宜使用 50 mg。\nVắc xin: 500.000 đồng; lịch 0-1-6 tháng."
        result = process_version(parent(text, "120ask.com"), config())
        self.assertTrue(result["version"]["content_candidate"])
        self.assertEqual(result["regions"][0]["text"], text)
        self.assertEqual(result["version"]["removed_chars"], 0)
        self.assertGreater(json.loads(result["version"]["diagnostics_json"])["repetitions"]["repeated_line_occurrences"], 0)

    def test_only_standalone_domain_control_is_removed(self):
        prefix = "健康咨询描述：孩子语言发育较慢，医生建议进行检查。\n" * 5
        body = prefix + "投诉\n" + "答：当天可以取得结果。患者提到投诉处理过程。"
        result = process_version(parent(body, "120ask.com"), config())
        self.assertEqual(result["version"]["removed_chars"], len("投诉\n"))
        self.assertIn("患者提到投诉处理过程", result["regions"][-1]["text"])
        other = process_version(parent(body, "other.example"), config())
        self.assertEqual(other["version"]["removed_chars"], 0)

    def test_protected_numbers_negations_and_units_veto_a_tail_cut(self):
        body = "Clinical context and original article paragraphs.\n" * 5 + "相关文章\n不宜使用 50 mg。"
        source = parent(body, "cnkang.com")
        result = process_version(source, config())
        self.assertEqual(result["version"]["removed_chars"], 0)
        self.assertEqual(result["transformations"][0]["decision"], "vetoed")
        self.assertIn("protected_clinical_signal_veto", json.loads(result["transformations"][0]["reason_json"]))
        self.assertEqual(result["regions"][0]["text"], body)
        verify_result(result, source, config(), None)

    def test_large_or_early_removals_are_vetoed(self):
        for text in ("相关文章\nShort title", "Body.\n" * 20 + "相关文章\n" + "Related story title.\n" * 100):
            with self.subTest(text=text[:20]):
                result = process_version(parent(text, "cnkang.com"), config())
                self.assertEqual(result["version"]["removed_chars"], 0)
                self.assertTrue(any(t["decision"] == "vetoed" for t in result["transformations"]))

    def test_each_initial_rule_has_a_synthetic_positive_and_out_of_domain_counterexample(self):
        prefix = "Original clinical context; no fever; do not use 50 mg.\n" * 6
        cases = [
            ("120ask.com", "投诉\n", "120ask_complaint_control"),
            ("familydoctor.com.cn", "举报/反馈\n", "familydoctor_feedback_control"),
            ("cnkang.com", "相关文章\nRelated story title.", "cnkang_related_tail"),
            ("familydoctor.com.cn", "最新文章\nRelated story title.", "familydoctor_related_tail"),
            ("vov.vn", "This site is protected by reCAPTCHA.\n", "vov_recaptcha_footer"),
            ("vinmec.com", "Để đặt lịch khám tại viện, Quý khách vui lòng bấm số HOTLINE .\n", "vinmec_booking_footer"),
            ("baonghean.vn", "» Unrelated story\n", "baonghean_inline_related_link"),
            ("thanhnien.vn", ">> Related story\n", "thanhnien_related_link")]
        for domain, control, rule_id in cases:
            with self.subTest(rule=rule_id):
                result = process_version(parent(prefix + control, domain), config())
                self.assertEqual(result["version"]["removed_chars"], len(control))
                self.assertEqual({t["rule_id"] for t in result["transformations"]}, {rule_id})
                self.assertEqual(result["regions"][0]["text"], prefix)
                untouched = process_version(parent(prefix + control, "other.example"), config())
                self.assertEqual(untouched["version"]["removed_chars"], 0)

    def test_vinmec_clinical_booking_conditions_and_familydoctor_header_date_survive(self):
        prefix = "Original medical body with clinical history.\n" * 6
        caution = "Để đặt lịch khám tại viện, Quý khách vui lòng không tự dùng 50 mg.\n"
        result = process_version(parent(prefix + caution, "vinmec.com"), config())
        self.assertEqual(result["version"]["removed_chars"], 0)
        self.assertEqual(result["transformations"][0]["decision"], "vetoed")
        header = "新生儿大便异常\n2010-09-09 08:25:40 家庭医生在线\n举报/反馈\n" + prefix
        source = parent(header, "familydoctor.com.cn")
        result = process_version(source, config())
        self.assertIn("2010-09-09 08:25:40", result["regions"][0]["text"])
        self.assertIsNone(json.loads(result["version"]["metadata_json"])["fields"]["published_date"]["value"])

    def test_recaptcha_footer_does_not_quarantine_substantive_article(self):
        prefix = "Original medical reporting with useful clinical context.\n" * 5
        body = prefix + "This site is protected by reCAPTCHA.\n"
        result = process_version(parent(body, "vov.vn"), config())
        self.assertTrue(result["version"]["content_candidate"])
        self.assertEqual(result["regions"][0]["text"], prefix)
        self.assertIsNone(failure_reason(body))
        self.assertEqual(failure_reason("Verify you are human"), "exact_language_or_access_gate")

    def test_binary_signature_and_soft_404_are_not_encoding_rewrites(self):
        for text, changes in [("404 Not Found\nnginx", {}),
                ("Exif Adobe " + "\ufffd" * 60, {}),
                ("\ufffd" * 60, {"final_url": "https://example.org/image.jpg"})]:
            with self.subTest(text=text[:20]):
                source = parent(text, **changes)
                result = process_version(source, config())
                self.assertFalse(result["version"]["content_candidate"])
                self.assertEqual(result["version"]["availability"], "quarantine")
                self.assertEqual(result["regions"], [])
                self.assertEqual(result["version"]["quarantined_chars"], len(text))
                self.assertEqual(source["evidence_text"], text)

    def test_one_replacement_character_is_kept_and_flagged(self):
        text = "Clinical source with one lost character \ufffd and dose 5 mg."
        result = process_version(parent(text), config())
        self.assertEqual(result["regions"][0]["text"], text)
        self.assertTrue(result["version"]["content_candidate"])
        self.assertIn("replacement_characters_require_review_no_repair", json.loads(result["version"]["quality_flags_json"]))

    def test_invalid_source_url_retains_parent_but_has_no_content_selection(self):
        source = parent("Original text remains in the parent store.", url="not-a-url")
        result = process_version(source, config())
        self.assertFalse(result["version"]["content_candidate"])
        self.assertEqual(result["version"]["availability"], "quarantine")
        self.assertEqual(result["version"]["quarantined_chars"], len(source["evidence_text"]))
        self.assertEqual(result["regions"], [])
        verify_result(result, source, config(), None)

    def test_missing_text_recovers_original_blocks_without_joining_source_coordinates(self):
        blocks = [["h", "Điều trị"], ["p", "Không dùng 50 mg."], ["tr", "Hb | 12 g/dL"]]
        source = parent("", blocks=json.dumps(blocks, ensure_ascii=False))
        result = process_version(source, config())
        self.assertEqual(result["version"]["extraction_method"], "recovered_legacy_blocks")
        self.assertEqual([r["text"] for r in result["regions"]], [b[1] for b in blocks])
        for index, region in enumerate(result["regions"]):
            self.assertEqual(region["source_start"], 0)
            self.assertEqual(json.loads(region["source_locator_json"])["index"], index)
        verify_result(result, source, config(), None)

    def test_existing_text_is_not_replaced_by_html_or_description(self):
        loader = Mock(side_effect=AssertionError("Existing text must not load HTML"))
        source = parent("Useful short answer.", html_trim="<article>Longer unrelated HTML.</article>", desc="Description")
        result = process_version(source, config(), html_loader=loader)
        loader.assert_not_called()
        self.assertEqual(result["version"]["extraction_method"], "retained_stage3_text")
        empty = process_version(parent("", blocks="", desc="Description is not an abstract"), config())
        self.assertEqual(empty["version"]["availability"], "unavailable")
        self.assertEqual(empty["regions"], [])
        self.assertIsNone(json.loads(empty["version"]["metadata_json"])["fields"]["abstract"]["value"])

    def test_vinmec_title_and_language_gate_are_not_a_medical_body(self):
        text = "Kidney stones\nThis content is not available in English."
        source = parent(text, "vinmec.com", title="Kidney stones")
        result = process_version(source, config())
        self.assertFalse(result["version"]["content_candidate"])
        self.assertEqual(result["version"]["availability"], "quarantine")
        self.assertEqual(result["regions"], [])
        mention = text + "\nOriginal Vietnamese clinical paragraph remains available: Không tự ngừng thuốc."
        kept = process_version(parent(mention, "vinmec.com", title="Kidney stones"), config())
        self.assertTrue(kept["version"]["content_candidate"])

    def test_cjk_and_mixed_language_mismatch_is_diagnostic_only(self):
        self.assertEqual(title_body_diagnostic("胃炎", "幽门螺杆菌感染可能导致胃炎。")["overlap"], 1)
        source = parent("幽门螺杆菌感染可能导致胃炎。", "cnkang.com", title="胃炎")
        source["quality_flags_json"] = json.dumps(["possible_title_body_mismatch"])
        result = process_version(source, config())
        self.assertTrue(result["version"]["content_candidate"])
        self.assertIn("legacy_title_mismatch_counterexample", json.loads(result["version"]["quality_flags_json"]))
        mixed = process_version(parent("English clinical narrative with no Chinese heading.", "cnkang.com", title="肾结石"), config())
        self.assertTrue(mixed["version"]["content_candidate"])
        self.assertIn("title_body_low_overlap_review_only", json.loads(mixed["version"]["quality_flags_json"]))

    def test_apply_requires_reviewed_rules_and_changes_use_applied_label(self):
        body = "Article context.\n" * 15 + "投诉\nAnswer."
        source = parent(body, "120ask.com")
        with self.assertRaisesRegex(ValueError, "reviewed rules"):
            process_version(source, config(), mode="apply")
        applied = process_version(source, config(True), mode="apply")
        self.assertEqual(applied["transformations"][0]["decision"], "applied_removal")
        self.assertFalse(applied["version"]["is_final"])

    def test_same_source_and_policy_reproduce_hash_and_preserve_nested_entities(self):
        source = parent("Không dùng ５０ mg &amp;amp; nước. 肾结石。")
        first, second = process_version(source, config()), process_version(source, config())
        self.assertEqual(first, second)
        self.assertEqual(first["regions"][0]["text"], source["evidence_text"])
        self.assertIn("&amp;", first["regions"][0]["normalized_text"])


class HTMLRecoveryTest(unittest.TestCase):
    def payload(self, markup):
        return {"html": markup, "locator": {"column": "html_trim", "record_id": "fixture-record"}}

    def test_parser_column_attribute_and_absolute_spans_survive_fragmented_multiline_html(self):
        """Regression: HTMLParser.offset is an integer, not our source-position method."""
        pieces = ["<article>\n<p>Không dùng &#", "53; mg &a", "mp; nước.</p>\n<p>肾结石。</p></article>"]
        markup = "".join(pieces)
        parser = ArticleHTML(markup)
        self.assertIsInstance(parser.offset, int)
        for piece in pieces:
            parser.feed(piece)
        parser.close()
        self.assertIsInstance(parser.offset, int)
        nodes = [node for node in parser.nodes if markup[node["start"]:node["end"]].strip()]
        self.assertEqual([markup[node["start"]:node["end"]] for node in nodes],
                         ["Không dùng &#53; mg &amp; nước.", "肾结石。"])
        self.assertEqual(nodes[0]["start"], markup.index("Không dùng"))
        self.assertEqual(nodes[1]["start"], markup.index("肾结石"))

    def test_explicit_article_nodes_keep_raw_entity_slices_and_skip_navigation(self):
        markup = '<nav>Global menu</nav><article><h2>Điều trị</h2><p>No fever &amp; no 5 mg.</p><nav>Related menu</nav><p>肾结石治疗。</p></article>'
        source = parent("", blocks="", html_trim=markup)
        loader = lambda _: self.payload(markup)
        result = process_version(source, config(), html_loader=loader)
        self.assertEqual(result["version"]["extraction_method"], "recovered_html_article")
        self.assertEqual([r["text"] for r in result["regions"]], ["Điều trị", "No fever &amp; no 5 mg.", "肾结石治疗。"])
        for region in result["regions"]:
            self.assertEqual(markup[region["source_start"]:region["source_end"]], region["text"])
        verify_result(result, source, config(), loader)

    def test_multiple_articles_body_only_and_hidden_roots_do_not_become_main_content(self):
        for markup in ("<body>Whole page navigation and articles.</body>",
                       "<main><article>A</article><article>B</article></main>",
                       "<article hidden>Hidden content.</article>"):
            units, issue = html_units(self.payload(markup), "parent", 5000)
            self.assertEqual(units, [])
            self.assertIsNotNone(issue)

    def test_inline_tags_remain_separate_mapped_regions_and_size_limit_is_explicit(self):
        markup = '<main><p>No <strong>fever</strong> &amp; no 5 mg.</p><script>invented()</script></main>'
        units, issue = html_units(self.payload(markup), "parent", 5000)
        self.assertIsNone(issue)
        self.assertEqual([u["text"] for u in units], ["No ", "fever", " &amp; no 5 mg."])
        self.assertTrue(all(markup[u["source_start"]:u["source_end"]] == u["text"] for u in units))
        self.assertEqual(html_units(self.payload(markup), "parent", 5), ([], "html_size_limit_review"))


if __name__ == "__main__":
    unittest.main()
