import json
import unittest

from verifin.context import as_of_context
from verifin.engine import ResearchEngine
from verifin.storage import SessionStore
from tests.test_engine import FixtureTools, ScriptedModel, full_script, FACT, SCOPE


class RewoundSessionTests(unittest.TestCase):
    def test_user_cutoff_is_applied_before_first_planner_and_cannot_be_widened(self):
        early = "2025-02-05T16:00:00-05:00"
        late = "2025-02-05T16:24:21-05:00"
        for message, option in [("研究时点为 " + early + "，营业收入呢？", None),
                                ("Return to the market close.", early)]:
            with self.subTest(message=message), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                store.update_scope(sid,{**SCOPE,"cutoff":late})
                store.put_evidence(sid,{**FACT,"published_at":late})
                model = ScriptedModel([("planner", {"action":"clarify","question":"Which metric?",
                                                   "scope":{"cutoff":late}})])
                result = ResearchEngine(store,model,FixtureTools()).ask(sid,message,as_of=option)
                self.assertEqual(result["status"],"waiting_for_clarification")
                seen = model.calls[0][1]
                self.assertEqual(seen["scope"]["cutoff"], early)
                self.assertEqual(seen["evidence"], [])
                self.assertEqual(store.get_session(sid)["scope"]["cutoff"], early)
                with self.assertRaisesRegex(ValueError,"conflicts"):
                    ResearchEngine(store,model,FixtureTools()).ask(sid,"As of " + late,as_of=early)
                self.assertEqual(len(model.calls), 1)

    def test_tightening_scope_while_waiting_removes_prior_tool_payload(self):
        early = "2025-02-05T16:00:00-05:00"
        release = "2025-02-05T16:24:21-05:00"
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            script = [("planner",{"action":"tool","tool":"financial_facts","arguments":{"entity":"MSFT"},
                                 "scope":{**SCOPE,"cutoff":release}}),
                      ("planner",{"action":"clarify","question":"Which metric?"}),
                      ("planner",{"action":"clarify","question":"Which period?"})]
            model = ScriptedModel(script)
            tools = FixtureTools(result={"ok":True,"summary":"late private marker","data":{},
                                         "evidence":[{**FACT,"published_at":release}],"calculations":[]})
            engine = ResearchEngine(store, model, tools)
            self.assertEqual(engine.ask(sid,"Give me the latest figures")["status"], "waiting_for_clarification")
            store.update_scope(sid,{**SCOPE,"cutoff":early})
            engine.ask(sid,"Revenue")
            self.assertNotIn("late private marker",json.dumps(model.calls[-1][1]))
            self.assertEqual(model.calls[-1][1]["evidence"],[])

    def test_publication_boundary_preserves_earlier_record_and_archive(self):
        early = "2025-02-05T16:00:00-05:00"
        release = "2025-02-05T16:24:21-05:00"
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            late_tools = FixtureTools(result={"ok": True, "summary":"known from later source",
                "data":{}, "calculations":[], "evidence":[{**FACT, "published_at":release}]})
            script = full_script()
            script[0][1]["scope"] = {**SCOPE,"cutoff":release}
            engine = ResearchEngine(store, ScriptedModel(script), late_tools)
            first = engine.ask(sid, "What revenue was reported?")
            old = first["report"]
            self.assertEqual(old["status"], "complete")
            store.put_evidence(sid, {**FACT,"id":"early-record","published_at":early})
            ep = {"request":"Return to market close", "active_claim_ids":[], "issues":[]}
            store.update_scope(sid,{**SCOPE,"cutoff":early})
            before = engine._context(sid,ep,all_claims=True)
            self.assertEqual([r["id"] for r in before["evidence"]],["early-record"])
            self.assertEqual(before["reports"],[])
            self.assertEqual(before["claims"],[])
            self.assertEqual(store.get_report(sid,old["version"]),old)
            store.update_scope(sid,{**SCOPE,"cutoff":release})
            after = engine._context(sid,ep,all_claims=True)
            self.assertEqual({r["id"] for r in after["evidence"]},{"early-record", FACT["id"]})
            self.assertEqual([r["version"] for r in after["reports"]],[old["version"]])

    def test_old_calculations_and_indirect_claims_are_excluded_with_their_source(self):
        ctx = {"scope":{"cutoff":"2025-02-05T21:00:00Z"},
               "evidence":[{"id":"public","published_at":"2025-02-05T09:00:00-05:00"},
                           {"id":"later","published_at":"2025-02-05T16:01:00-05:00"}],
               "calculations":[{"id":"second","operand_ids":["first","public"]},
                               {"id":"first","operand_ids":["public","later"]}],
               "claims":[{"id":"ok","evidence_ids":["public"]},{"id":"derived","calculation_ids":["second"]}],
               "reports":[{"scope":{},"evidence":[{"id":"old-version","published_at":"2025-02-06"}]}]}
        before=json.dumps(ctx,sort_keys=True)
        result=as_of_context(ctx)
        self.assertEqual(result["calculations"],[])
        self.assertEqual(result["claims"],[ctx["claims"][0]])
        self.assertEqual(result["reports"],[])
        self.assertEqual(json.dumps(ctx,sort_keys=True),before)
