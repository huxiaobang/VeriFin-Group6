import unittest

from verifin.income_followup import current_bridge, parts_for, plan, reason_action, wants_explanation


class IncomeFollowupTests(unittest.TestCase):
    def test_why_for_another_metric_does_not_trigger_a_lookup_on_the_bridge(self):
        parts = {"a":"营业利润和净利润的同比变动怎么对账？","b":"收入为什么下降？"}
        self.assertFalse(wants_explanation(" ".join(parts.values()), parts, ["a"]))
        self.assertTrue(wants_explanation("为什么营业利润涨了，净利润降了？"))
        self.assertFalse(wants_explanation("为什么方向不同？只列金额，不用解释。"))

    def test_one_search_with_missing_causes_is_a_terminal_result(self):
        ctx = {"scope":{"entities":["EXAMPLE"]}, "income_explanation":{"gaps":[{"metric":"unexplained"}],
               "source_review":"existing_evidence"}}
        self.assertEqual(reason_action(ctx)["arguments"], {"entity":"EXAMPLE"})
        ctx["income_explanation"]["source_review"] = "targeted_read"
        self.assertIsNone(reason_action(ctx))

    def test_children_only_attach_to_the_same_comparison(self):
        question = "营业利润上涨，净利润为什么下降？"
        parts = {"a": question, "b": "主要差在哪儿？", "c": "经营现金流是多少？"}
        self.assertEqual(parts_for(" ".join(parts.values()), parts), ["a", "b"])
        for qualifier in ("环比", "调整后", "全年", "分部"):
            self.assertFalse(parts_for(qualifier + question, None))
        self.assertFalse(parts_for("净利息费用下降为什么会影响营业利润？", None))

    def test_quarter_shift_cannot_use_previous_window_as_the_new_one(self):
        scope = {"entities": ["EXAMPLE"], "cutoff": "2026-10-01"}
        prev = {"period_start": "2026-04-01", "period_end": "2026-06-30"}
        shift = {"period_start": "2026-01-01", "period_end": "2026-03-31"}
        q = "上一季营业利润上涨，净利润为什么下降？"
        self.assertIn("gap", plan(q, None, scope, prev, referring=True))
        self.assertEqual(plan(q, None, scope, prev, referring=True, resolved_quarter=shift)["arguments"],
                         {"entity": "EXAMPLE", **shift})

    def test_previous_turn_receipt_cannot_answer_different_question_in_same_scope(self):
        q = "营业利润上涨，净利润为什么下降？"
        receipt = {"tool": "reconcile_net_income", "income_request": "上一问", "ok": True,
                   "scope_version": 3, "arguments": {"entity": "EXAMPLE"}, "data": {"income_bridge": {"complete": True}}}
        ctx = {"episode": {"id": "this", "request": q, "recent_tool_results": [receipt]},
               "session": {"scope_version": 3}, "scope": {"entities": ["EXAMPLE"]}}
        self.assertIsNone(current_bridge(ctx))
        receipt["income_request"] = q
        self.assertTrue(current_bridge(ctx)["complete"])
        ctx["scope"]["entities"].append("OTHER")
        self.assertIsNone(current_bridge(ctx))
