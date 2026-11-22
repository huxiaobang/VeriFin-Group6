"""Quarterly GAAP operands in a release with split date headers and merged cells."""
from unittest import TestCase

from verifin.release_summary import quarterly_cells
from verifin.tools import ToolRegistry


def row(*cells):
    return "<tr>" + "".join(f'<td colspan="{span}">{text}</td>' for span, text in cells) + "</tr>"


HEADLINE = """
<h1>Third Quarter Fiscal 2027 Results</h1>
<p>The company reported revenue for the third quarter ended October 25, 2026.</p>
<p>Financial outlook for the fourth quarter of fiscal 2027 follows below.</p>
"""
SUMMARY = """
<table><tr><td>GAAP</td></tr>
<tr><td>($ in billions)</td><td>Q3 FY27</td><td>Q3 FY26</td></tr>
<tr><td>Revenue</td><td>$115.8</td><td>$57.0</td></tr>
<tr><td>Operating income</td><td>$77.5</td><td>$36.0</td></tr></table>
"""
STATEMENT_TITLE = """
<p>CONDENSED CONSOLIDATED STATEMENTS OF INCOME</p>
<p>(In millions, except per share data)</p><p>(Unaudited)</p>
"""
HEADER = (
    row((9, ""), (9, "Three Months Ended"), (3, ""), (9, "Nine Months Ended"))
    + row((9, ""), (3, "October 25,"), (3, ""), (3, "October 26,"), (3, ""),
          (3, "October 25,"), (3, ""), (3, "October 26,"))
    + row((9, ""), (3, "2026"), (3, ""), (3, "2025"), (3, ""), (3, "2026"), (3, ""), (3, "2025"))
)
REVENUE = row((9, "Revenue"), (1, "$"), (1, "115,844"), (1, ""), (3, ""), (1, "$"), (1, "57,006"),
              (1, ""), (3, ""), (1, "$"), (1, "293,681"), (1, ""), (3, ""), (1, "$"), (1, "147,811"), (1, ""))
INCOME = row((9, "Operating income"), (2, "77,520"), (1, ""), (3, ""), (2, "36,010"),
             (1, ""), (3, ""), (2, "194,791"), (1, ""), (3, ""), (2, "86,088"), (1, ""))
RELEASE = f"<html>{HEADLINE}{SUMMARY}{STATEMENT_TITLE}<table>{HEADER}{REVENUE}{INCOME}</table></html>"


FULL_DATE_TITLE = """
<p>Condensed Consolidated Statements of Income</p>
<p>(In millions, except per share data; unaudited)</p>
"""
FULL_DATE_HEADER = (
    row((3, ""), (9, "Three Months Ended"), (3, ""), (9, "Nine Months Ended"))
    + row((3, ""), (3, "August 28, 2026"), (3, ""), (3, "August 29, 2025"), (3, ""),
          (3, "August 28, 2026"), (3, ""), (3, "August 29, 2025"))
)
FULL_DATE_ROWS = (
    row((3, "Total revenue"), (1, "$"), (1, "6,760"), (1, ""), (3, ""), (2, "5,988"), (1, ""),
        (3, ""), (2, "19,776"), (1, ""), (3, ""), (2, "17,575"), (1, ""))
    + row((3, "Operating income"), (2, "2,354"), (1, ""), (3, ""), (2, "2,173"), (1, ""),
          (3, ""), (2, "7,010"), (1, ""), (3, ""), (2, "6,445"), (1, ""))
)
FULL_DATE_RELEASE = (
    "<html><p>The company reported financial results for its third quarter FY2026 ended Aug. 28, 2026.</p>"
    "<p>GAAP operating income in the third quarter was $2.35 billion and non-GAAP operating income was $2.97 billion.</p>"
    f"{FULL_DATE_TITLE}<table>{FULL_DATE_HEADER}{FULL_DATE_ROWS}</table></html>"
)


def operands(html, *, form=None):
    parser = ToolRegistry._filing_parser(html.encode(), {}, collect_tables=True)
    record = {"text": parser.text()[:6000], "published_at": "2026-11-17", "source_document_id": "release", "form": form,
              "source_revision_authoritative": True, "snapshot_hash": "fixture"}
    return quarterly_cells(parser.tables, record, layouts=parser.table_layouts, source_text=parser.text())


class ReleaseStatementLayoutTests(TestCase):
    def test_exact_amounts_and_fiscal_period_come_from_distinct_source_labels(self):
        actual = {cell["metric"]: cell for cell in operands(RELEASE)}
        self.assertEqual({metric: cell["value"] for metric, cell in actual.items()},
                         {"revenue": 115_844_000_000, "operating_income": 77_520_000_000})
        self.assertAlmostEqual(100 * actual["operating_income"]["value"] / actual["revenue"]["value"],
                               66.91757881288629)
        for metric, evidence in actual.items():
            with self.subTest(metric=metric):
                self.assertEqual(evidence["fiscal_quarter"], "FY2027 Q3")
                self.assertEqual(evidence["period_end"], "2026-10-25")
                self.assertEqual(evidence["unit"], "USD")
                self.assertEqual(evidence["printed_scale"], 1_000_000)
                self.assertEqual(evidence["column_header"], "October 25, 2026")
                self.assertIn("HTML table[1]", evidence["locator"])
                self.assertIn("columns[9:12]", evidence["locator"])

    def test_explicit_negative_sign_or_accounting_parentheses(self):
        for value in ("-77,520", "(77,520)"):
            with self.subTest(value=value):
                cells = {cell["metric"]: cell for cell in operands(RELEASE.replace("77,520", value))}
                self.assertEqual(cells["operating_income"]["value"], -77_520_000_000)

    def test_ineligible_statements_cannot_supply_a_pair(self):
        cases = {
            "blank current cell": RELEASE.replace("115,844", ""),
            "missing cell changes table geometry": RELEASE.replace('<td colspan="1">115,844</td>', ""),
            "quarterly header moved to a different period": RELEASE.replace("October 25,</td>", "October 24,</td>", 1),
            "headline points at the comparative column": RELEASE.replace("ended October 25, 2026", "ended October 26, 2025"),
            "no GAAP heading": RELEASE.replace(">GAAP<", ">Non–GAAP<"),
            "non-GAAP statement heading": RELEASE.replace("CONDENSED CONSOLIDATED", "NON-GAAP CONDENSED CONSOLIDATED"),
            "unsupported rowspans": RELEASE.replace('<td colspan="2">77,520</td>', '<td colspan="2" rowspan="2">77,520</td>'),
            "accounting parentheses and explicit negative sign": RELEASE.replace("115,844", "(-115,844)"),
            "Canadian-dollar reporting currency": RELEASE.replace(
                "</h1>", "</h1><p>All dollar amounts are in Canadian dollars (CAD) unless otherwise indicated</p>"),
            "Canadian-dollar note beyond the returned passage": RELEASE.replace(
                "</html>", "<p>" + "background " * 600 + "All amounts are presented in CAD</p></html>"),
            "Canadian-dollar statement note": RELEASE.replace(
                "<p>CONDENSED", "<p>Canadian dollars</p><p>CONDENSED"),
            "ambiguous fiscal year": RELEASE.replace("</h1>", "</h1><p>Third Quarter Fiscal 2026 Results</p>"),
        }
        for case, release in cases.items():
            with self.subTest(case=case):
                self.assertEqual(operands(release), [])


    def test_full_dates_separate_the_quarter_from_comparative_and_ytd_columns(self):
        cells = {cell["metric"]: cell for cell in operands(FULL_DATE_RELEASE, form="8-K")}
        self.assertEqual({metric: cell["value"] for metric, cell in cells.items()},
                         {"revenue": 6_760_000_000, "operating_income": 2_354_000_000})
        for cell in cells.values():
            self.assertEqual(cell["fiscal_quarter"], "FY2026 Q3")
            self.assertEqual(cell["period_end"], "2026-08-28")
            self.assertIsNone(cell.get("period_start"))
            self.assertEqual(cell["period_quote"], "third quarter FY2026 ended Aug. 28, 2026")
            self.assertIn("columns[3:6]", cell["locator"])

    def test_full_date_statement_rejects_conflicting_or_ineligible_evidence(self):
        cases = {
            "current amount blank": FULL_DATE_RELEASE.replace("6,760", ""),
            "duplicate current quarter": FULL_DATE_RELEASE.replace("August 29, 2025", "August 28, 2026", 1),
            "current date only in cumulative group": FULL_DATE_RELEASE.replace("August 28, 2026", "August 27, 2026", 1),
            "non-GAAP narrative": FULL_DATE_RELEASE.replace("GAAP operating income in", "Non‑GAAP operating income in"),
            "non-GAAP statement": FULL_DATE_RELEASE.replace("Condensed Consolidated", "Adjusted Condensed Consolidated"),
            "reported income conflicts with table": FULL_DATE_RELEASE.replace("$2.35 billion", "$2.97 billion"),
            "other reporting currency": FULL_DATE_RELEASE.replace("</html>", "<p>Figures are reported in CAD</p></html>"),
            "contradictory fiscal headline": FULL_DATE_RELEASE.replace("<html>", "<html><h1>Third quarter FY2025</h1>"),
        }
        for name, release in cases.items():
            with self.subTest(name=name):
                self.assertEqual(operands(release, form="8-K"), [])
        self.assertEqual(operands(FULL_DATE_RELEASE, form="6-K"), [])
