import importlib.util
import unittest

from tests.test_tools import DATA
from verifin.engine import ResearchEngine
from verifin.operating_margin import _quarter_sentences, margin_reconciliation
from verifin.reporting import question_answers
from verifin.scope import stated_cutoff
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class WithoutPriorRevenue(ToolRegistry):
    """Exercise a returned table missing its comparative quarterly denominator."""
    def execute(self, name, args, session_id, scope):
        result = super().execute(name, args, session_id, scope)
        if name in {"search_documents", "read_document"}:
            missing = {r["id"] for r in result["evidence"] if r.get("cell_type") == "statement_amount_cell"
                       and r["metric"] == "revenue" and r["period_start"] == "2023-10-01"}
            result["evidence"] = [r for r in result["evidence"] if r["id"] not in missing]
            data = result["data"]
            data["numeric_operand_ids"] = [i for i in data.get("numeric_operand_ids", []) if i not in missing]
        return result


class QuarterlyOperatingMargin(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
    def test_elliptical_expense_question_uses_the_prior_quarter_but_does_not_survive_a_metric_switch(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            engine = ResearchEngine(store, None, ToolRegistry(store, DATA))
            engine.ask(sid, "截至2025年1月30日，MSFT 2025财年第二季度的营业利润率同比变了多少？")
            continued = engine.ask(sid, "哪些费用有贡献，哪些拖了后腿？")["report"]
            answer, = question_answers(continued)
            self.assertEqual(answer["status"], "calculated")
            self.assertIn("Sales and marketing", answer["answer_sentence"])
            self.assertEqual(continued["usage"]["tool_calls"], 1)
            switched = engine.ask(sid, "那经营现金流呢？")["report"]
            self.assertEqual(switched["followup_focus"]["metric"], "operating_cash_flow")
            self.assertEqual(switched["skill_fiscal_quarter"]["period_end"], "2024-12-31")
            again = engine.ask(sid, "哪些费用有贡献？")["report"]
            self.assertNotIn("followup_focus", again)

    def test_cutoff_requires_a_date_clause_separate_from_the_period(self):
        self.assertEqual(stated_cutoff("截至2025年1月30日，最近一季的业绩如何？"), "2025-01-30")
        self.assertIsNone(stated_cutoff("截至2024年12月31日的季度收入是多少？"))

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
    def test_same_followup_calculates_only_what_the_returned_quarter_cells_support(self):
        for registry, complete in ((ToolRegistry, True), (WithoutPriorRevenue, False)):
            with self.subTest(complete=complete), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                engine = ResearchEngine(store, None, registry(store, DATA))
                engine.ask(sid, "截至2025年1月30日，MSFT 2025财年第二季度的表现怎么样？")
                result = engine.ask(sid, "这一季的营业利润率同比变化多少个百分点？公司披露了哪些原因？")
                report = result["report"]
                self.assertEqual(report["usage"]["model_calls"], 0)
                self.assertFalse(report["verification_available"])
                self.assertEqual(report["scope"]["cutoff"], "2025-01-30")
                numbers, causes = question_answers(report)
                calcs = {c["id"]: c for c in report["calculations"]}
                if complete:
                    now, prior, change = (calcs[k] for k in numbers["calculation_ids"])
                    self.assertEqual([now["operation"], prior["operation"], change["operation"]],
                                     ["margin", "margin", "percentage_point_change"])
                    self.assertEqual(change["operand_ids"], [now["id"], prior["id"]])
                    self.assertEqual((now["period_start"], prior["period_start"]), ("2024-10-01", "2023-10-01"))
                    self.assertAlmostEqual(change["value"], 1.87, places=2)
                else:
                    self.assertEqual(numbers["status"], "gap")
                    self.assertIn("2024Q2的营业收入", numbers["missing_support"])
                    self.assertEqual(len(calcs), 1)
                    self.assertAlmostEqual(calcs[numbers["calculation_ids"][0]]["value"], 45.46, places=2)
                self.assertIn("cloud engineering", causes["answer_sentence"])
                self.assertNotEqual(numbers.get("missing_support"), causes.get("answer_sentence"))

    def test_full_year_section_does_not_supply_a_quarter_driver(self):
        text = ("Fourth Quarter Results:\nOperating income increased due to lower rent.\n"
                "Full Year Highlights:\nOperating income increased due to pricing.")
        self.assertEqual(list(_quarter_sentences(text, True)), ["Operating income increased due to lower rent."])

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
    def test_partial_comparative_row_and_conflicting_subtotal_leave_a_gap(self):
        from tests.test_statement_amount_cells import StatementAmountTests
        fixture = StatementAmountTests()
        self.addCleanup(fixture.doCleanups)
        registry, args, observation, _ = fixture.run_fixture(expenses=[
            ("Marketing expenses", "80", "90", "300", "250"),
            ("Personnel costs", "32", "112", "100", "50"),
            ("Total operating expenses", "112", "202", "400", "300")])
        scope = {"entities": ["AAA"], "cutoff": "2025-02-21"}
        end = "2024-12-29"
        tool = "reconcile_operating_margin"
        result = registry.execute(tool, dict(args, period_end=end), "session", scope)
        self.assertTrue(result["ok"], result)
        bridge = result["data"]["margin_reconciliation"]
        self.assertEqual(bridge["status"], "complete")
        self.assertEqual(bridge["changes"]["unexplained"], 0)
        self.assertEqual([p["residual_amount"] for p in bridge["periods"].values()], [0, 0])
        self.assertEqual(len(bridge["rows"]), 2)
        cells = observation["evidence"]
        missing_id = bridge["rows"][0]["current"]["cell_id"]
        partial = margin_reconciliation([c for c in cells if c["id"] != missing_id], entity="AAA", period_end=end)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["periods"]["current"]["residual_amount"], 80000)
        self.assertEqual(partial["periods"]["prior"]["residual_amount"], 0)
        self.assertIsNone(partial["rows"][0]["contribution_pp"])
        self.assertAlmostEqual(partial["changes"]["unexplained"], (90 / 990 - 80 / 1100) * 100)
        self.assertIsNotNone(partial["rows"][1]["contribution_pp"])
        self.assertTrue(any("Marketing expenses" in gap for gap in partial["missing"]))
        self.assertNotIn(missing_id, partial["evidence_ids"])
        total_id = bridge["periods"]["current"]["reported_total"]["cell_id"]
        conflict = margin_reconciliation([dict(c, value=125000) if c["id"] == total_id else c for c in cells],
                                         entity="AAA", period_end=end)
        self.assertEqual(conflict["status"], "partial")
        self.assertTrue(any("费用总额" in gap for gap in conflict["missing"]))
        for outside in (dict(scope, cutoff="2024-12-29"), dict(scope, entities=["BBB"])):
            self.assertFalse(registry.execute(tool, dict(args, period_end=end), "session", outside)["ok"])

    @unittest.skipUnless(importlib.util.find_spec("lxml"), "native HTML disclosure parser is optional")
    def test_bounded_other_income_requires_a_reconciled_profit_sign(self):
        from tests.test_statement_amount_cells import StatementAmountTests
        fixture = StatementAmountTests()
        self.addCleanup(fixture.doCleanups)
        for label, current, expected in [
            ("Other income", "10", "complete"),
            ("Other income", "11", "partial"),
            ("Other expense (income), net", "10", "partial"),
        ]:
            with self.subTest(label=label, current=current):
                _, _, result, _ = fixture.run_fixture(expenses=[
                    ("Gross profit margin", "15%", "18%", "17%", "17%"),
                    ("Personnel costs", "122", "222", "300", "340"),
                    (label, current, "20", "30", "10")])
                bridge = margin_reconciliation(result["evidence"], entity="AAA", period_end="2024-12-29")
                self.assertEqual(bridge["status"], expected)
                self.assertNotIn("gross_profit_margin", {r["key"] for r in bridge["rows"]})
                moves = {m["key"]: m["value"] for m in bridge["amount_changes"]}
                if expected == "complete":
                    self.assertEqual(moves, {"gross_profit": 10000, "personnel_costs": 100000,
                                             "other_income": -10000, "operating_income": 100000})
                    self.assertEqual(bridge["changes"]["unexplained"], 0)
                else:
                    self.assertEqual(set(moves), {"gross_profit", "personnel_costs", "operating_income"})
