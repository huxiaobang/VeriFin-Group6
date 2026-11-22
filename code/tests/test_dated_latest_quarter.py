"""Dated latest-quarter evidence from a linked release and a matching 10-Q."""
from importlib.util import find_spec
from unittest import TestCase, skipUnless

from verifin.answer_periods import latest_answer_issues, remember_latest_quarter
from verifin.latest_quarter import acquire
from verifin.metric_language import metric_mentions
from verifin.tagged_profit_cells import reported_quarter_cells


END = "2026-08-28"
START = "2026-05-30"
RELEASE = "release.htm"
EXHIBIT = "ex991.htm"
REPORT = "periodic.htm"
QUESTION = "请给出最新财季营业利润率、财季起止日期及 SEC 10-Q 链接。"
VALUES = {"revenue": 6_760_000_000, "operating_income": 2_354_000_000}


def context(identifier, start):
    return f"""<xbrli:context id="{identifier}"><xbrli:entity>
        <xbrli:identifier>0000796343</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{END}</xbrli:endDate></xbrli:period>
        </xbrli:context>"""


def amount(label, tag, first, cumulative):
    return f"""<tr><td>{label}</td>
        <td><ix:nonFraction contextRef="quarter" unitRef="usd" name="us-gaap:{tag}" scale="6">{first}</ix:nonFraction></td>
        <td><ix:nonFraction contextRef="ytd" unitRef="usd" name="us-gaap:{tag}" scale="6">{cumulative}</ix:nonFraction></td></tr>"""


REPORT_HTML = (
    '<html><body><ix:nonNumeric name="dei:EntityCentralIndexKey">0000796343</ix:nonNumeric>'
    '<ix:nonNumeric name="dei:DocumentFiscalYearFocus">2026</ix:nonNumeric>'
    '<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus">Q3</ix:nonNumeric>'
    '<ix:nonNumeric name="dei:DocumentPeriodEndDate">August 28, 2026</ix:nonNumeric>'
    + context("quarter", START) + context("ytd", "2025-11-29")
    + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    + '<div>Condensed Consolidated Statements of Income</div><table>'
    + amount("Total revenue", "RevenueFromContractWithCustomerExcludingAssessedTax", "6,760", "19,776")
    + amount("Operating income", "OperatingIncomeLoss", "2,354", "7,010")
    + '</table></body></html>'
)


def filing(form, accession, primary, report_date, filed):
    return {"form": form, "accession": accession, "primaryDocument": primary, "reportDate": report_date,
            "filingDate": filed, "url": "https://www.sec.gov/" + primary, "items": "2.02" if form == "8-K" else ""}


class DatedLatestQuarterTests(TestCase):
    @skipUnless(find_spec("lxml"), "optional HTML parser is needed for inline XBRL")
    def test_exact_period_is_read_from_the_same_dimensionless_10q_context(self):
        doc = {"raw": REPORT_HTML.encode(), "entity": "ADBE", "cik": "0000796343", "form": "10-Q",
               "url": "https://www.sec.gov/periodic.htm", "snapshot_hash": "fixture",
               "source": REPORT, "published_at": "2026-09-16"}
        cells = reported_quarter_cells(doc, END)
        self.assertEqual({s["metric"]: s["value"] for s in cells}, VALUES)
        for cell in cells:
            self.assertEqual((cell["period_start"], cell["period_end"], cell["unit"]), (START, END, "USD"))
            self.assertEqual(cell["fiscal_quarter"], "FY2026 Q3")
            self.assertEqual(cell["origin_context"], "quarter")
            self.assertIn("xbrli:context", cell["context_locator"])
            self.assertIn("ix:nonfraction", cell["locator"])
        self.assertEqual(reported_quarter_cells({**doc, "cik": "0001045810"}, END), [])
        conflicting = REPORT_HTML.replace('contextRef="quarter" unitRef="usd" name="us-gaap:OperatingIncomeLoss"',
                                          'contextRef="ytd" unitRef="usd" name="us-gaap:OperatingIncomeLoss"')
        self.assertEqual(reported_quarter_cells({**doc, "raw": conflicting.encode()}, END), [])
        wrong_cover_date = REPORT_HTML.replace("August 28, 2026", "August 29, 2026")
        cells = reported_quarter_cells({**doc, "raw": wrong_cover_date.encode()}, END)
        self.assertEqual({c["metric"]: c["value"] for c in cells}, VALUES)
        self.assertTrue(all("fiscal_quarter" not in c for c in cells))

    @staticmethod
    def to_periodic():
        route = acquire("ADBE", "2026-11-19", QUESTION)
        next(route)
        rows = [
            filing("10-Q", "periodic", REPORT, END, "2026-09-16"),
            filing("8-K", "release", RELEASE, "2026-09-10", "2026-09-10"),
        ]
        primary = route.send({"data": {"snapshot": {"mode": "live"}, "filings": rows}})
        assert primary["arguments"]["primary_document"] == RELEASE
        exhibit = route.send({"ok": True, "evidence": [{"text": "Item 2.02. Results of Operations"}],
                              "data": {"linked_documents": [EXHIBIT]}})
        assert exhibit["arguments"]["exhibit_document"] == EXHIBIT
        release_rows = [{"id": "release-" + metric, "metric": metric, "value": value, "unit": "USD",
                         "period_end": END, "fiscal_quarter": "FY2026 Q3"} for metric, value in VALUES.items()]
        periodic = route.send({"ok": True, "evidence": release_rows,
                               "data": {"quarterly_gaap_operands": {r["metric"]: r["id"] for r in release_rows}}})
        return route, periodic, release_rows

    def test_cross_checked_10q_supplies_amounts_and_dates_in_the_calculation_path(self):
        route, receipt, release_rows = self.to_periodic()
        self.assertEqual(receipt["arguments"]["primary_document"], REPORT)
        self.assertTrue(receipt["arguments"]["quarter_operands"])
        tagged = [{**cell, "id": "dated-" + cell["metric"], "period_start": START} for cell in release_rows]
        readers = {r["metric"]: r["id"] for r in tagged}
        check = route.send({"ok": True, "evidence": tagged, "data": {"quarterly_gaap_operands": readers}})
        self.assertEqual(check["arguments"]["operand_ids"], ["dated-operating_income", "dated-revenue"])
        research = route.send({"ok": True})
        self.assertIn("XBRL", research["reason"])

    def test_conflicting_10q_cannot_be_delivered_as_the_exhibit_quarter(self):
        route, _, release_rows = self.to_periodic()
        release_rows[0] = {**release_rows[0], "unit": "CAD"}
        with self.assertRaises(StopIteration) as stopped:
            route.send({"ok": True, "evidence": release_rows,
                        "data": {"quarterly_gaap_operands": {r["metric"]: r["id"] for r in release_rows}}})
        self.assertIn("币种无法核对一致", str(stopped.exception.value))

    def test_missing_10q_preserves_release_operands_without_claiming_precise_start(self):
        route, _, _ = self.to_periodic()
        calculate = route.send({"ok": False, "data": {"http_status": 403}})
        self.assertEqual(calculate["arguments"]["operand_ids"], ["release-operating_income", "release-revenue"])
        research = route.send({"ok": True})
        self.assertNotIn("XBRL", research["reason"])

    @staticmethod
    def to_filing(rows):
        route = acquire("ADBE", "2026-11-19", QUESTION)
        next(route)
        primary = route.send({"data": {"snapshot": {"mode": "live"}, "filings": rows}})
        assert primary["arguments"]["primary_document"] == RELEASE
        return route

    def test_403_can_use_the_same_quarter_10q_once_with_exact_amounts_and_dates(self):
        for submitted in ("2026-09-16", "2026-09-08"):
            with self.subTest(submitted=submitted):
                # The readable primary identifies the period whether the
                # same-quarter 10-Q was filed before or after the 8-K.
                route = self.to_filing([filing("10-Q", "periodic", REPORT, END, submitted),
                                        filing("8-K", "release", RELEASE, "2026-09-10", "2026-09-10")])
                exhibit = route.send({"ok": True, "evidence": [{"text": "Item 2.02. Results of Operations"}],
                                      "data": {"linked_documents": [EXHIBIT], "disclosed_quarters": [
                                          {"quarter": "2026Q3", "end_date_text": "August 28, 2026"}]}})
                self.assertEqual(exhibit["arguments"]["exhibit_document"], EXHIBIT)
                periodic = route.send({"ok": False, "data": {"http_status": 403}})
                self.assertEqual(periodic["arguments"]["primary_document"], REPORT)
                self.assertTrue(periodic["arguments"]["quarter_operands"])
                cells = [{"id": metric, "metric": metric, "value": value, "unit": "USD", "period_start": START,
                          "period_end": END, "fiscal_quarter": "FY2026 Q3"} for metric, value in VALUES.items()]
                read = {"ok": True, "evidence": cells,
                        "data": {"quarterly_gaap_operands": {c["metric"]: c["id"] for c in cells}}}
                calculation = route.send(read)
                self.assertEqual((calculation["tool"], calculation["arguments"]["operand_ids"]),
                                 ("calculate", ["operating_income", "revenue"]))
                draft = route.send({"ok": True})
                self.assertEqual(draft["action"], "research")
                for text in ("HTTP 403", "以同季 10-Q 为依据", "XBRL"):
                    self.assertIn(text, draft["reason"])

    def test_refused_newer_release_cannot_fall_back_to_older_filed_period(self):
        route = self.to_filing([filing("10-Q", "old", REPORT, "2026-05-29", "2026-06-12"),
                                filing("8-K", "release", RELEASE, "2026-09-10", "2026-09-10")])
        route.send({"ok": True, "evidence": [{"text": "Item 2.02. Results of Operations"}],
                    "data": {"linked_documents": [EXHIBIT], "disclosed_quarters": [
                        {"quarter": "2026Q3", "end_date_text": "August 28, 2026"}]}})
        with self.assertRaises(StopIteration) as stopped:
            route.send({"ok": False, "data": {"http_status": 403}})
        self.assertIn("未找到可核实为同季的 10-Q", str(stopped.exception.value))

    def test_unreadable_8k_could_announce_a_short_period_before_a_delayed_10q(self):
        # An 8-K could announce a September transition period on October 1
        # before a late filing for the previous August quarter on October 5.
        route = self.to_filing([filing("10-Q", "older-quarter", REPORT, END, "2026-10-05"),
                                filing("8-K", "release", RELEASE, "2026-10-01", "2026-10-01")])
        with self.assertRaises(StopIteration) as stopped:
            route.send({"ok": False, "data": {"http_status": 403}})
        self.assertIn("最新相关申报的正文，尚不能核实财季", str(stopped.exception.value))
        self.assertIn("HTTP 403", str(stopped.exception.value))

    def test_alternative_10q_requires_two_amounts_and_the_disclosed_fiscal_quarter(self):
        incomplete = {"ok": True, "data": {}}
        wrong_label = {"ok": True, "data": {"quarterly_gaap_operands": {"revenue": "r", "operating_income": "o"}},
                       "evidence": [{"id": "r", "fiscal_quarter": "FY2026 Q2"},
                                    {"id": "o", "fiscal_quarter": "FY2026 Q2"}]}
        for response, reason in ((incomplete, "两项带完整期间的合并 GAAP 金额"), (wrong_label, "财年及季度尚不能核实")):
            with self.subTest(reason=reason):
                route = self.to_filing([filing("10-Q", "periodic", REPORT, END, "2026-09-16"),
                                        filing("8-K", "release", RELEASE, "2026-09-10", "2026-09-10")])
                route.send({"ok": True, "evidence": [{"text": "Item 2.02. Results of Operations"}],
                            "data": {"linked_documents": [EXHIBIT], "disclosed_quarters": [
                                {"quarter": "2026Q3", "end_date_text": "August 28, 2026"}]}})
                route.send({"ok": False, "data": {"http_status": 403}})
                with self.assertRaises(StopIteration) as stopped:
                    route.send(response)
                self.assertIn(reason, str(stopped.exception.value))

    def test_traditional_chinese_amount_and_margin_names_remain_distinct(self):
        self.assertEqual(metric_mentions("營收、營業利潤及營業利潤率"), {"revenue", "operating_income", "operating_income_margin"})
        self.assertEqual(metric_mentions("營業利益率"), {"operating_income_margin"})


    def test_verified_end_cannot_substitute_for_an_unverified_start_or_fiscal_label(self):
        released = {"id": "release", "kind": "document", "entity": "ADBE", "fiscal_quarter": "FY2026 Q3",
                    "period_end": END, "accession": "release"}
        reported = {**released, "id": "dated", "kind": "financial_fact", "period_start": START, "accession": "periodic"}
        episode = {"request": QUESTION}
        remember_latest_quarter(episode, {"tool": "read_filing", "ok": True, "arguments": {"cutoff": "2026-11-19"},
                                         "evidence_ids": [released["id"]], "data": {"filing": {
                                             **filing("8-K", "release", RELEASE, "2026-09-10", "2026-09-10"), "entity": "ADBE"}}},
                                accepted_filing=True)
        ctx = {"scope": {"cutoff": "2026-11-19", "entities": ["ADBE"]}, "episode": episode}

        def issues(statement, records):
            claim = {"id": "date", "requirement_ids": ["latest"], "text": statement, "evidence_ids": list(records)}
            return list(latest_answer_issues(ctx, {"latest": {"text": QUESTION}}, {"date": claim}, records, {}))

        missing_start, = issues(f"財季起止日期為 {START} 至 {END}。", {"release": released})
        self.assertIn("起止日期", missing_start["missing_support"])
        wrong_label, = issues(f"Adobe FY2026 Q4 截至 {END}。", {"release": released})
        self.assertIn("季度编号", wrong_label["missing_support"])
        self.assertFalse(issues(f"Adobe FY2026 Q3 截至 {END}。", {"release": released}))

        remember_latest_quarter(episode, {"tool": "read_filing", "ok": True, "arguments": {"cutoff": "2026-11-19"},
                                         "evidence_ids": [reported["id"]], "data": {"filing": {
                                             **filing("10-Q", "periodic", REPORT, END, "2026-09-16"), "entity": "ADBE"}}},
                                accepted_filing=True)
        self.assertEqual(len(episode["latest_quarter_observations"]), 2)
        self.assertFalse(issues(f"財季起止日期為 {START} 至 {END}。", {"dated": reported}))
