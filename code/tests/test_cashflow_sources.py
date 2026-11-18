from datetime import date
from hashlib import sha256
import importlib.util
import unittest

from verifin.statement_analysis import Findings


@unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML parser is optional")
class CashFlowSourcesTests(unittest.TestCase):
    def filing(self, tag="filing", *, form="10-Q", period_end="2024-03-31", net="50",
               depreciation="17", adjustment="(8)", cfo="59", heading="Three Months Ended",
               current="March 31, 2024", prior="March 31, 2023", currency="CAD",
               pivot="Changes in operating assets and liabilities, excluding divestitures:",
               extra="", hidden=False):
        def row(label, current, prior):
            return f"<tr><td>{label}</td><td colspan='2'>{current}</td><td colspan='2'>{prior}</td></tr>"
        body = (
            "<h2>Consolidated Statements of Cash Flows</h2>"
            f"<p>{currency} in thousands</p><table><tr><td></td><th colspan='4'>{heading}</th></tr>"
            f"<tr><td></td><th colspan='2'>{current}</th><th colspan='2'>{prior}</th></tr>"
            +row("Net earnings", net, "40")+row("Adjustments to reconcile net earnings:","","")
            +row("Depreciation expense", depreciation, "12")
            +extra+row(pivot, "", "")+row("Trade receivables", adjustment, "(5)")
            +row("Cash flows from operating activities", cfo,"47")+"</table>")
        if hidden:
            body = "<div hidden>"+body+"</div>"
        raw = ("<html><title>FY2024 Q1</title><body>"+body+"</body></html>").encode()
        return dict(entity="NEW", source_form=form, form=form, raw=raw, source=f"{tag}.htm",
                    url=f"https://example.test/{tag}", published_at="2024-05-10",
                    period_end=period_end, snapshot_hash=sha256(raw).hexdigest())

    def run_skill(self,*documents,question="本季经营现金流和净利润差了多少？主要调节项是哪些？"):
        from verifin.cashflow_skill import analyze
        result = Findings()
        analyze(question, [], list(documents), result)
        return result

    def test_section_heading_and_printed_working_capital_total_can_coexist(self):
        doc = self.filing(adjustment="(8)")
        raw = doc["raw"].replace(b"<table>", b"<div><span></span><table>").replace(b"</table>", b"</table></div>")
        raw = raw.replace(b"<tr><td>Net earnings", b"<tr><td>Cash flows from operating activities</td></tr><tr><td>Net earnings")
        raw = raw.replace(b"Changes in operating assets and liabilities, excluding divestitures:", b"")
        raw = raw.replace(b"Trade receivables", b"Changes in current assets and current liabilities")
        doc.update(raw=raw, snapshot_hash=sha256(raw).hexdigest())
        found = self.run_skill(doc)
        self.assertTrue(any(n["kind"] == "cash_reconciliation" for n in found.notes), found.gaps)
        # Do not count the total plus a breakdown as two separate adjustments.
        detail = b"<tr><td>Trade receivables</td><td colspan='2'>1</td><td colspan='2'>1</td></tr>"
        raw = raw.replace(b"<tr><td>Cash flows from operating activities</td><td colspan='2'>59", detail+b"<tr><td>Cash flows from operating activities</td><td colspan='2'>59")
        doc.update(raw=raw, snapshot_hash=sha256(raw).hexdigest())
        partial = self.run_skill(doc)
        self.assertTrue(any(n["kind"] == "cash_bridge" for n in partial.notes))
        self.assertFalse(any(n["kind"] == "cash_reconciliation" for n in partial.notes))

    def test_period_unit_row_xpath_and_formal_operands(self):
        from verifin.cashflow_skill import dom
        filing = self.filing()
        result = self.run_skill(filing)
        self.assertEqual(result.gaps,[])
        self.assertEqual([n["kind"] for n in result.notes],["cash_bridge","cash_drivers","cash_drivers","cash_reconciliation"])
        self.assertEqual([v["value"] for v in result.calculations.values()], [9,17,-8,59,0])
        root = dom(filing["raw"])
        for fact in result.evidence.values():
            self.assertEqual((fact["period_start"],fact["period_end"]),("2024-01-01","2024-03-31"))
            self.assertEqual(fact["unit"],"CAD thousands")
            self.assertEqual(fact["snapshot_hash"],filing["snapshot_hash"])
            self.assertEqual(root.xpath(fact["row_label_locator"])[0].text_content(),fact["metric"])
            self.assertEqual(root.xpath(fact["locator"])[0].text_content(),fact["printed_value"])
            self.assertEqual([root.xpath(p)[0].text_content() for p in fact["column_header_locators"]],fact["column_headers"])
        self.assertEqual(len(result.evidence),4)
        delta=result.record(result.notes[0]["calculation_ids"][0])
        self.assertEqual([result.value(k) for k in delta["operand_ids"]],[59,50])

    def test_missing_shifted_and_unreconciled_rows_preserve_endpoints_and_known_lines(self):
        shifted = "<tr><td>Other</td><td>9</td><td>7</td></tr>"
        for options in (dict(adjustment=""), dict(adjustment="N/A"), dict(depreciation="15"),
                        dict(extra=shifted), dict(extra="<tr><td>Other</td><td>1</td><td>2</td><td colspan='2'>5</td></tr>")):
            with self.subTest(options=options):
                result=self.run_skill(self.filing(**options))
                self.assertTrue(result.gaps)
                self.assertEqual([c["value"] for c in result.calculations.values()],[9])
                self.assertTrue(result.notes[0]["complete"])
                self.assertTrue(all(not n["complete"] for n in result.notes if n["kind"]=="cash_drivers"))
                self.assertFalse(any(n["kind"]=="cash_reconciliation" for n in result.notes))

    def test_equivalent_copies_use_periodic_filing_and_disagreements_remain_partial(self):
        release = self.filing("news",form="8-K Exhibit 99")
        filing = self.filing()
        result = self.run_skill(release,filing)
        self.assertFalse(result.gaps)
        self.assertEqual({r["source_form"] for r in result.evidence.values()},{"10-Q"})
        result = self.run_skill(self.filing("news",form="8-K",depreciation="18"),filing)
        self.assertTrue(result.gaps)
        self.assertTrue(result.notes[0]["complete"])
        self.assertFalse(any(v["metric"]=="Depreciation expense" for v in result.evidence.values()))
        result = self.run_skill(self.filing("news",form="8-K",cfo="60"),filing)
        self.assertTrue(result.gaps)
        self.assertFalse(result.notes[0]["complete"])
        self.assertFalse(result.notes[0]["calculation_ids"])
        self.assertFalse(self.run_skill(self.filing("news", currency="USD"),filing).notes)

    def test_hidden_latest_filing_never_reverts_to_prior_quarter(self):
        recent = self.filing("new",period_end="2024-06-30",hidden=True)
        result=self.run_skill(self.filing(),recent)
        self.assertFalse(result.notes)
        self.assertTrue(result.gaps)
        result=self.run_skill(self.filing(),question="FY2023 Q1 经营现金流比净利差多少？")
        self.assertFalse(result.notes)

    def test_qualified_heading_with_an_amount_is_not_a_group(self):
        doc=self.filing()
        doc["raw"]=doc["raw"].replace(b"excluding divestitures:</td><td colspan='2'></td>",
                                      b"excluding divestitures:</td><td colspan='2'>3</td>")
        doc["snapshot_hash"]=sha256(doc["raw"]).hexdigest()
        result=self.run_skill(doc)
        self.assertEqual([n["kind"] for n in result.notes],["cash_bridge"])
        self.assertTrue(result.gaps)

    def test_week_and_split_year_headers_keep_actual_period_starts(self):
        from verifin.cashflow_skill import _read_documents
        for heading, current, prior, start in [
            ("Fifty-Three Weeks Ended","December 29, 2024","December 24, 2023",date(2023,12,25)),
            ("39 Weeks Ended","September 22, 2024","September 24, 2023",date(2023,12,25)),
            ("Three Months Ended March 31,","2024","2023",date(2024,1,1))]:
            with self.subTest(heading=heading):
                table, = _read_documents([self.filing(heading=heading,current=current,prior=prior)])
                self.assertEqual(max(table.columns).start,start)
                self.assertEqual(len(table.columns),2)

    def test_a_bare_dollar_symbol_needs_both_comparative_amounts_to_identify_currency(self):
        keys = [("net_income","2023-01-01","2023-03-31",40000),
                ("operating_cash_flow","2023-01-01","2023-03-31",47000)]
        filing=self.filing(currency="$")
        def denomination(witnesses):
            filing["cash_currency_evidence"]=dict(zip(keys,witnesses))
            result=self.run_skill(filing)
            self.assertFalse(result.gaps)
            return next(iter(result.evidence.values()))
        self.assertEqual(denomination([{"CAD":{}}])["unit"],"$ thousands")
        self.assertEqual(denomination([{"USD":{}},{"CAD":{}}])["unit"],"$ thousands")
        cell=denomination([{"CAD":{}},{"CAD":{}}])
        self.assertEqual(cell["unit"],"CAD thousands")
        self.assertEqual(len(cell["currency_reference"]["matching_filed_operands"]),2)

    def test_br_in_shared_duration_headers_binds_four_year_leaves_separately(self):
        from verifin.cashflow_skill import _read_documents
        def cells(tag, values):
            return "".join(f"<{tag}>{v}</{tag}>" for v in values)
        def body(label, annual, prior_year, quarter, prior_quarter):
            return (f"<tr><td>{label}</td>" + cells("td",[annual,prior_year,quarter,prior_quarter])+"</tr>")
        filing=self.filing(period_end="2024-09-30")
        html = ("<html><body><table><tr><td colspan='5'>USD in millions</td></tr>"
                "<tr><th></th><th colspan='2'>Fiscal Year Ended<br/>September 30,</th>"
                "<th colspan='2'>Three Months Ended<br/>September 30,</th></tr>"
                "<tr><td></td>"+cells("th",[2024,2023,2024,2023])+"</tr>"
                +body("Net earnings",450,300,110,65)+body("Depreciation",71,52,18,20)
                +body("Changes in operating assets and liabilities:","","","","")
                +body("Trade receivables",-37,-30,-9,-13)
                +body("Net cash from operations",484,322,119,72)+"</table></body></html>")
        filing["raw"]=html.encode()
        filing["snapshot_hash"]=sha256(filing["raw"]).hexdigest()
        table,=_read_documents([filing])
        self.assertEqual({(p.end.year,p.months) for p in table.columns},{(2024,3),(2024,12),(2023,3),(2023,12)})
        for question,period_start,expected in [("本季经营现金流和净利差多少？","2024-07-01",9),
                                               ("全年经营现金流和净利差多少？","2023-10-01",34)]:
            result=self.run_skill(filing,question=question)
            self.assertFalse(result.gaps)
            self.assertEqual({r["period_start"] for r in result.evidence.values()},{period_start})
            self.assertEqual(result.value(result.notes[0]["calculation_ids"][0]),expected)
        # A string with no word boundary and no actual line break supplies no duration.
        filing["raw"]=filing["raw"].replace(b"<br/>",b"")
        filing["snapshot_hash"]=sha256(filing["raw"]).hexdigest()
        self.assertFalse(self.run_skill(filing).notes)
