"""Geometry/provenance checks, not financial-answer gold labels."""
import hashlib
import importlib.util
import socket
import unittest
from unittest.mock import patch

from verifin.data import DataError
from verifin.structured_documents import parse_structured_html, table_chunks


COMPLEX = """<!doctype html><html><head><meta charset="UTF-8"></head><body>
<h1>季度财报</h1><p>Visible <span hidden>HIDDEN</span> continuation.</p>
<div style="display: none">SECRET</div><ix:hidden>HIDDEN_XBRL</ix:hidden>
<table id="financials"><caption>USD millions; actual versus outlook</caption>
<thead><tr><th rowspan="2">Metric</th><th colspan="2">FY2025</th></tr>
<tr><th>Actual Q1</th><th>Outlook Q2</th></tr></thead><tbody>
<tr><td rowspan="2">Revenue</td><td>001,203</td><td>NA</td></tr>
<tr><td>(52.3)</td><td>10.5%</td></tr>
<tr><td>Gross margin</td><td>70.6%</td><td>71.0%</td></tr>
</tbody></table><p>Amounts are not validated financial facts.</p></body></html>"""


@unittest.skipUnless(importlib.util.find_spec("lxml") and importlib.util.find_spec("pandas"),
                     "optional HTML parser dependencies are not installed")
class StructuredDocumentTests(unittest.TestCase):
    def parse(self, raw=COMPLEX, **kwargs):
        return parse_structured_html(raw, source_url="fixture://complex", snapshot_path="complex.html", **kwargs)

    def test_percentages_keep_their_own_period_when_annual_column_comes_first(self):
        from verifin.table_cells import index_percentages
        source = """<table><tr><td></td><td>Year Ended</td><td colspan="2">Three Months Ended</td>
        <td rowspan="2">% Change</td></tr>
        <tr><td></td><td>December 31, 2024</td><td>December 31, 2023</td><td>December 31, 2024</td></tr>
        <tr><td>Net income margin</td><td>8.2%</td><td>4.0%</td><td>(3.0) %</td><td>25%</td></tr></table>
        <table><tr><td>Margin</td><td>99%</td></tr></table>"""
        meta = {"id":"doc","entity":"TEST","source_url":"fixture://reordered","snapshot_path":"test.html",
                "snapshot_hash":hashlib.sha256(source.encode()).hexdigest(),"published_at":"2025-02-01","mode":"replay"}
        cells = index_percentages(source, meta)
        self.assertEqual([(v["period_start"],v["period_end"],v["value"]) for v in cells], [
            ("2024-01-01","2024-12-31",8.2),("2023-10-01","2023-12-31",4.0),("2024-10-01","2024-12-31",-3.0)])
        self.assertEqual(len({c["locator"] for c in cells}),3)
        self.assertTrue(all(len(c["column_header_locators"]) == 2 for c in cells))

    def test_merged_headers_and_cells_keep_exact_financial_strings(self):
        document = self.parse()
        table = document["tables"][0]
        self.assertEqual(table["status"], "parsed")
        self.assertEqual(table["column_headers"], [["Metric", "Metric"], ["FY2025", "Actual Q1"], ["FY2025", "Outlook Q2"]])
        self.assertEqual(table["rows"], [["Revenue", "001,203", "NA"], ["Revenue", "(52.3)", "10.5%"], ["Gross margin", "70.6%", "71.0%"]])
        self.assertFalse(table["financial_semantics_validated"])
        self.assertIn("季度财报", document["text"])

    def test_source_locators_resolve_against_original_snapshot(self):
        from lxml import etree, html
        document = self.parse(COMPLEX.encode())
        table = document["tables"][0]
        original = html.document_fromstring(COMPLEX.encode(), parser=html.HTMLParser(no_network=True, remove_comments=True))
        target = original.getroottree().xpath(table["locator"]["dom_xpath"])[0]
        self.assertEqual(target.get("id"), "financials")
        self.assertEqual(table["locator"]["snapshot_hash"], hashlib.sha256(COMPLEX.encode()).hexdigest())
        self.assertEqual(table["html_fragment_sha256"], hashlib.sha256(etree.tostring(target, encoding="utf-8")).hexdigest())
        for cell in table["source_cells"]:
            self.assertEqual(len(original.getroottree().xpath(cell["dom_xpath"])), 1)
        self.assertTrue(any(c["rowspan"] == 2 for c in table["source_cells"]))
        self.assertTrue(any(c["colspan"] == 2 for c in table["source_cells"]))
        self.assertEqual(document["text_snapshot_hash"], hashlib.sha256(document["text"].encode()).hexdigest())
        for block in document["blocks"]:
            self.assertEqual(document["text"][block["text_start"]:block["text_end"]], block["text"])
            self.assertEqual(block["text_snapshot_hash"], document["text_snapshot_hash"])

    def test_hidden_content_removed_without_discarding_visible_tail(self):
        document = self.parse()
        self.assertIn("Visible continuation.", document["text"])
        for hidden in ("SECRET", "HIDDEN", "HIDDEN_XBRL"):
            self.assertNotIn(hidden, document["text"])

    def test_chunks_repeat_both_period_header_levels(self):
        document = self.parse()
        chunks = table_chunks(document, max_rows=1)
        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertIn("FY2025 / Actual Q1", chunk["text"])
            self.assertIn("FY2025 / Outlook Q2", chunk["text"])
            self.assertEqual(chunk["locator"]["snapshot_hash"], document["snapshot_hash"])
            self.assertEqual(chunk["chunking_status"], "header_repeated")
            self.assertFalse(chunk["text_is_exact_document_slice"])

    def test_undeclared_td_headers_do_not_get_detached_from_rows(self):
        document = self.parse('<table><tr><td>Period</td><td>Q1</td></tr><tr><td>Sales</td><td>12</td></tr><tr><td>Cost</td><td>5</td></tr></table>')
        chunks = table_chunks(document, max_rows=1)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["row_end"], 3)
        self.assertTrue(chunks[0]["row_count_exceeds_target"])
        self.assertEqual(chunks[0]["chunking_status"], "whole_table_no_declared_headers")

    def test_duplicate_and_empty_headers_are_source_text_not_pandas_inventions(self):
        document = self.parse('<table><tr><th></th><th>2025</th><th>2025</th></tr><tr><td>Sales</td><td>001</td><td>002</td></tr></table>')
        table = document["tables"][0]
        self.assertEqual(table["column_headers"], [[""], ["2025"], ["2025"]])
        self.assertEqual(table["rows"], [["Sales", "001", "002"]])
        self.assertNotIn("2025.1", table["text"])

    def test_nested_and_oversized_tables_are_explicit_failures(self):
        nested = self.parse('<table><tr><td>Outer<table><tr><td>Inner</td></tr></table></td></tr></table>')
        self.assertEqual(nested["tables"][0]["status"], "unresolved")
        self.assertEqual(nested["tables"][0]["failure_reason"], "nested_table_requires_separate_interpretation")
        self.assertIn("Unresolved table", nested["text"])
        too_large = self.parse('<table><tr><td colspan="99999999">12</td></tr></table>')
        self.assertEqual(too_large["tables"][0]["failure_reason"], "unsupported_or_excessive_colspan")
        self.assertEqual(too_large["status"], "partial")
        with self.assertRaises(DataError):
            self.parse(max_bytes=10)

    def test_empty_layout_table_is_not_a_successful_financial_table(self):
        document = self.parse('<table><tr><td> </td></tr></table>')
        self.assertEqual(document["tables"][0]["failure_reason"], "empty_layout_table")
        self.assertFalse(table_chunks(document))

    def test_no_network_and_stable_identity(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")) as connect:
            first, second = self.parse(), self.parse()
        connect.assert_not_called()
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertEqual(first["tables"][0]["id"], second["tables"][0]["id"])
        changed = self.parse(COMPLEX.replace("001,203", "001,204"))
        self.assertNotEqual(first["tables"][0]["id"], changed["tables"][0]["id"])

    def test_table_nested_in_list_is_not_flattened_by_paragraph_walker(self):
        document = self.parse('<ul><li>Evidence<table><tr><td>Sales</td><td>12</td></tr></table></li></ul>')
        self.assertEqual(len([b for b in document["blocks"] if b["kind"] == "table"]), 1)


if __name__ == "__main__":
    unittest.main()
