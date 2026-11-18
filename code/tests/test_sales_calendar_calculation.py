import unittest
from decimal import Decimal

from tests.test_sales_comparison import QUESTIONS, disclosure, research
from verifin.reporting import report_view
from verifin.sales_calendar import attach_calendar_calculation, calendar_growth_rates


class SalesCalendarCalculationTests(unittest.TestCase):
    def test_reconcile_when_neither_growth_rate_is_printed(self):
        text = """Example reports first quarter of 2025 results
Fiscal quarter 2025Q1 2024Q1
Net sales 1200 1000
Business days 60 55
"""
        result = research(disclosure(text))
        attach_calendar_calculation(result, "EX")
        calc, = result["calculations"]
        self.assertAlmostEqual(calc["value"], 10.0)
        rows = result["data"]["part_findings"]
        self.assertNotIn("missing_support", rows["part-1"])
        self.assertEqual(rows["part-1"]["calculation_ids"], [calc["id"]])
        self.assertNotIn("calculation_ids", rows["part-2"])
        self.assertEqual(rows["part-2"]["status"], "gap")
        view = {"model_mode": "offline_tools_only", "request_parts": QUESTIONS,
                "evidence": result["evidence"], "calculations": [calc], "offline_part_findings": rows}
        self.assertEqual(report_view(view)["question_answers"][0]["status"], "calculated")
        self.assertEqual(report_view(dict(view, calculations=[dict(calc, stale=True)]))
                         ["question_answers"][0]["status"], "pending")

    def test_sign_and_unusable_denominators(self):
        total, daily, gap = calendar_growth_rates(900, 1000, 50, 60)
        self.assertEqual((total, daily, gap), (Decimal(-10), Decimal(8), Decimal(-18)))
        for values in ((1, 0, 60, 62), (1, 2, 0, 60), (1, 2, 60.5, 61), ("NaN", 2, 60, 61)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                calendar_growth_rates(*values)
