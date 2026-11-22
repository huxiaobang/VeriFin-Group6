"""A link delivered for a financial result must resolve to its cited SEC source."""

from unittest import TestCase

from tests import test_live_latest_quarter as latest
from verifin.evaluation import FrozenCase
from verifin.model import validate_response
from verifin.reporting import report_view
from verifin.verification import check_rules


OTHER_QUARTER = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/nvda-20260726.htm"


class SourceModel(latest.FixtureModel):
    def complete(self, role, context):
        result = super().complete(role, context)
        if role == "researcher":
            claim = result["claims"][-1]
            source = "SEC 原表 " + OTHER_QUARTER
            result["summary"] = result["summary"].replace(claim["text"], source)
            claim["text"] = source
            return validate_response(role, result, context)
        return result


class SECFilingLinkTests(TestCase):
    def test_same_issuer_links_bind_to_each_quarter_and_original_status(self):
        q1 = "https://www.sec.gov/Archives/edgar/data/789019/000095017024118967/first.htm"
        q2 = "https://www.sec.gov/Archives/edgar/data/789019/000095017025010491/second.htm"
        later = "https://www.sec.gov/Archives/edgar/data/789019/000119312525256321/later.htm"
        # The primary file names do not encode the report dates.
        facts = [{"id": ref, "kind": "financial_fact", "entity": "MSFT", "form": "10-Q",
                  "filing_url": url, "period_start": start, "period_end": end,
                  "filing_link_provenance": {"report_date": report_date},
                  "fiscal_period_context": {"fy": fiscal_year, "fp": quarter}}
                 for ref, url, start, end, report_date, fiscal_year, quarter in (
                     ("q1", q1, "2024-07-01", "2024-09-30", "2024-09-30", 2025, "Q1"),
                     ("q2", q2, "2024-10-01", "2024-12-31", "2024-12-31", 2025, "Q2"),
                     ("prior-column", later, "2024-07-01", "2024-09-30", "2025-09-30", 2026, "Q1"))]
        context = {"request": "请列出微软 FY2025 Q1 和 Q2 的 SEC 申报。", "evidence": facts}
        q1_inline = q1.replace("https://www.sec.gov/", "https://www.sec.gov/ix?doc=/")
        cases = [
            (f"FY2025 Q1 的 SEC 申报为 {q1_inline}；FY2025 Q2 的 SEC 申报为 {q2}。", True),
            (f"FY2025 Q2 的 SEC 申报为 {q2}；FY2025 Q1 的 SEC 申报为 {q1}。", True),
            (f"FY2025 Q1 的 SEC 申报为 {q2}；FY2025 Q2 的 SEC 申报为 {q1}。", False),
            (f"FY2025 Q1 的原始 SEC 申报为 {later}。", False),
            (f"FY2025 Q1 的比较数据见微软后来提交的 FY2026 Q1 季报 {later}。", True),
            (f"FY2025 Q1 比较数据的原始出处是微软后来提交的季报 {later}。", True),
            (f"FY2025 Q2 的比较数据见微软后来提交的季报 {later}。", False),
        ]
        for text, supported in cases:
            with self.subTest(text=text):
                claim = {"id": "links", "text": text, "requirement_ids": [], "constraints": {},
                         "evidence_ids": [r["id"] for r in facts]}
                candidate = {**context, "claims": [claim]}
                live = check_rules(candidate)
                self.assertEqual(check_rules(FrozenCase.from_context("quarter-links", candidate).context()), live)
                issues = [issue for issue in live if "stated quarter" in issue["missing_support"]]
                self.assertEqual(not issues, supported)

    def test_wrong_quarter_link_removes_only_source_answer(self):
        fixture = latest.LatestQuarterTests()
        report, calls, urls = fixture.run_question(fixture.responses(), model=SourceModel())
        self.assertEqual(report["status"], "partial")
        answers = report_view(report)["question_answers"]
        self.assertEqual([row["status"] for row in answers], ["answered"] * 6 + ["gap"])
        self.assertNotIn(OTHER_QUARTER, str(answers))
        self.assertEqual([call["tool"] for call in calls], latest.ACQUISITION)
        self.assertEqual(urls, latest.SEC_URLS)

    def test_frozen_links_use_cited_file_and_matched_amount_even_with_another_cited_filing(self):
        primary = latest.SEC_URLS[1]
        amounts = {"id": "q3", "kind": "financial_fact", "entity": "NVDA", "metric": "revenue",
                   "source_url": latest.URL, "filing_url": primary, "value": 115_844_000_000, "unit": "USD"}
        other = {"id": "q2", "kind": "document", "entity": "NVDA", "source_url": OTHER_QUARTER}
        claim = {"id": "link", "requirement_ids": [], "constraints": {}, "evidence_ids": ["q3", "q2"]}
        context = {"request": "Give the revenue and cite the filing.", "claims": [claim], "evidence": [amounts, other]}
        inline = latest.URL.replace("https://www.sec.gov/", "https://www.sec.gov/ix?doc=/") + "#statement"
        sibling = latest.URL.replace(latest.EXHIBIT, "unread-sibling.htm")
        for shown, supported in ((latest.URL, True), (primary, True), (inline, True),
                                 (OTHER_QUARTER, False), (sibling, False)):
            with self.subTest(shown=shown):
                claim["text"] = f"Revenue was USD 115,844 million. [SEC filing]({shown})"
                frozen = FrozenCase.from_context("filing-link", context).context()
                live = check_rules(context)
                self.assertEqual(check_rules(frozen), live)
                link_issues = [issue for issue in live if "displayed filing link" in issue["missing_support"]]
                self.assertEqual(not link_issues, supported)
