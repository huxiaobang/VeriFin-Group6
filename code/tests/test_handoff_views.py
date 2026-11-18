import unittest

from verifin.context import as_of_context, history_report_views
from verifin.reporting import question_answers, render_markdown


class HandoffViews(unittest.TestCase):
    def test_critic_sees_only_the_instruction_used_for_its_own_draft(self):
        from verifin.engine import ResearchEngine
        from verifin.storage import SessionStore
        from tests.test_engine import CLAIM, RESEARCH, REVIEW, FixtureTools, ScriptedModel, full_script
        flow = full_script()
        flow[1] = ("planner", {"action":"research", "reason":"First draft direction"})
        issue = {"target_id":CLAIM["id"], "issue_type":"omission", "evidence_refs":[],
                 "missing_support":"Explain the basis.", "proposed_action":"Clarify the basis."}
        flow[3] = ("critic", {**REVIEW,"issues":[issue]})
        flow[4:4] = [("planner",{"action":"research"}), ("researcher",RESEARCH), ("critic",REVIEW)]
        with SessionStore(":memory:") as store:
            sid = store.create_session()["id"]
            roles = ScriptedModel(flow)
            result = ResearchEngine(store,roles,FixtureTools()).ask(sid,"What was revenue?")
            self.assertEqual(result["status"], "complete")
        for role in ("researcher","critic"):
            self.assertEqual([ctx.get("draft_instruction") for r,ctx in roles.calls if r == role],
                             ["First draft direction",None])

    def test_questions_with_a_shared_quarter_reject_answers_from_other_fiscal_dates(self):
        dates = {"entity":"Issuer","period_start":"2024-10-01","period_end":"2024-12-31"}
        amounts = {"id":"amount", "text":"2025Q2 sales increased 2%.", "status":"Supported","constraints":dates}
        demand = {"id":"demand", "text":"Demand remained mixed.", "status":"Supported","constraints":dates}
        parts = {"part-1":"Issuer 2025Q1, how did sales change?", "part-2":"Did demand broaden?"}
        coverage = [{"part":part,"status":"answered","claim_id":c["id"],"answer_sentence":c["text"]}
                    for part,c in zip(parts,(amounts,demand))]
        report = {"claims":[amounts,demand],"request_parts":parts,"request_coverage":coverage,"verification_available":True}
        self.assertEqual([a["status"] for a in question_answers(report)],["gap","gap"])
        matching = {**report,"request_parts":{**parts,"part-1":parts["part-1"].replace("2025Q1","2025Q2")}}
        self.assertEqual([a["status"] for a in question_answers(matching)],["answered","answered"])
        # Other issuers and independently requested periods cannot inherit this quarter binding.
        other = {**demand,"constraints":{**dates,"entity":"Other issuer"}}
        self.assertEqual(question_answers({**report,"claims":[amounts,other]})[1]["status"],"answered")
        explicit = {**report,"request_parts":{**parts,"part-2":"Did demand broaden in 2025Q2?"}}
        self.assertEqual(question_answers(explicit)[1]["status"],"answered")

    def test_new_quarter_drops_inherited_dates_but_preserves_explicit_date_edits(self):
        from verifin.engine import ResearchEngine
        from verifin.storage import SessionStore
        from tests.test_engine import FixtureTools, ScriptedModel, full_script
        for edited in (False,True):
            with self.subTest(edited=edited), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                first = ResearchEngine(store,ScriptedModel(full_script()),FixtureTools()).ask(sid,"What was MSFT revenue?")
                self.assertEqual(first["status"],"complete")
                if edited:
                    store.update_scope(sid,{**first["report"]["scope"],"period_start":"2024-01-01","period_end":"2024-03-31"})
                roles = ScriptedModel([("planner",{"action":"clarify","question":"Confirm the fiscal dates?"})])
                ResearchEngine(store,roles,FixtureTools()).ask(sid,"MSFT 2024Q3 的营收是多少？")
                _, context = roles.calls[0]
                self.assertEqual(context["episode"]["skill_fiscal_quarter"]["quarter"],"2024Q3")
                self.assertEqual(context["scope"].get("period_end"),"2024-03-31" if edited else None)

    def test_only_source_notes_with_current_record_versions_survive_in_history(self):
        original = {"id":"cell", "version":1, "published_at":"2025-01-17"}
        report = {"scope":{"cutoff":"2025-01-18"}, "evidence":[original], "claims":[], "calculations":[],
                  "skill_notes":[{"text":"An archive excerpt", "evidence_ids":["cell"], "calculation_ids":[]}],
                  "skill_gap":"Only this excerpt was available.", "verification_available":False, "model_mode":"offline_tools_only"}
        ctx = {"scope":report["scope"], "reports":[report], "calculations":[], "claims":[], "evidence":[original]}
        self.assertEqual(history_report_views(as_of_context(ctx))[0]["skill_notes"], report["skill_notes"])
        for replacement in (dict(original, version=2), dict(original, stale=True)):
            view, = history_report_views(as_of_context(dict(ctx, evidence=[replacement])))
            self.assertEqual(view["skill_notes"], [])
            self.assertNotIn("skill_gap", view)
        self.assertEqual(history_report_views(as_of_context(dict(ctx, scope={"cutoff":"2025-01-01"}))), [])

    def test_export_only_calls_a_part_answered_when_its_reviewed_sentence_exists(self):
        claim = {"id":"c", "text":"Sales grew.", "status":"Supported"}
        parts = {"part-1":"How did sales change?"}
        coverage = [{"part":"part-1","claim_id":"c","answer_sentence":claim["text"],"status":"answered"}]
        report = {"claims":[claim], "request_parts":parts, "request_coverage":coverage, "verification_available":True}
        self.assertEqual(question_answers(report)[0]["status"], "answered")
        self.assertIn("How did sales change?",render_markdown(report))
        for invalid in (dict(report,verification_available=False), dict(report,claims=[]),
                        dict(report,claims=[dict(claim,text="New draft.")]), dict(report,claims=[dict(claim,stale=True)])):
            self.assertNotEqual(question_answers(invalid)[0]["status"],"answered")

    def test_tool_diagnostic_survives_markdown_export(self):
        report = {"status":"partial", "stop_reason":"tool_fault_requires_inspection",
                  "tool_fault":{"tool":"calculate","reason":"Use two numeric cells, with matching periods."}}
        self.assertIn("calculate: Use two numeric cells, with matching periods.", render_markdown(report))
