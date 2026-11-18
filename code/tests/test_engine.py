"""Controller/integration tests. All model responses below are explicit fixtures."""
import json
from pathlib import Path
import tempfile
import unittest

from tests.critic_fixture import fixture_verdicts
from verifin.engine import Budget, ResearchEngine, session_lock
from verifin.model import ModelError, validate_response
from verifin.reporting import export_report, render_csv, render_markdown
from verifin.storage import SessionStore


SCOPE = {"entities": ["MSFT"], "period_start": "2024-10-01", "period_end": "2024-12-31", "cutoff": "2025-01-31", "requirements": [{"id": "req:revenue", "text": "Report revenue for the requested quarter"}]}
FACT = {"id": "fact:revenue", "kind": "financial_fact", "entity": "MSFT", "metric": "revenue", "value": 69632000000, "unit": "USD", "period_start": "2024-10-01", "period_end": "2024-12-31", "published_at": "2025-01-29T21:11:54Z", "source_url": "https://data.sec.gov/example", "locator": "facts.us-gaap.Revenue", "snapshot_hash": "a" * 64}
CLAIM = {"id": "claim:revenue", "text": "Quarterly revenue was USD 69.632 billion.", "evidence_ids": [FACT["id"]], "calculation_ids": [], "requirement_ids": ["req:revenue"], "constraints": {"entity": "MSFT", "period_start": "2024-10-01", "period_end": "2024-12-31", "metric": "revenue", "unit": "USD"}, "asserted_value": 69632000000, "asserted_unit": "USD"}
RESEARCH = {"claims": [CLAIM], "answered_requirement_ids": ["req:revenue"], "unanswered_requirement_ids": [], "summary": "Quarterly revenue was USD 69.632 billion."}
REVIEW = {"issues": [], "reviewed_claim_ids": ["claim:revenue"], "reviewed_requirement_ids": ["req:revenue"]}


class ScriptedModel:
    """Only the tests instantiate this class. It is not a production fallback."""
    mode = "scripted_fixture"
    timeout = 20

    def __init__(self, script, callback=None):
        self.script = list(script)
        self.calls = []
        self.last_usage = {}
        self.callback = callback

    def complete(self, role, context):
        self.calls.append((role, context))
        self.last_usage = {"total_tokens": 10}
        if self.callback:
            self.callback(role, context)
        expected_role, result = self.script.pop(0)
        if role != expected_role:
            raise AssertionError((role, expected_role))
        if isinstance(result, Exception):
            raise result
        return validate_response(role, fixture_verdicts(role, json.loads(json.dumps(result)), context), context)


class FixtureTools:
    def __init__(self, result=None, callback=None):
        self.result = result or {"ok": True, "summary": "fixture fact", "evidence": [FACT], "calculations": [], "data": {}}
        self.calls = []
        self.callback = callback

    def list_tools(self):
        return [{"name": "financial_facts", "description": "TEST fixture", "parameters": {"type": "object"}}]

    def execute(self, name, arguments, session_id, scope):
        self.calls.append((name, arguments))
        if self.callback:
            self.callback()
        return json.loads(json.dumps(self.result))


def full_script():
    return [("planner", {"action": "tool", "tool": "financial_facts", "arguments": {"entity": "MSFT"}, "scope": SCOPE}),
            ("planner", {"action": "research"}), ("researcher", RESEARCH), ("critic", REVIEW), ("planner", {"action": "finish"})]


def saved_source_followup(question):
    source_claim = {**CLAIM, "id":"claim:source", "text":"The source is the filing's facts.us-gaap.Revenue record.",
                    "requirement_ids":["req:source"]}
    return [("planner", {"action":"research", "requirements":[{"id":"req:source","text":question}]}),
            ("researcher", {"claims":[source_claim], "summary":source_claim["text"],
                            "answered_requirement_ids":["req:source"], "unanswered_requirement_ids":[]}),
            ("critic", {"reviewed_claim_ids":["claim:source"],"reviewed_requirement_ids":["req:source"],"issues":[],
                        "request_coverage":[{"part":"part-1","status":"answered","claim_id":"claim:source",
                                             "answer_sentence":source_claim["text"]}]}),
            ("planner", {"action":"finish"})]


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sessions.sqlite3"
        self.store = SessionStore(self.path)
        self.sid = self.store.create_session("Test session")["id"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_complete_episode_requires_claims_review_and_coverage(self):
        model = ScriptedModel(full_script())
        result = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Revenue for MSFT fiscal Q2 2025?")
        self.assertEqual("complete", result["status"], result)
        self.assertEqual("Supported", result["report"]["claims"][0]["status"])
        self.assertEqual(5, result["episode"]["model_calls"])
        self.assertEqual(1, result["episode"]["tool_calls"])
        self.assertEqual(50, result["episode"]["usage"]["total_tokens"])
        from verifin.engine import digest
        event = next(e for e in self.store.events(self.sid) if e["type"] == "tool_call")
        self.assertEqual(FACT["value"], event["observation"]["evidence"][0]["value"])
        self.assertEqual(digest(event["observation"]), event["observation_sha256"])

    def test_last_permitted_call_can_complete_a_fully_reviewed_report(self):
        model = ScriptedModel(full_script()[:-1])
        result = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Revenue?", budget=Budget(max_model_calls=4))
        self.assertEqual(result["report"]["status"], "complete")
        self.assertEqual(result["report"]["issues"], [])
        self.assertEqual(result["episode"]["model_calls"], 4)
        self.assertEqual(len(model.calls), 4)

    def test_isolated_researcher_claim_leaves_visible_gap_but_other_claim_reaches_critic(self):
        bad = dict(CLAIM, id="claim:bad", evidence_ids=[FACT["id"], "source_doc_unknown"],
                   text="A claim relying on an uninspected document.")
        draft = dict(RESEARCH, claims=[bad, CLAIM], summary=bad["text"])
        script = full_script()
        script[2] = ("researcher", draft)
        model = ScriptedModel(script)
        report = ResearchEngine(self.store, model, FixtureTools(), finish_after_review=True).ask(self.sid, "Revenue?")["report"]
        ctx = next(ctx for role, ctx in model.calls if role == "critic")
        self.assertEqual([c["id"] for c in ctx["claims"]], [CLAIM["id"]])
        self.assertEqual([(c["id"], c["status"]) for c in report["claims"]], [(CLAIM["id"], "Supported")])
        self.assertEqual(report["status"], "partial")  # Both claims target the same requirement.
        problem = next(i for i in report["issues"] if i["target_id"] == bad["id"])
        self.assertEqual(problem["evidence_refs"], [])
        self.assertIn(bad["text"], render_markdown(report))
        self.assertNotIn(bad["text"], report["summary"])
        saved = self.store.latest_event(self.sid, "researcher_claims_quarantined")
        self.assertEqual(saved["id"], problem["contract_diagnostic"]["event_id"])
        self.assertEqual(saved["claims"][0]["claim"], bad)
        self.assertEqual(saved["summary"], draft["summary"])
        self.assertEqual(self.store.history(self.sid, "claim", bad["id"]), [])

    def test_all_invalid_revision_cannot_restore_an_old_adjudication(self):
        bad = dict(CLAIM, evidence_ids=["source_doc_unknown"], text="Replacement with a missing citation.")
        script = full_script()[:-1] + [("planner", {"action":"research", "reason":"Revise the source."}),
                                        ("researcher", dict(RESEARCH, claims=[bad], summary=bad["text"]))]
        model = ScriptedModel(script)
        engine = ResearchEngine(self.store, model, FixtureTools())
        report = engine.ask(self.sid, "Revenue?")["report"]
        self.assertEqual(report["stop_reason"], "researcher_no_valid_references")
        self.assertEqual(report["claims"], [])
        self.assertFalse(report["verification_available"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual([r["id"] for r in report["unanswered"]], ["req:revenue"])
        self.assertEqual([r for r, _ in model.calls].count("critic"), 1)
        self.assertTrue(self.store.history(self.sid, "claim", CLAIM["id"]))
        self.assertFalse(any(e["type"] == "reviewed_draft_restored" for e in self.store.events(self.sid)))
        # The next user request uses the same session; its clean draft may complete.
        fixed = ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Return to the revenue amount.")["report"]
        self.assertEqual(fixed["status"], "complete")
        self.assertFalse(any(i.get("contract_diagnostic") for i in fixed["issues"]))

    def test_verified_handoff_delivers_the_same_findings_without_tail_call(self):
        from verifin.cli import parser
        args = parser().parse_args(["ask", self.sid, "Revenue?", "--finish-after-review"])
        model = ScriptedModel(full_script()[:-1])
        actual = ResearchEngine(self.store, model, FixtureTools(), finish_after_review=args.finish_after_review)
        result = actual.ask(self.sid, "Revenue?")
        self.assertEqual([role for role, _ in model.calls], ["planner", "planner", "researcher", "critic"])
        self.assertEqual(result["report"]["status"], "complete")
        self.assertTrue(result["report"]["verification_available"])
        routes = [e for e in self.store.events(self.sid) if e["type"] == "controller_route"]
        self.assertEqual([e["reason"] for e in routes], ["all_claims_and_requirements_verified"])
        self.assertEqual(self.store.get_report(self.sid, 1), result["report"])

    def test_verified_handoff_returns_to_planner_while_a_repair_is_required(self):
        issue = {"target_id":"req:revenue","issue_type":"omission","evidence_refs":[],
                 "missing_support":"Verify the figure.","proposed_action":"Recheck the source."}
        model = ScriptedModel([*full_script()[:3], ("critic",{**REVIEW,"issues":[issue]}),
                               ("planner",{"action":"research","reason":"Recheck the disclosed figure."}),
                               ("researcher",RESEARCH), ("critic",REVIEW)])
        result = ResearchEngine(self.store,model,FixtureTools(),finish_after_review=True).ask(self.sid,"Revenue?")
        self.assertEqual(result["report"]["status"], "complete")
        self.assertEqual(len(model.calls), 7)
        reviews = [e for e in self.store.events(self.sid) if e["type"] == "verification"]
        self.assertEqual([len(e["issues"]) for e in reviews], [1, 0])

    def test_repeated_omission_is_visible_and_last_review_survives_budget(self):
        issue = {"target_id": "req:revenue", "issue_type": "omission", "evidence_refs": [],
                 "missing_support": "The requested comparison is missing.", "proposed_action": "Calculate the gap."}
        review = ("critic", {**REVIEW, "issues": [issue]})
        script = [*full_script()[:3], review,
                  ("planner", {"action": "research", "reason": "Add the missing difference."}),
                  ("researcher", RESEARCH), ("critic", {**REVIEW, "issues": [
                      {**issue, "missing_support": "The difference still needs a value."}]}),
                  ("planner", {"action": "research", "reason": "Try rewriting again."})]
        model = ScriptedModel(script)
        result = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Compare", budget=Budget(max_model_calls=9))
        self.assertTrue(result["report"]["verification_available"])
        self.assertEqual(result["episode"]["stop_reason"], "model_budget_exhausted")
        self.assertEqual(len(model.calls), 8)
        self.assertNotIn("repair_feedback", model.calls[0][1])
        drafting = model.calls[5][1]["repair_feedback"]
        self.assertEqual(drafting["current_draft_instruction"], "Add the missing difference.")
        self.assertEqual(drafting["last_tool_in_this_turn"]["tool"], "financial_facts")
        planner = model.calls[-1][1]["repair_feedback"]
        self.assertEqual(planner["previous_draft_instruction"], "Add the missing difference.")
        self.assertEqual(planner["tool_calls_since_review"], 0)
        self.assertEqual(planner["targets_and_consecutive_reviews_on_same_sources"],
                         [{"target_id":"req:revenue", "issue_type":"omission", "reviews":2}])

    def test_reopened_followup_reuses_saved_evidence_without_tool_call(self):
        ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Revenue?")
        self.store.close()
        self.store = SessionStore(self.path)
        tools = FixtureTools()
        question = "Show the source for that number"
        model = ScriptedModel(saved_source_followup(question))
        result = ResearchEngine(self.store, model, tools).ask(self.sid, question)
        self.assertEqual("complete", result["status"], result)
        self.assertEqual([], tools.calls)
        self.assertEqual(2, len(self.store.list_reports(self.sid)))
        self.assertTrue(any(m["content"] == "Revenue?" for m in model.calls[0][1]["messages"]))

    def test_new_request_supplies_default_before_discovery_and_survives_empty_lists(self):
        ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Revenue?")
        question = "Where is the source?"
        class StopAfterDraftInput:
            def __init__(self):
                self.inputs = []
            def complete(self, role, context):
                self.inputs.append((role, context["requirements"]))
                if role == "researcher":
                    raise ModelError("intentionally stop after recording the draft")
                if len(self.inputs) == 1:
                    return {"action":"tool", "tool":"financial_facts","arguments":{"entity":"MSFT"},"requirements":[]}
                return {"action":"research","scope":{"requirements":[]}}
        model = StopAfterDraftInput()
        ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, question)
        self.assertEqual([role for role, _ in model.inputs], ["planner","planner","researcher"])
        self.assertTrue(all([r["text"] for r in items] == [question] for _, items in model.inputs))

    def test_explicit_repeat_and_manually_edited_requirements(self):
        ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Revenue?")
        repeated = ScriptedModel([("planner",{"action":"research"}),("researcher",RESEARCH),("critic",REVIEW),("planner",{"action":"finish"})])
        report = ResearchEngine(self.store, repeated, FixtureTools()).ask(self.sid,"再回答一次")["report"]
        self.assertEqual(report["status"],"complete")
        self.assertEqual(repeated.calls[0][1]["requirements"], SCOPE["requirements"])
        edited = [{"id":"user-specified","text":"Use only the user's updated requirements"}]
        self.store.update_scope(self.sid,{**SCOPE,"requirements":edited})
        model = ScriptedModel([("planner", ModelError("capture"))])
        ResearchEngine(self.store, model, FixtureTools()).ask(self.sid,"Check the filing")
        self.assertEqual(model.calls[0][1]["requirements"],edited)

    def test_clarification_preserves_budget_and_excludes_wait(self):
        clock = [0.0]
        def tick(role, context):
            clock[0] += 1
        model = ScriptedModel([("planner", {"action": "clarify", "question": "Which period?", "scope": SCOPE}), ("planner", {"action": "research"}), ("researcher", RESEARCH)], tick)
        engine = ResearchEngine(self.store, model, FixtureTools(), clock=lambda: clock[0])
        first = engine.ask(self.sid, "Revenue?", budget=Budget(3, 10, 10))
        self.assertEqual("waiting_for_clarification", first["status"])
        self.store.put_evidence(self.sid, FACT)
        clock[0] += 10000
        second = engine.ask(self.sid, "The December quarter", budget=Budget(100, 100, 1000))
        self.assertEqual(first["episode"]["id"], second["episode"]["id"])
        self.assertEqual(3, second["episode"]["model_calls"])
        self.assertEqual("model_budget_exhausted", second["episode"]["stop_reason"])
        self.assertEqual(3, second["episode"]["active_seconds"])
        self.assertFalse(second["report"]["verification_available"])

    def test_failed_model_call_counts_and_does_not_expose_secret(self):
        model = ScriptedModel([("planner", ModelError("secret-key-do-not-display"))])
        result = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Request")
        self.assertEqual(1, result["episode"]["model_calls"])
        self.assertEqual("partial", result["status"])
        self.assertNotIn("secret-key", json.dumps(result) + json.dumps(self.store.events(self.sid)))

    def test_repeated_failed_tools_stop_without_unbounded_retry(self):
        action = ("planner", {"action": "tool", "tool": "financial_facts", "arguments": {}, "scope": SCOPE})
        tools = FixtureTools({"ok": False, "summary": "unavailable", "error": "not covered"})
        result = ResearchEngine(self.store, ScriptedModel([action] * 4), tools).ask(self.sid, "Request", budget=Budget(max_no_progress=3))
        self.assertEqual("no_progress", result["episode"]["stop_reason"])
        self.assertEqual(3, result["episode"]["tool_calls"])

    def test_completion_rejected_without_review_or_required_coverage(self):
        result = ResearchEngine(self.store, ScriptedModel([("planner", {"action": "finish", "scope": SCOPE})]), FixtureTools()).ask(self.sid, "Request")
        self.assertEqual("partial", result["status"])
        self.assertEqual(["req:revenue"], [r["id"] for r in result["report"]["unanswered"]])

    def test_finish_after_lookup_without_candidate_routes_to_budgeted_review(self):
        script=full_script()
        script[1]=("planner",{"action":"finish","reason":"I have the answer already"})
        model=ScriptedModel(script)
        result=ResearchEngine(self.store,model,FixtureTools()).ask(self.sid,"Find the disclosed revenue")
        self.assertEqual("complete",result["status"])
        self.assertEqual(["planner","planner","researcher","critic","planner"],[r for r,c in model.calls])
        self.assertEqual(len(model.calls),result["episode"]["model_calls"])
        self.assertTrue(any(e.get("event",e).get("type")=="controller_route" for e in self.store.events(self.sid)))

    def test_refetch_timestamp_alone_does_not_reset_no_progress_budget(self):
        tools = FixtureTools()
        def new_timestamp():
            tools.result["data"] = {"retrieved_at": f"2026-10-13T03:00:0{len(tools.calls)}Z", "network_requests": 1}
        tools.callback = new_timestamp
        action = ("planner", {"action": "tool", "tool": "financial_facts", "arguments": {}, "scope": SCOPE})
        result = ResearchEngine(self.store, ScriptedModel([action] * 4), tools).ask(self.sid, "Revenue?", budget=Budget(max_no_progress=2))
        self.assertEqual("no_progress", result["episode"]["stop_reason"])
        self.assertEqual(3, result["episode"]["tool_calls"])

    def test_missing_content_targets_requirement_without_existing_claim(self):
        script = [("planner", {"action": "research", "scope": SCOPE}),
                  ("researcher", {"claims": [], "answered_requirement_ids": [], "unanswered_requirement_ids": ["req:revenue"], "summary": "Unavailable"}),
                  ("critic", {"issues": [{"target_id": "req:revenue", "issue_type": "omission", "evidence_refs": [], "missing_support": "Revenue is unavailable", "proposed_action": "Try another source"}], "reviewed_claim_ids": [], "reviewed_requirement_ids": ["req:revenue"]}),
                  ("planner", {"action": "finish"})]
        result = ResearchEngine(self.store, ScriptedModel(script), FixtureTools()).ask(self.sid, "Request")
        self.assertEqual("partial", result["status"])
        self.assertTrue(any(i["target_id"] == "req:revenue" for i in result["report"]["issues"]))

    def test_cancellation_during_tool_stops_further_model_calls(self):
        tools = FixtureTools(callback=lambda: self.store.request_cancel(self.sid))
        model = ScriptedModel(full_script())
        result = ResearchEngine(self.store, model, tools).ask(self.sid, "Request")
        self.assertEqual("cancelled", result["episode"]["stop_reason"])
        self.assertEqual(1, len(model.calls))
        self.assertEqual("partial", result["status"])

    def test_time_limit_cannot_be_evaded_by_successful_result(self):
        clock = [0.0]
        model = ScriptedModel(full_script(), lambda role, ctx: clock.__setitem__(0, clock[0] + 5))
        result = ResearchEngine(self.store, model, FixtureTools(), clock=lambda: clock[0]).ask(self.sid, "Request", budget=Budget(max_seconds=2))
        self.assertEqual("time_budget_exhausted", result["episode"]["stop_reason"])
        self.assertEqual(0, result["episode"]["tool_calls"])

    def test_run_lock_excludes_other_engine_without_blocking_cancel(self):
        with session_lock(self.store, self.sid):
            other = SessionStore(self.path)
            try:
                with self.assertRaisesRegex(ValueError, "already running"):
                    ResearchEngine(other, ScriptedModel([]), FixtureTools()).ask(self.sid, "Request")
                other.request_cancel(self.sid)
                self.assertTrue(self.store.is_cancelled(self.sid))
            finally:
                other.close()

    def test_export_retains_provenance_and_does_not_run_spreadsheet_formula(self):
        result = ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Request")
        paths = export_report(result["report"], Path(self.temp.name) / "export")
        self.assertEqual(4, len(paths))
        report_text = Path(paths[0]).read_text()
        self.assertIn(FACT["snapshot_hash"], report_text)
        self.assertIn("Supported", report_text)
        self.assertIn("'=HYPERLINK", render_csv([{"text": '=HYPERLINK("example")'}]))

    def test_internal_replanning_cannot_delete_unanswered_requirement(self):
        scope = {**SCOPE, "requirements": SCOPE["requirements"] + [{"id": "req:margin", "text": "Report operating margin"}]}
        script = [("planner", {"action": "tool", "tool": "financial_facts", "arguments": {}, "scope": scope}),
                  ("planner", {"action": "research", "requirements": SCOPE["requirements"]})]
        result = ResearchEngine(self.store, ScriptedModel(script), FixtureTools()).ask(self.sid, "Revenue and margin?")
        self.assertEqual("partial", result["status"])
        self.assertEqual("execution_error", result["episode"]["stop_reason"])
        self.assertEqual(2, len(result["report"]["scope"]["requirements"]))
        self.assertEqual(2, len(result["report"]["unanswered"]))

    def test_cancel_before_worker_entry_is_not_cleared(self):
        self.store.request_cancel(self.sid)
        model = ScriptedModel([])
        result = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Request", reset_cancel=False)
        self.assertEqual("cancelled", result["episode"]["stop_reason"])
        self.assertEqual([], model.calls)

    def test_new_user_task_can_resolve_checklist_after_preliminary_lookup(self):
        previous_scope = {**SCOPE, "requirements": SCOPE["requirements"] + [{"id": "req:margin", "text": "Old task also asked for margin"}]}
        self.store.update_scope(self.sid, previous_scope)
        script = [("planner", {"action": "tool", "tool": "financial_facts", "arguments": {}}),
                  ("planner", {"action": "research", "requirements": SCOPE["requirements"]}),
                  ("researcher", RESEARCH), ("critic", REVIEW), ("planner", {"action": "finish"})]
        result = ResearchEngine(self.store, ScriptedModel(script), FixtureTools()).ask(self.sid, "For this new request, only give revenue, without the old margin task.")
        self.assertEqual("complete", result["status"], result["episode"])
        self.assertEqual(SCOPE["requirements"], result["report"]["scope"]["requirements"])

    def test_document_refresh_without_query_hits_invalidates_prior_review(self):
        observation = {"source_document_id": "filing:test", "source_revision": "a" * 64,
                       "source_observed_at": "2026-10-13T01:00:00Z", "source_revision_authoritative": True,
                       "source_url": FACT["source_url"]}
        document = {**FACT, "kind": "document", "text": "Revenue disclosure.", **observation}
        tools = FixtureTools({"ok": True, "summary": "fixture filing passage", "evidence": [document], "calculations": [], "data": {"source_observation": observation}})
        def refresh_without_hits():
            if len(tools.calls) == 2:
                tools.result = {"ok": False, "summary": "Refreshed document has no query hits", "evidence": [], "calculations": [],
                                "data": {"source_observation": {**observation, "source_revision": "b" * 64, "source_observed_at": "2026-10-13T02:00:00Z"}}}
        tools.callback = refresh_without_hits
        script = full_script()[:-1] + [("planner", {"action": "tool", "tool": "financial_facts", "arguments": {}}), ("planner", {"action": "finish"})]
        result = ResearchEngine(self.store, ScriptedModel(script), tools).ask(self.sid, "Revenue, checking for source corrections.")
        self.assertEqual("partial", result["status"], result["episode"])
        self.assertFalse(result["report"]["verification_available"])
        self.assertEqual("Pending Verification", result["report"]["claims"][0]["status"])
        self.assertTrue(result["report"]["claims"][0]["stale"])
        self.assertTrue(result["report"]["unanswered"])

    def test_interrupted_checkpoint_is_preserved_as_partial_before_new_work(self):
        engine = ResearchEngine(self.store, ScriptedModel([("planner", {"action": "finish"})]), FixtureTools())
        episode = {"id": "crashed", "status": "running", "request": "Earlier", "budget": {}, "model_calls": 1, "tool_calls": 0, "active_seconds": 1, "usage": {}, "active_claim_ids": [], "issues": [], "reviewed": False, "answered_requirement_ids": [], "unanswered_requirement_ids": [], "summary": ""}
        self.store.add_event(self.sid, {"type": "episode_checkpoint", "episode": episode})
        engine.ask(self.sid, "Continue research")
        reports = self.store.list_reports(self.sid)
        self.assertEqual(2, len(reports))
        self.assertEqual("interrupted", reports[0]["stop_reason"])
        self.assertEqual("partial", reports[0]["status"])

    def test_real_snapshot_tools_roundtrip_and_followup(self):
        from scripts.demo_replay import run_demo
        result = run_demo(Path(self.temp.name) / "replay")
        self.assertEqual(0, result["live_model_calls"])
        self.assertEqual(69632000000, result["source_values"]["revenue"])
        self.assertAlmostEqual(45.45754825367647, result["operating_margin_percent"])
        self.assertEqual(0, result["followup_tool_calls"])
        self.assertEqual(2, result["saved_report_versions"])
        self.assertIn("SCRIPTED FIXTURE", Path(result["exports"][0]).read_text())


if __name__ == "__main__":
    unittest.main()
