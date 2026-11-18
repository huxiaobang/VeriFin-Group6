import unittest

from tests.test_sales_calendar_review import review_fixture
from tests.test_sales_comparison import SUMMARY, disclosure, research
from verifin.context import current_tool_receipts, reopen_comparison_receipt
from verifin.engine import digest
from verifin.request_review import check_comparison_coverage
from verifin.sales_calendar import attach_calendar_calculation
from verifin.storage import SessionStore
from verifin.verification import check_rules


class QuarterAuditTests(unittest.TestCase):
    def test_quarter_changes_and_outlook_headers_end_the_previous_table_scope(self):
        for title, expected in (("2025Q2", False), ("Outlook.", False),
                                ("2025Q1 Outlook:", False), ("2025Q1", True)):
            with self.subTest(title=title):
                result = research(disclosure(SUMMARY.replace("Nine-month Period", title + "\nNine-month Period", 1)))
                attach_calendar_calculation(result, "EX")
                self.assertEqual(bool(result.get("calculations")), expected)

    def test_customer_percentages_use_accounting_sign_and_incomplete_rows_are_visible(self):
        table = """Example reports first quarter of 2025 results
We report customers in two categories. The DSR change compared with the prior year follows:
DSR Change
Three-month Period
% of Sales
Three-month Period
2025 2024 2025 2024
National accounts 5.6 % 8.6 % 63.6 % 60.8 %
Non-national accounts NEGATIVE -1.9 % 36.4 % 39.2 %"""
        for literal in ("-4.1 %", "(4.1 %)", "(4.1) %", "N/A"):
            with self.subTest(literal=literal):
                row = research(disclosure(table.replace("NEGATIVE", literal)))["data"]["part_findings"]["part-2"]
                if literal == "N/A":
                    self.assertEqual(row["status"], "gap")
                    self.assertTrue(row["evidence_ids"])
                else:
                    self.assertIn("Non-national accounts -4.1%", row["answer_sentence"])
                    self.assertIn("不能据此判断", row["answer_sentence"])

    def test_correct_gap_with_an_explicitly_wrong_day_count_fails(self):
        for day_statement, expected in (("本期比上年同期少了一个营业日", "gap"),
                                        ("营业日分别为63天和64天", "gap"),
                                        ("本期比上年同期多了一个营业日", "answered"),
                                        ("2024Q1比2025Q1少1个营业日", "answered"),
                                        ("如果少了1个营业日，影响会相反。本期实际多了1个营业日", "answered"),
                                        ("上年同期和本期的营业日分别为63天和64天", "answered")):
            with self.subTest(statement=day_statement):
                ctx, rows = review_fixture(day_statement + "。总额增速减日均增速约为1.62个百分点。", True)
                self.assertEqual(check_comparison_coverage(ctx, rows)[0][0]["status"], expected)
                self.assertEqual(any(i["issue_type"] == "numerical_inconsistency" for i in check_rules(ctx)),
                                 expected == "gap")

    def test_other_tool_calls_cannot_erase_this_turns_quantitative_obligation(self):
        ctx, rows = review_fixture()
        receipt, = ctx["episode"]["recent_tool_results"]
        recent = [{"tool":"read_document", "ok":True, "offset":n} for n in range(6, 14)]
        ctx["episode"].update(id="this-turn", recent_tool_results=recent)
        for item in ctx["evidence"] + ctx["calculations"]:
            item["version"] = 1
        with SessionStore(":memory:") as store:
            ctx["session"] = store.create_session()
            observation = {"data":receipt["data"], "calculations":ctx["calculations"], "evidence":ctx["evidence"]}
            event = {"type":"tool_call", "tool":receipt["tool"], "arguments":receipt["arguments"], "ok":True,
                     "episode_id":"this-turn", "observation":observation, "observation_sha256":digest(observation)}
            store.add_event(ctx["session"]["id"], event)
            store.add_event(ctx["session"]["id"], {**event, "episode_id":"other-turn", "observation_sha256":"invalid"})
            self.assertEqual(check_comparison_coverage(ctx, rows)[0], rows)
            reopen_comparison_receipt(ctx, store)
            restored = current_tool_receipts(ctx)
            self.assertEqual(check_comparison_coverage(restored, rows)[0][0]["status"], "gap")
            self.assertEqual(restored["episode"]["recent_tool_results"][0]["event_sequence"], 1)
            self.assertEqual(restored["episode"]["recent_tool_results"][1:], recent)
            ctx["calculations"][0]["version"] = 2
            self.assertEqual(current_tool_receipts(ctx)["episode"]["recent_tool_results"][0]["record_status"],
                             "superseded_or_unavailable")
            ctx["episode"].update(id="new-turn", recent_tool_results=recent)
            reopen_comparison_receipt(ctx, store)
            self.assertEqual(ctx["episode"]["recent_tool_results"], recent)
            ctx["episode"]["id"] = "this-turn"
            observation["data"]["part_findings"]["part-1"] = {"status":"gap"}
            observation["calculations"] = []
            store.add_event(ctx["session"]["id"], {**event, "observation_sha256":digest(observation)})
            reopen_comparison_receipt(ctx, store)
            self.assertEqual(check_comparison_coverage(current_tool_receipts(ctx), rows)[0], rows)
            store.add_event(ctx["session"]["id"], {**event, "observation_sha256":"invalid"})
            ctx["episode"]["recent_tool_results"] = recent
            reopen_comparison_receipt(ctx, store)
            self.assertEqual(ctx["episode"]["recent_tool_results"], recent)
