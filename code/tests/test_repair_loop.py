"""Repeated critique/replanning protocol over real sources, scripted decisions.

This checks scheduling and provenance, not a real model's ability to find errors.
"""
import copy
from pathlib import Path
import tempfile
import unittest

from tests.critic_fixture import fixture_verdicts
from verifin.engine import ResearchEngine
from verifin.model import validate_response
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class TwoRepairFixture:
    mode = "scripted_fixture"
    timeout = 20
    last_usage = {}

    def __init__(self):
        self.step = 0
        self.drafts = 0
        self.calls = []

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        scope = {"entities": ["MSFT"], "period_start": "2024-10-01", "period_end": "2024-12-31",
                 "cutoff": "2025-01-31", "requirements": [{"id": "revenue", "text": "Revenue for MSFT FY2025 Q2, with exact dates and source"}]}
        if role == "planner":
            actions = ["wrong_period", "research", "correct_period", "research", "research", "finish"]
            action = actions[self.step]
            self.step += 1
            if action.endswith("period"):
                result = {"action": "tool", "tool": "financial_facts", "arguments": {"entity": "MSFT", "metric": "revenue"}}
                if action == "wrong_period":
                    result["scope"] = scope
                    result["arguments"].update(period_start="2024-07-01", period_end="2024-09-30")
            else:
                result = {"action": action}
        elif role == "researcher":
            self.drafts += 1
            expected_end = "2024-09-30" if self.drafts == 1 else "2024-12-31"
            fact = next(e for e in context["evidence"] if e["period_end"] == expected_end)
            text = f"MSFT revenue for 2024-10-01 through 2024-12-31 was USD {fact['value']:,}."
            if self.drafts == 2:
                text += " This was entirely caused by advertising."
            claim = {"id": "claim:revenue", "text": text, "evidence_ids": [fact["id"]],
                     "calculation_ids": [], "requirement_ids": ["revenue"],
                     "constraints": {"entity": "MSFT", "metric": "revenue", "unit": "USD",
                                     "period_start": scope["period_start"], "period_end": scope["period_end"]},
                     "asserted_value": fact["value"], "asserted_unit": "USD"}
            result = {"claims": [claim], "answered_requirement_ids": ["revenue"],
                      "unanswered_requirement_ids": [], "summary": "SCRIPTED FIXTURE"}
        elif role == "critic":
            issues = []
            if self.drafts < 3:
                issues = [{"target_id": "claim:revenue",
                           "issue_type": "source_time_mismatch" if self.drafts == 1 else "causal_overstatement",
                           "evidence_refs": context["claims"][0]["evidence_ids"],
                           "missing_support": "The citation uses the preceding quarter." if self.drafts == 1 else "No source establishes the proposed causal explanation.",
                           "proposed_action": "Fetch the requested quarter and revise." if self.drafts == 1 else "Remove the unsupported causal explanation while preserving the required revenue."}]
            result = {"issues": issues, "reviewed_claim_ids": ["claim:revenue"], "reviewed_requirement_ids": ["revenue"]}
        else:
            raise AssertionError(role)
        return validate_response(role, fixture_verdicts(role, result, context), context)


class RepairLoopTests(unittest.TestCase):
    def test_two_successive_feedback_cycles_preserve_required_answer_and_recheck(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SessionStore(Path(temporary) / "sessions.sqlite3")
            try:
                sid = store.create_session("Repeated correction protocol")['id']
                model = TwoRepairFixture()
                data = Path(__file__).resolve().parents[1] / "data" / "replay"
                result = ResearchEngine(store, model, ToolRegistry(store, data)).ask(sid, "MSFT FY2025 Q2 revenue with dates and source.")
                self.assertEqual("complete", result["status"], result["episode"])
                self.assertEqual(12, result["episode"]["model_calls"])
                self.assertEqual(2, result["episode"]["tool_calls"])
                checks = [event for event in store.events(sid) if event["type"] == "verification"]
                self.assertEqual(3, len(checks))
                self.assertTrue(checks[0]["issues"])
                self.assertTrue(any(issue["issue_type"] == "causal_overstatement" for issue in checks[1]["issues"]))
                self.assertEqual([], checks[2]["issues"])
                self.assertEqual(69_632_000_000, result["report"]["claims"][0]["asserted_value"])
                self.assertNotIn("caused", result["report"]["claims"][0]["text"])
                self.assertEqual([], result["report"]["unanswered"])
                self.assertEqual("scripted_fixture", result["report"]["model_mode"])
                history = store.history(sid, "claim", "claim:revenue")
                self.assertTrue(any("caused" in entry["text"] for entry in history))
                planner_contexts = [ctx for role, ctx in model.calls if role == "planner"]
                self.assertTrue(any(any(i["issue_type"] == "causal_overstatement" for i in ctx["issues"]) for ctx in planner_contexts))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
