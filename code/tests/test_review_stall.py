import tempfile
import unittest
from pathlib import Path

from tests.test_engine import FACT, RESEARCH, REVIEW, FixtureTools, ScriptedModel, full_script
from verifin.engine import ResearchEngine, record_review, repeated_redraft
from verifin.storage import SessionStore


def rotating_reviews():
    script = full_script()[:1]
    for category in ("missing_provenance", "omission", "unsupported_statement"):
        issue = {"target_id":"req:revenue", "issue_type":category, "evidence_refs":[FACT["id"]],
                 "missing_support":"A separate filing-body citation is still unavailable.",
                 "proposed_action":"Retrieve the filing body or disclose the source limitation."}
        script += [("planner", {"action":"research"}), ("researcher", RESEARCH),
                   ("critic", {**REVIEW, "issues":[issue]})]
    return script


class ReviewStallTests(unittest.TestCase):
    def run_episode(self, script, tools):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "test.sqlite3")
            try:
                sid = store.create_session("Offline")["id"]
                model = ScriptedModel(script)
                result = ResearchEngine(store, model, tools, finish_after_review=True).ask(sid, "Revenue?")
                self.assertFalse(model.script)
                return result["report"], [role for role, _ in model.calls]
            finally:
                store.close()

    def test_changing_labels_does_not_buy_unlimited_revisions(self):
        report, roles = self.run_episode(rotating_reviews() + [("planner", {"action":"research"})], FixtureTools())
        self.assertEqual(report["stop_reason"], "repeated_review_without_progress")
        self.assertEqual(roles.count("researcher"), 3)
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["verification_available"])
        self.assertEqual(report["claims"][0]["status"], "Supported")
        self.assertIn("filing-body", report["issues"][0]["missing_support"])

    def test_genuinely_new_source_permits_another_draft(self):
        tools = FixtureTools()
        def new_source():
            if len(tools.calls) == 2:
                tools.result = {**tools.result, "evidence":[{**FACT, "id":"fact:filing-body"}]}
        tools.callback = new_source
        extra = [("planner", {"action":"tool", "tool":"financial_facts", "arguments":{"source":"filing"}}),
                 ("planner", {"action":"research"}), ("researcher", RESEARCH), ("critic", REVIEW)]
        report, roles = self.run_episode(rotating_reviews() + extra, tools)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(roles.count("researcher"), 4)
        self.assertEqual(len(tools.calls), 2)

    def test_coverage_progress_resets_span_but_regaining_the_same_part_does_not(self):
        episode = {"reviewed":True, "tool_calls":1, "no_progress":0, "reviewed_claim_ids":["claim:amount"]}
        context = {"session":{"scope_version":1}, "evidence":[FACT], "calculations":[],
                   "request_parts":{"part-1":"Report the amount."}}
        for index, covered in enumerate((False, False, True, False, True)):
            episode["issues"] = [{"target_id":f"other{index}", "issue_type":"omission"}]
            episode["request_coverage"] = ([{"part":"part-1", "status":"answered", "claim_id":"claim:amount"}]
                                           if covered else [])
            record_review(episode, context, "")
            self.assertEqual(repeated_redraft(episode, 3), index == 4)
