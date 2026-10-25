"""Metadata evidence tests. Run manually; no model, network, or corpus required."""
import json
import unittest

from vibiomir.metadata import extract_metadata, normalize_date


class MetadataTest(unittest.TestCase):
    def fields(self, row, domain="example.org", options=None):
        return extract_metadata(row, domain, options)["fields"]

    def test_description_and_crawl_time_do_not_become_abstract_or_publication(self):
        fields = self.fields({"desc": "Kidney stone overview", "ts": "2025-10-23",
            "text": "Patient admitted 2020-01-01. Related news: 2026-08-01. BS Nguyễn A tư vấn."})
        self.assertEqual(fields["description"]["value"], "Kidney stone overview")
        for key in ("abstract", "published_date", "updated_date", "authors", "category"):
            self.assertIsNone(fields[key]["value"])
            self.assertEqual(fields[key]["candidates"], [])

    def test_date_precision_and_ambiguity(self):
        for raw, locale, normalized, precision in [
            ("2020", "unknown", "2020", "year"),
            ("2020-02", "unknown", "2020-02", "month"),
            ("2020年2月29日", "zh", "2020-02-29", "day"),
            ("29/02/2020", "vi", "2020-02-29", "day"),
            ("2020/2/29", "unknown", "2020-02-29", "day"),
            ("2020-02-29T03:04:05Z", "unknown", "2020-02-29T03:04:05+00:00", "time")]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_date(raw, locale), (normalized, precision, None))
        for raw in ("01/02/2020", "2021-02-29", "not a date", "2020-13", "0000"):
            self.assertIsNone(normalize_date(raw)[0])
        field = self.fields({"published_date": "01/02/2020"})["published_date"]
        self.assertIsNone(field["value"])
        self.assertEqual(field["candidates"][0]["raw_value"], "01/02/2020")
        self.assertEqual(field["candidates"][0]["issue"], "ambiguous_date_order")

    def test_explicit_html_conflicts_and_source_precedence(self):
        row = {"title": "Original title", "html_trim": '''<title>Site title</title>
            <meta property="og:title" content="Different title">
            <meta name="author" content="Nguyễn A"><meta name="citation_author" content="王医生">
            <meta property="article:published_time" content="2021-03-04">
            <meta name="citation_abstract" content="Explicit abstract &amp; findings">'''}
        fields = self.fields(row)
        self.assertEqual(fields["title"]["value"], "Original title")
        self.assertTrue(fields["title"]["conflict"])
        self.assertEqual(len(fields["title"]["candidates"]), 3)
        self.assertEqual(fields["authors"]["value"], ["Nguyễn A", "王医生"])
        self.assertEqual(fields["published_date"]["value"], "2021-03-04")
        self.assertEqual(fields["abstract"]["value"], "Explicit abstract & findings")
        self.assertEqual(fields["title"]["selected"]["locator"], {"column": "title"})

    def test_jsonld_article_author_references_and_non_article_dates(self):
        graph = {"@graph": [
            {"@type": "Organization", "datePublished": "1900", "name": "Hospital"},
            {"@id": "#person", "@type": "Person", "name": "王医生"},
            {"@type": "ScholarlyArticle", "headline": "肾结石", "author": {"@id": "#person"},
             "datePublished": "2020", "abstract": "Original abstract"}]}
        markup = '<script type="application/ld+json">' + json.dumps(graph) + '</script>'
        fields = self.fields({"html_trim": markup})
        self.assertEqual(fields["published_date"]["value"], "2020")
        self.assertEqual(len(fields["published_date"]["candidates"]), 1)
        self.assertEqual(fields["published_date"]["selected"]["date_precision"], "year")
        self.assertEqual(fields["authors"]["value"], ["王医生"])
        self.assertEqual(fields["authors"]["selected"]["locator"]["raw_author"], {"@id": "#person"})
        self.assertEqual(fields["abstract"]["value"], "Original abstract")

    def test_body_candidates_are_unselected_and_source_mapped(self):
        for text, domain, key, expected in [
            ("Title\n  Bài viết bởi BS Nguyễn An\nNội dung", "vinmec.com", "authors", "BS Nguyễn An"),
            ("肾结石\n2021-03-04 08:09:10 家庭医生在线\n正文", "familydoctor.com.cn", "published_date", "2021-03-04 08:09:10")]:
            field = self.fields({"text": text}, domain)[key]
            self.assertIsNone(field["value"])
            candidate = field["candidates"][0]
            self.assertFalse(candidate["selectable"])
            span = candidate["locator"]
            self.assertEqual(text[span["start"]:span["end"]], expected)
            self.assertEqual(candidate["raw_value"], expected)
        self.assertEqual(self.fields({"text": "2021-03-04 家庭医生在线"})["published_date"]["candidates"], [])

    def test_malformed_jsonld_leaves_explicit_metadata_available(self):
        result = extract_metadata({"html_trim": '<meta name="description" content="Summary">'
            '<script type="application/ld+json">{broken}</script>'}, "example.org")
        self.assertIn("malformed_json_ld", result["issues"])
        self.assertEqual(result["fields"]["description"]["value"], "Summary")
        self.assertIsNone(result["fields"]["published_date"]["value"])


if __name__ == "__main__":
    unittest.main()
