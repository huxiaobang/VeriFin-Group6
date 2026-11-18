import importlib.util
from unittest import TestCase, skipUnless

from verifin.cashflow_delivery import answer_parts
from verifin.reporting import report_view


class CashAnswerTests(TestCase):
    def test_no_unsupported_answers_to_distinct_questions(self):
        questions = {"a":"本季经营现金流比净利润多了多少？","b":"主要是哪些项目拉开了差额？",
                     "c":"明年经营现金流与净利润的差额呢？","d":"经营现金流和净利润的比率是多少？"}
        common = dict(period_end="2025-03-31",period_start="2025-01-01", evidence_ids=["cell"],calculation_ids=["diff"])
        money = dict(common,kind="cash_bridge",text="Cash exceeds net income.")
        drivers = dict(common,kind="cash_drivers",text="Working capital.",complete=False)
        findings=answer_parts(questions,[money,drivers],{},["Investment adjustment is absent."])
        self.assertEqual(set(findings),{"a","b"})
        self.assertEqual(findings["a"]["status"],"source_excerpt")
        self.assertEqual(findings["b"]["status"],"gap")
        self.assertFalse(answer_parts(questions,[money],{"period_end":"2025-06-30"},[]))

    def test_only_fully_answered_default_parent_is_removed(self):
        qs={"a":"Q1?","b":"Q2?"}
        default={"id":"request:ep","text":"Q1? Q2?"}
        separate={"id":"extra","text":"Cover another topic"}
        report={"request":default["text"],"request_parts":qs,"episode_id":"ep","model_mode":"offline_tools_only",
                "claims":[],"issues":[],"scope":{"requirements":[default,separate]},"unanswered":[default,separate],
                "evidence":[{"id":"e"}],"calculations":[{"id":"c"}]}
        grounded={"status":"source_excerpt","answer_sentence":"quarter total","evidence_ids":["e"],"calculation_ids":["c"]}
        report["offline_part_findings"]={"a":grounded, "b":grounded}
        self.assertEqual(report_view(report)["unanswered"],[separate])
        report["offline_part_findings"]["b"]={"status":"gap","missing_support":"missing comparison"}
        self.assertEqual(report_view(report)["unanswered"],[default,separate])


@skipUnless(importlib.util.find_spec("lxml"),"native HTML parser is optional")
class CumulativeBoundaryTests(TestCase):
    def test_unrelated_starts_and_missing_rows_do_not_create_amounts(self):
        from tests.test_cashflow_sources import CashFlowSourcesTests
        fixture=CashFlowSourcesTests()
        year=fixture.filing("annual",period_end="2024-12-29",heading="Fifty-Two Weeks Ended",
                            current="December 29, 2024",prior="December 31, 2023")
        def nine_months(**replace):
            opts=dict(period_end="2024-09-29",heading="Thirty-Nine Weeks Ended",
                      current="September 29, 2024",prior="October 1, 2023",net="21",
                      depreciation="13",adjustment="(4)",cfo="30")
            return fixture.filing("interim",**{**opts,**replace})
        valid=fixture.run_skill(year,nine_months())
        self.assertFalse(valid.gaps,valid.gaps)
        bridge=next(n for n in valid.notes if n["kind"]=="cash_bridge")
        self.assertEqual(bridge["period_start"],"2024-09-30")
        changed=fixture.run_skill(year,nine_months(current="September 30, 2024",period_end="2024-09-30"))
        self.assertFalse(changed.notes)
        self.assertTrue(changed.gaps)
        missing=fixture.run_skill(year,nine_months(depreciation="N/A"))
        self.assertTrue(missing.gaps)
        self.assertEqual([n["kind"] for n in missing.notes],["cash_bridge"])
