"""Exercise Critic response recovery at the model/engine boundary, without a provider."""
from http.client import IncompleteRead
import json
import unittest

from tests.test_engine import CLAIM, FACT, FixtureTools, ScriptedModel, full_script
from verifin.engine import Budget, ResearchEngine
from verifin.model import _completed_role_output
from verifin.storage import SessionStore


class BrokenReceiptModel(ScriptedModel):
    def __init__(self, defects):
        super().__init__(full_script())
        self.defects = list(defects)

    def complete(self, role, context):
        if role != "critic" or not self.defects:
            return super().complete(role, context)
        self.calls.append((role, context))
        self.last_usage = {"total_tokens": 7}
        defect = self.defects.pop(0)
        raw = '{"issues":'
        if defect == "unknown_issue_target":
            raw = json.dumps({
                "reviewed_claim_ids": [c["id"] for c in context["claims"]],
                "reviewed_requirement_ids": [r["id"] for r in context["requirements"]],
                "issues": [{"target_id": "claim:absent-from-this-draft"}],
            })
        return _completed_role_output(role, raw, context)


class CriticReceiptRetryTests(unittest.TestCase):
    def run_case(self, defects, limit=16):
        store = SessionStore(":memory:")
        self.addCleanup(store.close)
        sid = store.create_session()["id"]
        model = BrokenReceiptModel(defects)
        result = ResearchEngine(store, model, FixtureTools()).ask(sid, "Revenue?", budget=Budget(max_model_calls=limit))
        return result, model.calls, [e for e in store.events(sid) if e["type"] == "model_call"]

    def test_each_contract_failure_retries_the_same_draft_only(self):
        for defect in ("unknown_issue_target", "invalid_critic_json"):
            with self.subTest(defect=defect):
                run, calls, events = self.run_case([defect])
                self.assertEqual([role for role, ctx in calls],
                                 ["planner", "planner", "researcher", "critic", "critic", "planner"])
                original, repair = calls[3][1], calls[4][1]
                hint = repair["critic_receipt_repair"]
                self.assertEqual(hint["previous_contract_error"], defect)
                self.assertEqual(original, {k: v for k, v in repair.items() if k != "critic_receipt_repair"})
                self.assertEqual(events[3]["contract_error"], defect)
                self.assertEqual(events[3]["draft_claim_versions"], events[4]["draft_claim_versions"])
                self.assertEqual(run["episode"]["usage"]["total_tokens"], 57)
                self.assertEqual(run["episode"]["tool_calls"], 1)
                self.assertEqual(run["status"], "complete")
                self.assertEqual(run["report"]["claims"][0]["status"], "Supported")

    def test_second_failure_stops_with_unverified_draft(self):
        run, calls, events = self.run_case(["invalid_critic_json", "unknown_issue_target"])
        self.assertEqual([role for role, ctx in calls][-2:], ["critic", "critic"])
        self.assertEqual(run["episode"]["model_calls"], 5)
        self.assertEqual(run["report"]["delivery_stage"], "critic_receipt_invalid")
        self.assertEqual(run["report"]["critic_receipt_error"], "unknown_issue_target")
        self.assertFalse(run["report"]["verification_available"])
        self.assertEqual(run["report"]["claims"][0]["status"], "Pending Verification")

    def test_exhausted_budget_keeps_first_failure_without_issuing_a_retry(self):
        run, calls, events = self.run_case(["invalid_critic_json"], limit=4)
        self.assertEqual(run["episode"]["stop_reason"], "model_budget_exhausted")
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(events), 4)
        self.assertEqual(run["report"]["critic_receipt_error"], "invalid_critic_json")
        self.assertFalse(run["report"]["verification_available"])

    def test_interruption_preserves_draft_and_evidence_if_verification_cannot_finish(self):
        cases = [
            ("model_budget_exhausted", 4, 1),
            ("time_budget_exhausted", 16, 1),
            ("execution_error", 16, 2),  # The retry also loses its response.
        ]
        for reason, limit, attempts in cases:
            with self.subTest(reason=reason), SessionStore(":memory:") as store:
                sid = store.create_session()["id"]
                now = 0

                def advance_clock(role, context):
                    nonlocal now
                    if role == "critic" and reason == "time_budget_exhausted":
                        now = 60

                script = full_script()[:3] + [("critic", IncompleteRead(b"", 1)) for _ in range(attempts)]
                model = ScriptedModel(script, callback=advance_clock)
                tools = FixtureTools()
                engine = ResearchEngine(store, model, tools, clock=lambda: now)
                result = engine.ask(sid, "Revenue?", budget=Budget(max_model_calls=limit, max_seconds=60))
                report = store.get_report(sid, 1)
                self.assertEqual(result["episode"]["stop_reason"], reason)
                self.assertEqual(report["delivery_stage"], "draft_unverified")
                self.assertFalse(report["verification_available"])
                claim, = report["claims"]
                fact, = report["evidence"]
                self.assertEqual((claim["text"], claim["status"]), (CLAIM["text"], "Pending Verification"))
                self.assertEqual(claim["evidence_ids"], [fact["id"]])
                self.assertEqual(fact["value"], FACT["value"])
                self.assertEqual(len(tools.calls), 1)
                self.assertEqual([r for r, _ in model.calls], ["planner", "planner", "researcher"] + ["critic"] * attempts)
                self.assertEqual(result["episode"]["model_calls"], len(model.calls))
                failed = [e for e in store.events(sid) if e["type"] == "model_call" and not e["ok"]]
                self.assertEqual([e["error_type"] for e in failed], ["IncompleteRead"] * attempts)
