import unittest

from verifin.evaluation import FrozenCase
from verifin.verification import check_rules


class QuarterDurationTests(unittest.TestCase):
    def check_period(self, question, text, facts, *, request=None):
        sources = [
            {"id": f"source-{index}", "kind": "financial_fact", "entity": "MSFT",
             "value": amount, "unit": "%", "metric": "revenue_growth",
             "period_start": start, "period_end": end, "source_url": "https://www.sec.gov/Archives/example",
             "locator": "statement of operations", "snapshot_hash": "frozen", "published_at": "2024-07-30"}
            for index, (start, end, amount) in enumerate(facts)
        ]
        context = {"request": request or question,
                   "requirements": [{"id": "question", "text": question}],
                   "scope": {"entities": ["MSFT"], "period_end": "2024-06-30", "cutoff": "2024-11-01"},
                   "claims": [{"id": "claim", "text": text, "requirement_ids": ["question"],
                               "evidence_ids": [row["id"] for row in sources], "calculation_ids": []}],
                   "evidence": sources, "calculations": []}
        findings = check_rules(context)
        self.assertEqual(check_rules(FrozenCase.from_context("quarter-duration", context).context()), findings)
        return [issue for issue in findings if issue.get("scope") == "answer_period"]

    def test_q4_with_the_same_end_as_the_year_still_requires_a_quarter(self):
        for start in ("2023-07-01", "2023-10-01", "2024-01-01"):
            with self.subTest(start=start):
                findings = self.check_period("2024Q4，请回答微软营收同比增长多少？",
                                             "微软 FY2024 Q4 营收同比增长 15.67%。",
                                             [(start, "2024-06-30", 15.669962)], request="那上一季呢？")
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]["evidence_refs"], ["source-0"])

    def test_preserves_valid_quarter_annual_and_mixed_disclosures(self):
        annual = ("2023-07-01", "2024-06-30", 15.669962)
        quarter = ("2024-04-01", "2024-06-30", 15.195145)
        long_quarter = ("2024-03-11", "2024-06-30", 15.195145)  # 16-week fiscal quarter
        for fact in (quarter, long_quarter):
            self.assertEqual(self.check_period("FY2024 Q4 营收同比增长多少？",
                                              "FY2024 Q4 营收同比增长 15.20%。", [fact]), [])
        self.assertEqual(self.check_period("FY2024 全年营收同比增长多少？",
                                          "FY2024 全年营收同比增长 15.67%。", [annual]), [])
        question = "FY2024 Q4 和全年营收同比增长各是多少？"
        self.assertEqual(self.check_period(question, "FY2024 Q4 营收同比 15.20%，全年营收同比 15.67%。",
                                          [quarter, annual]), [])
        self.assertTrue(self.check_period(question, "FY2024 Q4 营收同比 15.67%，全年营收同比 15.67%。",
                                         [quarter, annual]))

    def test_quarter_end_observation_without_a_start_is_not_cumulative(self):
        self.assertEqual(self.check_period("FY2024 Q4 末的资产负债比是多少？",
                                          "FY2024 Q4 末的资产负债比是 15.67%。",
                                          [(None, "2024-06-30", 15.669962)]), [])
