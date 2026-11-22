"""Quarterly earnings-release summaries supply same-row GAAP operands."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_tools import DATA, MemoryStore
from verifin.data import stable_id
from verifin.margin_formula import draft_calculation
from verifin.statement_amount_cells import statement_cells
from verifin.tools import ToolRegistry


QUESTION = "What were Microsoft's revenue and operating margin in FY2025 Q2? Show the source and calculation."
SUMMARY = """<html><body><h2>Consolidated Financial Results</h2><table>
<tr><th></th><th colspan="2">Three Months Ended December 31,</th></tr>
<tr><th>(USD in millions)</th><th>Net Sales</th><th>Income from Operations</th></tr>
<tr><th>2023 As Reported (GAAP)</th><td>900</td><td>150</td></tr>
<tr><th>2024 As Reported (GAAP)</th><td>1,024</td><td>(120)</td></tr>
<tr><th>Constant Currency Impact</th><td>20</td><td>30</td></tr>
</table></body></html>"""


@unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
class ReportedGAAPTests(unittest.TestCase):
    @staticmethod
    def fixture(raw, *, source_text=None, companies=(), **metadata):
        from lxml import html

        source_text = source_text if source_text is not None else " ".join(" ".join(html.fromstring(raw).itertext()).split())
        doc = {"entity": "TEST", "source": "summary.html", "text": "summary.txt",
               "period_end": "2024-12-31", "published_at": "2025-01-29", **metadata}
        info = {"sha256": "test", "url": "https://example.test/quarter"}
        return statement_cells(raw.encode(), doc, info, "doc", source_text, {}, companies=companies)

    def test_actual_two_document_windows_supply_exact_operands_to_the_margin_handoff(self):
        with TemporaryDirectory() as cache:
            store = MemoryStore()
            registry = ToolRegistry(store, DATA, cache_dir=Path(cache))
            doc = next(d for d in registry.data.manifest["documents"] if d["source"].endswith("msft_fy25q2_ir.html"))
            snapshot = registry.data.raw(doc["text"]).decode()
            info = registry.data.info(doc["source"])
            args = {"entity": "MSFT", "document_id": stable_id("source_doc_", info["url"])}
            scope = {"entities": ["MSFT"], "cutoff": "2025-02-01"}

            def call(tool, arguments):
                result = registry.execute(tool, arguments, "session", scope)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["data"]["network_requests"], 0)
                store.save(result)
                return result

            heading = call("read_document", args)
            self.assertEqual(heading["data"]["numeric_operand_status"], "text_only")
            window = call("read_document", {**args, "offset": 4500, "limit_chars": 6000})
            self.assertEqual(window["data"]["numeric_operand_status"], "attached")
            passage, revenue, income = window["evidence"]
            self.assertEqual((revenue["value"], income["value"]), (69_632_000_000, 31_653_000_000))
            self.assertEqual(revenue["table_id"], income["table_id"])
            for cell in (revenue, income):
                self.assertEqual((cell["period_start"], cell["period_end"]), ("2024-10-01", "2024-12-31"))
                self.assertEqual((cell["basis"], cell["unit"], cell["printed_scale"]), ("US-GAAP", "USD", 1_000_000))
                self.assertEqual(window["data"]["numeric_operand_passages"][cell["id"]], [passage["id"]])
                self.assertTrue(cell["locator"].startswith(cell["table_xpath"]))
                sequence = snapshot[cell["source_context_start"]:cell["source_row_end"]]
                self.assertIn(cell["printed_value"], sequence)
                self.assertIn("Three Months Ended December 31,", sequence)

            row_only = call("read_document", {**args, "offset": revenue["source_row_start"], "limit_chars": 200})
            self.assertEqual(row_only["data"]["numeric_operand_status"], "text_only")
            header_start = snapshot.index("Three Months Ended December 31,", revenue["source_context_start"])
            no_title = call("read_document", {**args, "offset": header_start, "limit_chars": 400})
            self.assertIn(revenue["printed_value"], no_title["evidence"][0]["text"])
            self.assertEqual(no_title["data"]["numeric_operand_status"], "text_only")
            selected = draft_calculation({"scope": scope, "episode": {"request": QUESTION}, "calculations": [],
                                          "evidence": heading["evidence"] + window["evidence"],
                                          "tools": registry.available_tools()})
            self.assertEqual(selected, {"operation": "margin", "operand_ids": [income["id"], revenue["id"]]})
            receipt, = call("calculate", selected)["calculations"]
            self.assertAlmostEqual(receipt["value"], 31_653 / 69_632 * 100)

    def test_other_issuer_summary_uses_reported_row_and_preserves_sign(self):
        rows = self.fixture(SUMMARY)
        self.assertEqual([(r["metric"], r["value"], r["basis"], r["unit"]) for r in rows],
                         [("revenue", 1_024_000_000, "GAAP", "USD"), ("operating_income", -120_000_000, "GAAP", "USD")])
        self.assertTrue(all(r["period_start"] == "2024-10-01" for r in rows))
        self.assertFalse(self.fixture(SUMMARY, period_start="2024-09-30"))
        self.assertFalse(self.fixture(SUMMARY, published_at="2024-10-01"))

    def test_reject_unbound_or_ambiguous_current_rows(self):
        for original, replacement in (
            ("2024 As Reported (GAAP)", "2024 As Reported (Non-GAAP)"),
            ("2024 As Reported (GAAP)", "2024 Constant Currency"),
            ("2024 As Reported (GAAP)", "2025 As Reported (GAAP)"),
            ("Three Months Ended", "Six Months Ended"),
            ("December 31,", "December 28,"),
            ("(USD in millions)", "($ in millions)"),
            ("<td>1,024</td>", ""),
            ("<th>Net Sales</th>", "<th>Income from Operations</th>"),
            ("Consolidated Financial Results", "Segment Financial Results"),
        ):
            with self.subTest(replacement=replacement):
                self.assertFalse(self.fixture(SUMMARY.replace(original, replacement)))
        conflicting_currency = SUMMARY.replace("(USD in millions)", "(EUR in millions)").replace("1,024", "$1,024")
        self.assertFalse(self.fixture(conflicting_currency))

    def test_do_not_assemble_a_pair_across_two_tables_or_two_rows(self):
        first = SUMMARY.replace("<th>Income from Operations</th>", "<th>Other Income</th>")
        second = SUMMARY.replace("<th>Net Sales</th>", "<th>Total Expenses</th>")
        self.assertFalse(self.fixture(first.removesuffix("</body></html>") + second.removeprefix("<html><body>")))
        competing = SUMMARY.replace("<td>(120)</td></tr>", "<td>(120)</td></tr>"
                                    "<tr><th>2024 Reported (GAAP)</th><td>1,110</td><td>150</td></tr>")
        self.assertFalse(self.fixture(competing))

    def test_mixed_headers_cannot_qualify_reported_cells_by_position_alone(self):
        for row_label, first, second, year in (
            ("Reporting currency", "USD", "CAD", "2023"),
            ("Reporting scale", "Millions", "Thousands", "2023"),
            ("Basis", "Total", "Per Share", "2023"),
            ("Duration", "Current quarter", "Fiscal year to date", "2024"),
        ):
            with self.subTest(row_label=row_label):
                marker = f"<tr><th>{year} As Reported (GAAP)"
                extra = f"<tr><th>{row_label}</th><th>{first}</th><th>{second}</th></tr>"
                self.assertFalse(self.fixture(SUMMARY.replace(marker, extra + marker)))

        linked = SUMMARY.replace("<th>Income from Operations</th>", '<th id="profit">Income from Operations</th>')
        linked = linked.replace("<td>(120)</td>", '<td headers="profit">(120)</td>')
        self.assertEqual(len(self.fixture(linked)), 2)
        other = linked.replace('headers="profit"', 'headers="foreign"').replace(
            "</table>", '<tfoot><tr><th></th><th></th><th id="foreign">CAD thousands</th></tr></tfoot></table>')
        self.assertFalse(self.fixture(other))

    def test_distinct_tables_cannot_supply_a_detached_normalized_row(self):
        from lxml import html

        segment = SUMMARY.replace("Consolidated Financial Results", "Segment Results").replace("USD in millions", "CAD in millions")
        raw = SUMMARY.removesuffix("</body></html>") + segment.removeprefix("<html><body>")
        text = " ".join(" ".join(html.fromstring(raw).itertext()).split())
        text = text.replace("2024 As Reported (GAAP) 1,024 (120)", "", 1)
        self.assertFalse(self.fixture(raw, source_text=text))

    def test_an_explicit_nearby_issuer_symbol_controls_namesake_scope(self):
        companies = ({"ticker": "TEST", "name": "Twin Financial Holdings"},
                     {"ticker": "OTHER", "name": "Twin Financial Holdings"})
        for entity, expected in (("TEST", 2), ("OTHER", 0)):
            source = SUMMARY.replace("<h2>", f"<h1>Twin Financial Holdings ({entity})</h1><h2>", 1)
            self.assertEqual(len(self.fixture(source, companies=companies)), expected)
