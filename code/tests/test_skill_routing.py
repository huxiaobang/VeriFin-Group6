import unittest

from verifin.data import DataError
from verifin.financial import chart_comparison
from verifin.model import ModelClient
from verifin.skill_calculations import next_action, prepare
from verifin.skill_routing import instructions, select_skills
from verifin.tools import TOOL_SPECS


class SkillRoutingTests(unittest.TestCase):
    def test_route_current_question_and_pure_followup(self):
        old = ["为什么应收占用了现金？"]
        self.assertEqual(select_skills("具体怎么算？", old), ["cash-flow-bridge"])
        self.assertEqual(select_skills("改看业绩指引。", old), ["implied-period-guidance"])
        self.assertEqual(select_skills("股价是多少？", old), [])
        self.assertEqual(select_skills("那家公司的股价呢？", old), [])
        self.assertEqual(select_skills("不用看现金流，只看全年目标。", old), ["implied-period-guidance"])
        self.assertEqual(select_skills("同店和新店应怎么比较？"), ["store-cohorts"])
        self.assertEqual(select_skills("门店利润率为什么比集团高？"), ["unit-profit-reconciliation"])

    def test_all_cards_delivered_to_role_and_matched_review_conditions(self):
        question = "经销商库存和经营现金流分别怎么样？"
        selected = select_skills(question)
        self.assertEqual(set(selected), {"dealer-inventory", "cash-flow-bridge"})
        client = ModelClient("unused", "offline", "https://example.invalid")
        context = {"episode": {"request": question, "research_skills": selected}, "tools": TOOL_SPECS}
        for role in ("planner", "researcher", "critic", "generic_reviewer"):
            text = client._research_role(role, context)
            for name in selected:
                addition = instructions([name], "critic" if role == "generic_reviewer" else role)
                self.assertTrue(addition)
                self.assertIn(addition, text)
            serialized = client._cached_tool_input(role, context)["input"]
            self.assertTrue(any(m["role"] == "developer" and m["content"] == text for m in serialized))
        self.assertNotIn("Project research method", client._research_role("planner", {"episode": {}}))

    def test_explicit_pack_takes_precedence(self):
        from verifin.research_methods import configure_methods, PACK_NAME
        client = configure_methods(ModelClient("unused", "offline", "https://example.invalid"), PACK_NAME)
        text = client._research_role("planner", {"episode": {"research_skills": ["dealer-inventory"]}})
        self.assertIn(client.role_guidance["planner"], text)
        self.assertNotIn(instructions(["dealer-inventory"], "planner"), text)

    def test_ambiguous_or_missing_quarters_cannot_pick_other_year_as_neighbor(self):
        scope = {"entities": ["EXAMPLE"], "cutoff": "2031-01-31"}
        history = ["这家公司2030年第四季表现如何？"]
        def work(question):
            return prepare(question, history, select_skills(question), scope)
        for question in ("这季美国经销商库存减少多少？", "换成另一家公司，这季经销商库存减少多少？",
                         "这季经销商库存环比和同比各变了多少？"):
            self.assertIsNone(work(question))
        state = work("这季经销商库存少了多少？")
        cells = [{"id": label, "entity": "EXAMPLE", "cell_type": "chart_inventory_cell",
                  "figure_id": "series", "row_label": "Dealer inventory", "chart_period": label}
                 for label in ("2030Q4", "2029Q4")]
        state.update(steps=[{"tool":"search_documents", "ok":True}], queries=[], operands=[c["id"] for c in cells])
        self.assertIsNone(next_action(state, {"evidence":cells}))
        self.assertIn("gap", state)

    def test_calculator_rejects_cross_figure_and_invalid_or_reversed_periods(self):
        a = {"cell_type":"chart_inventory_cell", "figure_id":"series", "entity":"EXAMPLE",
             "metric":"dealer units", "unit":"units", "chart_period":"2030Q4"}
        b = {**a, "chart_period":"2030Q3"}
        self.assertEqual(chart_comparison(a, b, "difference"), ("2030Q3", "2030Q4"))
        for wrong in ({**b, "figure_id":"other"}, {**b, "chart_period":"2030Q0"}, {**b, "chart_period":"2031Q1"}):
            with self.assertRaises(DataError):
                chart_comparison(a, wrong, "difference")
