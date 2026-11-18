"""Controller and wire contract checks; model decisions here are fixtures, not accuracy data."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_engine import FixtureTools, ScriptedModel, full_script
from verifin.engine import ResearchEngine
from verifin.model import ModelClient, validated_output, OutputContractError
from verifin.skill_routing import catalog, role_sections, instructions
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry, TOOL_SPECS
from verifin.data import DataError

A, B = "trace-project-cash-rights", "reconcile-fee-bases"


class PlannerMethods(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = SessionStore(Path(temp.name)/"session.db")
        self.addCleanup(self.store.close)
        self.sid = self.store.create_session("routing")["id"]

    def test_packaged_registry_and_role_boundaries_on_both_wire_views(self):
        from verifin.context import planner_view
        registry = dict(catalog())
        from verifin.statement_analysis import ANALYZERS
        self.assertEqual(22, len(registry))
        self.assertLessEqual(set(ANALYZERS), registry.keys())
        analyzer = next(t for t in TOOL_SPECS if t["name"] == "analyze_disclosures")
        self.assertEqual(set(ANALYZERS), set(analyzer["parameters"]["properties"]["method"]["enum"]))
        self.assertTrue(all(set(role_sections(n)) == {"planner","researcher","critic"} and
                            all(role_sections(n).values()) for n in registry))
        client = ModelClient("unused","offline","https://example.invalid")
        for role in ("planner","researcher","critic"):
            ctx = {"tools":TOOL_SPECS,"episode":{"request":"本轮","research_skills":[A,B]},
                   "messages":[{"role":"user","content":"上一问"},{"role":"user","content":"本轮"}]}
            if role == "planner":
                ctx["skill_catalog"] = registry
            for wire in ([ctx, planner_view(ctx)] if role=="planner" else [ctx]):
                cached = client._cached_tool_input(role,wire)["input"]
                payload = json.dumps(cached,ensure_ascii=False)
                if role=="planner":
                    self.assertIn("skill_catalog",payload)
                    self.assertIn(registry["trace-lease-commencement"],payload)
                    self.assertIn("上一问",payload)
                else:
                    self.assertNotIn("skill_catalog",payload)
                direct = client._research_role(role,wire)
                self.assertIn({"role":"developer","content":direct},cached)
                for card in (A,B):
                    self.assertIn(instructions([card],role),direct)
                self.assertNotIn(role_sections("cash-flow-bridge")[role], direct)

    def test_real_controller_passes_selection_to_draft_and_review_then_resets(self):
        flow=full_script()
        flow[0]=(flow[0][0],dict(flow[0][1],research_skills=[A,B,A]))
        # A later decision cannot relabel a report drafted and checked under A and B.
        flow[-1]=("planner",dict(flow[-1][1],research_skills=[]))
        model=ScriptedModel(flow+[("planner",{"action":"clarify","question":"哪一次？","research_skills":[]})])
        engine=ResearchEngine(self.store,model,FixtureTools())
        with patch("verifin.skill_routing.select_skills",side_effect=AssertionError("legacy selector must not run")):
            first=engine.ask(self.sid,"MSFT revenue?")
            second=engine.ask(self.sid,"换个问题。下一次开会是哪天？")
        self.assertEqual(first["status"],"complete")
        self.assertEqual(first["report"]["research_skills"],[A,B])
        self.assertEqual(second["episode"]["research_skills"],[])
        self.assertEqual(second["episode"]["skill_selection_source"],"planner")
        for role,ctx in model.calls:
            self.assertEqual("skill_catalog" in ctx,role=="planner")
            if role in {"researcher","critic"}:
                self.assertEqual(ctx["episode"]["research_skills"],[A,B])
        follow=model.calls[-1][1]
        self.assertEqual(follow["episode"]["research_skills"],[])
        self.assertIn("MSFT revenue?",[m["content"] for m in follow["messages"]])
        self.assertTrue(follow["reports"][0]["claims"])

    def test_replacement_and_omission_are_distinct_from_no_selection(self):
        flow=full_script()
        flow[0]=("planner",dict(flow[0][1],research_skills=[A,B]))
        flow[1]=("planner",dict(flow[1][1],research_skills=[B]))
        model=ScriptedModel(flow)
        result=ResearchEngine(self.store,model,FixtureTools()).ask(self.sid,"Revenue?")
        self.assertEqual(result["report"]["research_skills"],[B])
        self.assertEqual(model.calls[2][1]["episode"]["research_skills"],[B])
        for decision in ({"action":"finish"},{"action":"finish","research_skills":[]}):
            other=self.store.create_session("optional")["id"]
            model=ScriptedModel([("planner",decision)])
            r=ResearchEngine(self.store,model,FixtureTools()).ask(other,"经销商库存？")
            self.assertEqual(r["report"]["research_skills"],[])
            self.assertEqual(r["report"]["skill_selection_source"], "planner" if "research_skills" in decision else "not_specified")

    def test_rejects_unknown_ids_and_preserves_named_tool_normalization(self):
        for invalid in ("one", ["not-a-skill"], [3], [{"id":A}]):
            with self.assertRaises(OutputContractError) as e:
                validated_output("planner",{"action":"finish","research_skills":invalid},{})
            self.assertEqual(e.exception.code,"invalid_research_skills")
        d=validated_output("planner",{"action":"discover_company","arguments":{"query":"ABC"},"research_skills":[A,B]}, {"tools":TOOL_SPECS})
        self.assertEqual((d["action"],d["tool"],d["research_skills"]),("tool","discover_company",[A,B]))

    def test_disclosure_analyzer_cannot_reselect_from_question(self):
        tools=ToolRegistry(self.store,Path(__file__).resolve().parents[1]/"data")
        scope={"entities":["MSFT"],"cutoff":"2025-09-01"}
        self.store.append_message(self.sid,"user","为什么利润没变成经营现金？")
        def save(names):
            self.store.add_event(self.sid,{"type":"episode_checkpoint","episode":{
                "status":"running","request":"为什么利润没变成经营现金？","research_skills":names,"skill_selection_source":"planner"}})
        with patch("verifin.statement_analysis.analyze",return_value={"ok":True}) as analyze:
            save([A])
            for args in ({},{"method":"cash-flow-bridge"}):
                with self.assertRaises(DataError):
                    tools._analyze_disclosures({"entity":"MSFT",**args},self.sid,scope)
            analyze.assert_not_called()
            save([A,"cash-flow-bridge"])
            tools._analyze_disclosures({"entity":"MSFT"},self.sid,scope)
            self.assertEqual(analyze.call_args.args[-1],["cash-flow-bridge"])

    def test_web_jobs_overlap_across_sessions_but_reject_duplicate_writer(self):
        import threading, time
        from verifin.engine import Budget
        from verifin.webapp import Application
        app=Application(self.store.path,Path(__file__).resolve().parents[1]/"data/replay")
        other=self.store.create_session("other")["id"]
        rendezvous=threading.Barrier(3,timeout=8)
        release=threading.Event()
        class WaitModel:
            def complete(self,role,ctx):
                assert role=="planner"
                rendezvous.wait()
                assert release.wait(8)
                return {"action":"finish","research_skills":[]}
        with patch("verifin.webapp.ModelClient.from_env",side_effect=lambda *, max_context_chars=None:WaitModel()):
            try:
                for sid in (self.sid,other):
                    app.start(sid,"查一下财报的发布时间。",Budget(max_model_calls=1))
                rendezvous.wait()  # both independent workers reached Planner before either returned
                with self.assertRaisesRegex(ValueError,"already running"):
                    app.start(self.sid,"改变这轮问题。",Budget())
            finally:
                release.set()
                deadline=time.monotonic()+8
                while any(app.jobs.values()) and time.monotonic()<deadline:
                    time.sleep(0.01)
        self.assertFalse(any(app.jobs.values()))
        self.assertFalse(app.errors)
        for sid in (self.sid,other):
            self.assertEqual(self.store.get_report(sid,1)["skill_selection_source"],"planner")
