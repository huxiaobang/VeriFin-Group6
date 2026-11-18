import unittest

from verifin.engine import ResearchEngine
from verifin.request_review import clarify_request
from verifin.storage import SessionStore
from tests.test_engine import CLAIM, FACT, RESEARCH, REVIEW, SCOPE, FixtureTools, ScriptedModel


class ClarifiedRequestTests(unittest.TestCase):
    def test_later_question_survives_planner_checklist_omission(self):
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            original = "Report on this company."
            clarification = "Use the December quarter. How much was revenue? Does it establish market-wide growth?"
            roles = ScriptedModel([
                ("planner", {"action":"clarify","scope":SCOPE,"question":"Which period and metrics?"}),
                ("planner", {"action":"research","requirements":SCOPE["requirements"]}),
                ("researcher", RESEARCH),
                ("critic", {**REVIEW,"request_coverage":[{"part":"part-1","status":"answered",
                           "claim_id":CLAIM["id"],"answer_sentence":CLAIM["text"]}]}),
                ("planner", {"action":"finish"})])
            engine = ResearchEngine(store,roles,FixtureTools())
            engine.ask(sid,original)
            store.put_evidence(sid,FACT)
            result = engine.ask(sid,clarification)
            for _,context in roles.calls[1:]:
                self.assertEqual(context["episode"]["request"],original+"\n"+clarification)
                self.assertEqual(context["request_parts"]["part-2"],"Does it establish market-wide growth?")
            self.assertEqual(result["status"],"partial")
            self.assertTrue(any(i["target_id"]=="part-2" for i in result["report"]["issues"]))

    def test_parameters_and_unpunctuated_mandates(self):
        original = "销售同比增长了多少？"
        _, date_parts, new = clarify_request(original, "2024 财年第四季度。")
        self.assertEqual((date_parts,new),({},False))
        _, parts, new = clarify_request(original,"请另列经营现金流")
        self.assertTrue(new)
        self.assertEqual(parts,{"part-1":original,"part-2":"请另列经营现金流"})
        updated, only, new = clarify_request(original,"只回答销售增长，并说明能否外推到行业")
        self.assertEqual(only,{"part-1":"只回答销售增长","part-2":"并说明能否外推到行业"})
        _, narrowed, new = clarify_request(updated,"第四季度",only)
        self.assertFalse(new)
        self.assertEqual(narrowed,{"part-1":only["part-1"],"part-2":only["part-2"]+"\n第四季度"})
