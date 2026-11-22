import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_tools import DATA, MemoryStore
from verifin.data import stable_id
from verifin.tools import ToolRegistry


@unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
class StatementAmountTests(unittest.TestCase):
    @staticmethod
    def fixture(*, prior="990", omit_income=False, omit_prior_td=False, unit="CAD in thousands",
                hidden=False, duplicate=False, repeat_text=False, expenses=()):
        title = "Consolidated Statements of Operations"
        row = "<tr><td>Net sales</td><td>$ 1,100</td>" + ("" if omit_prior_td else "<td>"+prior+"</td>") + "<td>4,001</td><td>3,800</td></tr>"
        income = "<tr><td>Income from operations</td><td>88</td><td>(12)</td><td>400</td><td>320</td></tr>"
        gross = "<tr><td>Gross profit</td><td>200</td><td>190</td><td>700</td><td>650</td></tr>"
        expense_rows = ["<tr>" + "".join("<td>" + cell + "</td>" for cell in values) + "</tr>"
                        for values in expenses]
        structural = ('<tr><th rowspan="2">Period</th><th colspan="2">Thirteen Weeks Ended</th>'
            '<th colspan="2">Fifty-Two Weeks Ended</th></tr>'
            '<tr><th>December 29, 2024</th><th>December 31, 2023</th>'
            '<th>December 29, 2024</th><th>December 31, 2023</th></tr>')
        source = ("<html><body>" + ('<div hidden>' if hidden else "") +
            "<h2>"+title+"</h2><p>("+unit+")</p><table>"+structural+row+
            (row if duplicate else "")+gross+"".join(expense_rows)+("" if omit_income else income)+"</table>" +
            ("</div>" if hidden else "") + "</body></html>")
        rows = ["banner "*40, title, unit, "Period Thirteen Weeks Ended Fifty-Two Weeks Ended",
                "December 29, 2024 December 31, 2023 December 29, 2024 December 31, 2023",
                "Net sales $ 1,100 "+("" if omit_prior_td else prior)+" 4,001 3,800",
                "Gross profit 200 190 700 650"]
        if repeat_text or duplicate:
            rows.append(rows[-2])
        rows.extend(" ".join(value for value in values if value) for values in expenses)
        if not omit_income:
            rows.append("Income from operations 88 (12) 400 320")
        return source, "\n".join(rows)

    def run_fixture(self, **opts):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name)
        source, excerpt = self.fixture(**opts)
        doc = {"entity":"AAA","source":"release.htm","text":"release.txt","form":"8-K Exhibit 99.1",
               "published_at":"2025-02-20","period_end":"2024-12-29","publication_precision":"date_only",
               "extraction_method":"row text"}
        files = {}
        url = "https://example.org/filing"
        for filename, content in [(doc["source"],source), (doc["text"],excerpt)]:
            raw = content.encode()
            (directory/filename).write_bytes(raw)
            files[filename] = {"sha256":hashlib.sha256(raw).hexdigest(), "url":url, "retrieved_at":"2025-02-21"}
        (directory/"manifest.json").write_text(json.dumps({
            "companies":[{"name":"Example","ticker":"AAA","cik":"0000000001"}], "documents":[doc], "files":files}))
        store = MemoryStore()
        registry = ToolRegistry(store, directory)
        args = {"entity":"AAA", "document_id":stable_id("source_doc_",url)}
        result = registry.execute("read_document",args,"session",{})
        self.assertTrue(result["ok"], result)
        store.save(result)
        return registry, args, result, excerpt

    def test_week_based_fiscal_period_needs_one_prior_filing_boundary(self):
        from datetime import date
        from verifin.statement_amount_cells import period

        heading = ["Three Months Ended", "February 2, 2025"]
        closes = {date(2024, 10, 27): [{"form": "10-Q"}]}
        self.assertEqual(period(heading, closes), ("2024-10-28", "2025-02-02"))
        self.assertIsNone(period(heading))
        self.assertIsNone(period(heading, {date(2024, 10, 27): [{"form": "10-QT"}]}))
        closes[date(2024, 11, 3)] = [{"form": "10-Q"}]
        self.assertIsNone(period(heading, closes))

    def test_filing_boundary_requires_same_issuer_and_prior_publication(self):
        from datetime import date
        from unittest.mock import Mock
        from verifin.statement_amount_cells import filed_boundaries

        data = Mock()
        data.manifest = {"companies": [{"ticker": "AAA", "cik": "0001", "submissions": "filings.json"}]}
        data.info.return_value = {"sha256": "snapshot"}
        recent = {"reportDate": ["2024-10-27"] * 3,
                  "form": ["10-Q", "10-Q", "8-K"], "accessionNumber": ["old", "later", "other"],
                  "filingDate": ["2024-11-01", "2025-02-20", "2024-11-02"]}
        data.json.return_value = {"cik": 1, "filings": {"recent": recent}}
        doc = {"entity": "AAA", "published_at": "2025-02-20"}
        records = filed_boundaries(data, doc)[date(2024, 10, 27)]
        self.assertEqual([(r["accession"], r["snapshot_hash"]) for r in records], [("old", "snapshot")])
        data.json.return_value["cik"] = 2
        self.assertEqual(filed_boundaries(data, doc), {})

    def test_native_rows_feed_formal_search_read_margin_and_point_change(self):
        registry, args, read, excerpt = self.run_fixture()
        self.assertEqual(read["data"]["network_requests"], 0)
        cells = {r["id"]:r for r in read["evidence"] if r.get("cell_type")=="statement_amount_cell"}
        self.assertEqual(len(cells), 12)
        self.assertEqual(set(read["data"]["numeric_operand_ids"]), cells.keys())
        searched = registry.execute("search_documents",{"entity":"AAA","query":"income from operations net sales"},
                                    "session",{})
        self.assertEqual({r["id"]:r for r in searched["evidence"] if r.get("cell_type")}, cells)
        for cell in cells.values():
            self.assertEqual(read["data"]["numeric_operand_passages"][cell["id"]], [read["evidence"][0]["id"]])
            self.assertIn(cell["printed_value"], excerpt[cell["source_row_start"]:cell["source_row_end"]])
            self.assertEqual(cell["currency"], "CAD")
            self.assertEqual(cell["printed_scale"], 1000)
            self.assertEqual(cell["scale"], 1)
            self.assertTrue(cell["locator"].startswith(cell["table_xpath"]+"/tr"))
            self.assertEqual(len(cell["column_header_locators"]), 2)
        indexed = {(r["metric"],r["period_start"]):r for r in cells.values()}
        margins = []
        for start in ("2024-09-30", "2023-10-02"):
            ids = [indexed[metric,start]["id"] for metric in ("operating_income", "revenue")]
            result = registry.execute("calculate",{"operation":"margin","operand_ids":ids},"session",{})
            self.assertTrue(result["ok"], result)
            registry.store.save(result)
            margins.append(result["calculations"][0]["id"])
        change = registry.execute("calculate",{"operation":"percentage_point_change","operand_ids":margins},"session",{})
        self.assertTrue(change["ok"],change)
        self.assertAlmostEqual(change["calculations"][0]["value"], 8+12/990*100)
        self.assertEqual(indexed["operating_income","2023-10-02"]["value"], -12000)
        unrelated = registry.execute("read_document",{**args, "limit_chars":200},"session",{})
        self.assertFalse(unrelated["data"].get("numeric_operand_ids"))

    def test_missing_source_cell_has_no_fallback_or_column_shift(self):
        for missing in ("", "—", "N/A"):
            with self.subTest(missing=missing):
                _,_,result,_ = self.run_fixture(prior=missing)
                cells = [r for r in result["evidence"] if r.get("cell_type")]
                self.assertEqual(len(cells),11)
                self.assertFalse(any(r["period_start"]=="2023-10-02" and r["metric"]=="revenue" for r in cells))
                annual = next(r for r in cells if r["period_start"]=="2024-01-01" and r["metric"]=="revenue")
                self.assertEqual(annual["value"],4001000)
        for opts, remaining in [({"omit_income":True},{"revenue","gross_profit"}),
                                ({"omit_prior_td":True},{"operating_income","gross_profit"}),
                                ({"duplicate":True},{"operating_income","gross_profit"}),
                                ({"repeat_text":True},{"operating_income","gross_profit"})]:
            with self.subTest(opts=opts):
                _,_,result,_ = self.run_fixture(**opts)
                self.assertEqual({r["metric"] for r in result["evidence"] if r.get("cell_type")}, remaining)

    def test_hidden_table_unknown_currency_mixed_scale_and_per_share_fail_closed(self):
        for opts in [{"hidden":True},{"unit":"in thousands"},{"unit":"CAD in thousands or millions"},
                     {"unit":"CAD in thousands except revenue in millions"}, {"unit":"CAD in millions of shares"},
                     {"unit":"USD and CAD in thousands"}]:
            with self.subTest(opts=opts):
                _,_,result,_ = self.run_fixture(**opts)
                self.assertFalse(result["data"].get("numeric_operand_ids"))

    def test_below_operating_income_does_not_supply_an_operating_contribution(self):
        from unittest.mock import patch
        source, excerpt = self.fixture()
        source = source.replace("</table>", "<tr><td>Other income</td><td>10</td><td>20</td><td>30</td><td>40</td></tr></table>")
        with patch.object(self, "fixture", return_value=(source, excerpt + "\nOther income 10 20 30 40")):
            _, _, result, _ = self.run_fixture()
        cells = [r for r in result["evidence"] if r.get("cell_type")]
        self.assertTrue(cells)
        self.assertNotIn("other_income", {r["metric"] for r in cells})

    def test_real_calendar_statement_with_split_years_and_shared_headers(self):
        registry = ToolRegistry(MemoryStore(), DATA)
        doc = next(d for d in registry.data.manifest["documents"] if d["source"].endswith("msft_fy25q2_ir.html"))
        content = registry.data.raw(doc["text"]).decode()
        offset = content.index("MICROSOFT CORPORATION INCOME STATEMENTS")
        result = registry.execute("read_document",{"entity":"MSFT","document_id":stable_id(
            "source_doc_",registry.data.info(doc["source"])["url"]),"offset":offset,"limit_chars":1800},"session",{})
        cells = [r for r in result["evidence"] if r.get("cell_type")]
        self.assertEqual(len(cells),24)
        rows = { (r["metric"],r["period_start"]):r for r in cells}
        self.assertEqual(rows["operating_income","2024-10-01"]["value"],31653000000)
        self.assertEqual(rows["revenue","2023-10-01"]["value"],62020000000)
        self.assertEqual(rows["revenue","2024-07-01"]["value"],135217000000)
        self.assertTrue(all(r["currency_reference"]["filed"] < doc["published_at"][:10] for r in cells))
        self.assertTrue(all(r["basis"] == "US-GAAP" and r["unit"] == "USD" for r in cells))
        for (metric, start), row in rows.items():
            if metric != "gross_profit":
                continue
            section = row["expense_section"]
            self.assertTrue(section["complete"])
            self.assertEqual([r["key"] for r in section["rows"]],
                             ["research_and_development","sales_and_marketing","general_and_administrative"])
            by_id = {r["id"]:r for r in cells}
            for entry in section["rows"]:
                amount = by_id[entry["cell_id"]]
                self.assertEqual((amount["expense_key"], amount["expense_role"]), (entry["key"],"component"))
                self.assertEqual((amount["period_start"],amount["period_end"]), (start,row["period_end"]))
                self.assertIn(amount["printed_value"],content[amount["source_row_start"]:amount["source_row_end"]])

    def test_expense_inventory_tracks_blank_cell_totals_rates_and_unsupported_subtotals(self):
        expenses = [("Operating expenses:",), ("Marketing expenses","70","","200","210"),
                    ("Marketing expenses as a percentage of sales","6%","7%","5%","6%"),
                    ("Research and development","42","32","100","120"),
                    ("Total operating expenses","112","202","300","330")]
        _, _, result, _ = self.run_fixture(expenses=expenses)
        cells = [r for r in result["evidence"] if r.get("cell_type")]
        sections = {r["period_start"]:r["expense_section"] for r in cells if r["metric"] == "gross_profit"}
        current, prior = sections["2024-09-30"], sections["2023-10-02"]
        self.assertTrue(current["complete"])
        self.assertFalse(prior["complete"])
        self.assertEqual([item["role"] for item in current["rows"]], ["component", "component", "total"])
        self.assertEqual([item["status"] for item in prior["rows"]], ["missing", "present", "present"])
        self.assertIsNone(prior["rows"][0]["cell_id"])
        self.assertTrue(prior["rows"][0]["row_label_locator"])
        self.assertIn("source_row_start",prior["rows"][0])
        total = next(r for r in cells if r["id"] == current["rows"][-1]["cell_id"])
        self.assertEqual((total["metric"],total["value"]), ("operating_expenses",112000))
        self.assertFalse(any("%" in r["printed_value"] for r in cells))
        for label in ("Total marketing expenses", "Gain on asset disposal", "Other operating items"):
            with self.subTest(label=label):
                _, _, result, _ = self.run_fixture(expenses=[*expenses, (label, "10", "20", "30", "40")])
                section = next(r["expense_section"] for r in result["evidence"] if r.get("metric") == "gross_profit")
                self.assertFalse(section["complete"])
                self.assertEqual(section["rows"][-1]["status"],"unsupported")
                self.assertFalse(section["rows"][-1]["cell_id"])

    def test_expense_only_excerpt_with_empty_pipe_field_retains_other_dated_cells(self):
        from verifin.statement_amount_cells import row_span
        parts = ["Research and development", "15", "", "43", "38"]
        source = "Research and development | 15 | | 43 | 38"
        self.assertEqual(row_span(parts,source), (0,len(source)))
        expenses = [("Research and development","150","120","450","380"),
                    ("Distribution costs","9","7","30","20"), ("Other items " * 25,"4","5","7","8")]
        registry, args, _, excerpt = self.run_fixture(expenses=expenses)
        start = excerpt.index("Research and development")
        self.assertNotIn("Income from operations",excerpt[start:start+200])
        result = registry.execute("read_document", {**args, "offset":start, "limit_chars":200},"session",{})
        cells = [r for r in result["evidence"] if r.get("expense_role") == "component"]
        self.assertEqual({r["row_label"] for r in cells}, {"Research and development","Distribution costs"})
