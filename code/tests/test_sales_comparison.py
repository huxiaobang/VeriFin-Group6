import unittest
from pathlib import Path
from unittest.mock import Mock

from verifin.retrieval import Corpus
from verifin.sales_comparison import inspect, supports_parts
from verifin.tools import ToolRegistry


QUESTIONS = {"part-1": "日均销售与总额的同比增速为什么不同？", "part-2": "调整天数后，客户需求普遍改善了吗？"}
TABLE = """Example reports second quarter of 2025 results
Fiscal quarter 2025Q2 2025Q1 2024Q1
Net sales growth YoY 2.4% 4.7% 8.7%
Daily sales growth YoY 1.3% 3.1% 6.1%
U.S. business days 63 64 63
Contract accounts daily sales YoY 3.3% 5.6% 2.2%
Other accounts daily sales YoY 1.1% -0.8% 4.1%
"""

SUMMARY = """Example reports first quarter of 2025 results
All dollar amounts are stated in millions.
Nine-month Period Three-month Period
2025 2024 Change 2025 2024 Change
Net sales $ 4,123.0 4,000.0 3.1 % $ 1,302.0 1,200.0 8.5 %
Business days 180 181 62 60
Daily sales $ 22.9 22.1 3.6 % $ 21.0 20.0 5.0 %
"""


def disclosure(body, *, entity="EX", published="2025-07-18"):
    return {"id":entity + published, "entity":entity, "text":body, "published_at":published,
            "source_url":"https://example.test/" + entity, "snapshot_hash":"test",
            "text_snapshot_path":"source.txt",
            "document_period_start":"2025-04-01", "document_report_date":"2025-06-30"}


def research(*docs):
    return inspect(Corpus(docs), "EX", "2025-07-31", "2025Q1", QUESTIONS)


class QuarterlyComparisonTests(unittest.TestCase):
    def test_raw_cells_reopen_in_their_quarter_columns(self):
        # Prefixes and non-ASCII text exercise original Unicode character offsets.
        doc = disclosure("表格\n" + "\n".join(f"L{i}: {s}" for i, s in enumerate(SUMMARY.splitlines(), 1)))
        result = research(doc)
        data = result["data"]
        self.assertEqual(data["calendar_bridge_parts"], ["part-1"])
        refs = {r["id"]: r for r in result["evidence"]}
        expected = {"2025Q1": ("1,302.0", "62"), "2024Q1": ("1,200.0", "60")}
        for period, numbers in expected.items():
            for metric, raw in zip(("net_sales", "business_days"), numbers):
                cell = data["period_operands"][period][metric]
                ref = refs[cell["evidence_id"]]
                self.assertEqual((cell["raw"], cell["value"]), (raw, raw.replace(",", "")))
                self.assertEqual((ref["text"], ref["fiscal_quarter"], ref["metric"]), (raw, period, metric))
                self.assertEqual(ref["value"], float(raw.replace(",", "")))
                self.assertEqual(ref["text"], doc["text"][ref["start"]:ref["end"]])
                self.assertIn("Nine-month Period Three-month Period", refs[cell["table_evidence_id"]]["text"])
                self.assertIn(cell["evidence_id"], data["part_findings"]["part-1"]["evidence_ids"])
                if metric == "net_sales":
                    self.assertEqual(cell["reported_unit"], "millions of dollars")
                    self.assertIn("stated in millions", refs[cell["unit_evidence_id"]]["text"])

    def test_period_groups_years_change_columns_and_metric_rows_can_move(self):
        doc = disclosure("""Example reports first quarter of 2025 results
Three-month Period Six-month Period
2024 Change 2025 Change 2025 2024
Gross profit 6 100% 12 200% 30 10
U.S. selling days 60 — 62 — 180 181
Daily sales 20.0 5.0% 21.0 3.6% 22.9 22.1
Total sales 1,200.0 8.5% 1,302.0 3.1% 4,123.0 4,000.0""")
        data = research(doc)["data"]
        self.assertEqual(data["part_findings"]["part-1"]["status"], "source_excerpt")
        cells = data["period_operands"]
        self.assertEqual(cells["2025Q1"]["net_sales"]["value"], "1302.0")
        self.assertEqual(cells["2024Q1"]["business_days"]["value"], "60")

    def test_standalone_quarter_and_dated_columns_work_without_printed_rates(self):
        for header in ("Three-month Period\n2024 2025", "Fiscal quarter 2024Q1 2025Q1"):
            with self.subTest(header=header):
                doc = disclosure("Example reports first quarter of 2025 results\n" + header +
                                 "\nSelling days 55 59\nNet sales 1,100.0 1,239.0")
                data = research(doc)["data"]
                cells = data["period_operands"]
                self.assertEqual(cells["2025Q1"]["net_sales"]["value"], "1239.0")
                self.assertEqual(cells["2024Q1"]["business_days"]["value"], "55")

    def test_unknown_duplicate_or_incomplete_columns_never_supply_four_operands(self):
        for old, new in (
                ("Nine-month Period Three-month Period", "Nine-month Period Six-month Period"),
                ("Nine-month Period Three-month Period", "Three-month Period Three-month Period"),
                ("Nine-month Period Three-month Period", "Other basis Three-month Period"),
                ("2025 2024 Change 2025 2024 Change", "2025 2024 Change 2024 2024 Change"),
                ("180 181 62 60", "180 181 62"),
                ("1,302.0 1,200.0", "1,302.0 —"),
                ("Business days 180 181 62 60", "Business days 180 181 62 60\nSelling days 180 181 66 67")):
            with self.subTest(new=new):
                data = research(disclosure(SUMMARY.replace(old, new)))["data"]
                self.assertNotIn("period_operands", data)

    def test_conflicting_source_amounts_or_rates_withhold_the_bridge(self):
        altered = SUMMARY.replace("1,302.0", "1,600.0")
        for docs in ((disclosure(altered),),
                     (disclosure(SUMMARY), disclosure(altered, published="2025-07-19"))):
            with self.subTest(n=len(docs)):
                data = research(*docs)["data"]
                self.assertNotIn("period_operands", data)
                row = data["part_findings"]["part-1"]
                self.assertEqual(row["status"], "gap")
                self.assertIn("相互一致", row["missing_support"])

    def test_prior_quarter_rows_inside_later_release_are_usable(self):
        doc = disclosure(TABLE)
        other = disclosure(TABLE.replace("4.7%", "90.0%"), entity="OTHER")
        after = disclosure(TABLE.replace("4.7%", "91.0%"), published="2025-08-01")
        result = research(doc, other, after)
        data = result["data"]
        self.assertEqual([d["document_id"] for d in data["inspected_documents"]], [doc["id"]])
        self.assertNotIn("fiscal_period", data)
        by_part = data["part_findings"]
        self.assertEqual([r["status"] for r in by_part.values()], ["source_excerpt", "source_excerpt"])
        for term in ("4.7%", "3.1%", "64", "63", "美国营业日", "吻合"):
            self.assertIn(term, by_part["part-1"]["answer_sentence"])
        for term in ("Contract accounts 5.6%", "Other accounts -0.8%", "不能据此判断"):
            self.assertIn(term, by_part["part-2"]["answer_sentence"])
        ids = {r["id"] for r in result["evidence"]}
        for record in result["evidence"]:
            self.assertEqual(record["text"], doc["text"][record["start"]:record["end"]])
        for row in by_part.values():
            self.assertTrue(row["evidence_ids"] and set(row["evidence_ids"]) <= ids)

    def test_inconsistent_growth_and_rounding_do_not_pass_bridge(self):
        data = research(disclosure(TABLE.replace("4.7%", "12.0%")))["data"]
        sales, demand = data["part_findings"].values()
        self.assertEqual(sales["status"], "gap")
        self.assertIn("相互一致", sales["missing_support"])
        self.assertEqual(demand["status"], "source_excerpt")

    def test_retroactive_prose_day_difference_has_direction_but_not_magnitude(self):
        doc = disclosure("""Retrospective facts
In Q1 2025, net sales rose 6.3% year over year.
During Q1 2025, daily sales grew 4.8% year over year.
Q1 2025 had one more selling day compared with the prior year.
""")
        sales, demand = research(doc)["data"]["part_findings"].values()
        self.assertEqual(sales["status"], "source_excerpt")
        self.assertIn("判断方向", sales["answer_sentence"])
        self.assertEqual(demand["status"], "gap")

    def test_forecasts_and_cumulative_growth_cannot_supply_quarter_sales(self):
        doc = disclosure("""Example reports fiscal 2025 results
For the first six months through Q1 2025, net sales rose 13% year over year.
In Q1 2025, we expected daily sales to rise by 12% year over year.
Quarterly sales forecast
""" + TABLE.split("\n", 1)[1])
        sales, demand = research(doc)["data"]["part_findings"].values()
        self.assertEqual((sales["status"], demand["status"]), ("gap", "gap"))
        self.assertNotIn("13%", sales["missing_support"])

    def test_work_selection_requires_only_matching_parts(self):
        self.assertTrue(supports_parts(QUESTIONS))
        self.assertFalse(supports_parts({"part-1":"美国营业日增加了吗？", "part-2":"资本开支是多少？"}))

    def test_tool_uses_active_user_questions_and_preserves_their_ids(self):
        store = Mock()
        tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / "data")
        tools.document_corpus = lambda: Corpus([disclosure(TABLE, entity="MSFT")])
        scope = {"cutoff":"2025-07-31", "entities":["MSFT"]}
        args = {"entity":"MSFT", "fiscal_quarter":"2025Q1"}
        def ask(**episode):
            store.latest_event.return_value = {"episode":{"status":"running", **episode}}
            return tools.execute("inspect_sales_comparison", args, "session", scope)
        single = ask(request=QUESTIONS["part-1"])
        self.assertEqual(list(single["data"]["part_findings"]), ["part-1"])
        compound = ask(request=" ".join(QUESTIONS.values()))
        self.assertEqual(list(compound["data"]["part_findings"]), ["part-1", "part-2"])
        clarified = ask(request="继续", inherited_parts=QUESTIONS,
                        clarified_parts={"part-2":QUESTIONS["part-2"]})
        self.assertEqual(list(clarified["data"]["part_findings"]), ["part-2"])
        self.assertFalse(ask(request="这个季度的现金余额是多少？")["ok"])
        self.assertFalse(ask(request=QUESTIONS["part-1"], status="complete")["ok"])

    def test_tool_accepts_only_the_stated_quarter_and_edited_dates(self):
        store = Mock()
        tools = ToolRegistry(store, Path(__file__).resolve().parents[1] / "data")
        tools.document_corpus = lambda: Corpus([disclosure(SUMMARY, entity="MSFT")])
        scope = {"cutoff": "2025-07-31", "entities": ["MSFT"]}
        dates = {"period_start": "2025-04-01", "period_end": "2025-06-30"}
        for label, bounds, expected in (("2025Q1", dates, True), ("2025Q2", dates, False),
                                         ("2025Q1", {**dates, "period_end": "2025-06-29"}, False)):
            with self.subTest(label=label, bounds=bounds):
                store.latest_event.return_value = {"episode": {
                    "status": "running", "request": " ".join(QUESTIONS.values()),
                    "retry_period_bounds": {"entity": "MSFT", "requested_quarter": label, **bounds},
                }}
                result = tools.execute("inspect_sales_comparison", {"entity": "MSFT", "fiscal_quarter": "2025Q1"},
                                       "session", scope)
                self.assertEqual(result["ok"], expected)
                if expected:
                    self.assertEqual(result["data"]["fiscal_period"], {"entity": "MSFT", "quarter": label, **dates})
