"""A rejected input can change the next action without losing an independent fact."""
import tempfile
import unittest
from pathlib import Path

from tests.test_engine import FACT, RESEARCH, REVIEW, SCOPE, FixtureTools, ScriptedModel
from verifin.engine import Budget, ResearchEngine
from verifin.model import ModelClient
from verifin.reporting import render_markdown
from verifin.storage import SessionStore


class CalculatorFixture(FixtureTools):
    def list_tools(self):
        return [{"name": "calculate", "description": "fixture", "parameters": {"type": "object"}}]

    def execute(self, name, arguments, session_id, scope):
        self.calls.append(arguments)
        if arguments["operation"] == "accepted":
            return {"ok": True, "summary": "accepted", "data": {}, "evidence": [FACT], "calculations": []}
        return {"ok": False, "error": "fixture input rejected", "data": {"recoverable_input_error": True},
                "evidence": [], "calculations": []}


def calculate(operation):
    return ("planner", {"action":"tool", "tool":"calculate", "arguments":{"operation":operation}})


class RecoveryTests(unittest.TestCase):
    def test_uncompressible_context_is_reported_as_a_local_unsent_call(self):
        client = ModelClient("fixture", "test", "https://example.test/v1", max_context_chars=100,
                             transport=lambda *_: self.fail("local overflow must not reach transport"))
        with tempfile.TemporaryDirectory() as folder, SessionStore(Path(folder) / "case.db") as store:
            sid = store.create_session()["id"]
            store.put_evidence(sid, FACT)
            report = ResearchEngine(store, client, FixtureTools()).ask(sid, "Report revenue")["report"]
            self.assertEqual(report["stop_reason"], "local_context_limit")
            self.assertEqual(report["usage"]["model_calls"], 0)
            self.assertGreater(report["context_limit"]["characters"], 100)
            self.assertIn("这次请求没有发出", render_markdown(report))
            self.assertIn(FACT["id"], [row["id"] for row in report["evidence"]])

    def run_case(self, plans, *, model_limit=12):
        with tempfile.TemporaryDirectory() as folder, SessionStore(Path(folder) / "case.db") as store:
            sid = store.create_session()["id"]
            store.update_scope(sid, SCOPE)
            store.put_evidence(sid, FACT)
            model = ScriptedModel([calculate("bad"), *plans, ("researcher", RESEARCH), ("critic", REVIEW)])
            tools = CalculatorFixture()
            report = ResearchEngine(store, model, tools, stop_on_tool_fault=True, finish_after_review=True).ask(
                sid, "Report revenue for the requested quarter", budget=Budget(max_model_calls=model_limit))["report"]
            self.assertTrue(report["verification_available"], report["stop_reason"])
            self.assertEqual("Supported", report["claims"][0]["status"])
            self.assertFalse(model.script)
            return tools.calls, model.calls

    def test_identical_rejected_action_is_never_executed_twice(self):
        tools, calls = self.run_case([calculate("bad")])
        self.assertEqual(1, len(tools))
        self.assertEqual("fixture input rejected", calls[1][1]["episode"]["tool_recovery"]["detail"])

    def test_changed_operation_can_succeed(self):
        tools, calls = self.run_case([calculate("accepted"), ("planner", {"action":"research"})])
        self.assertEqual(2, len(tools))
        self.assertNotIn("tool_recovery", calls[-1][1]["episode"])

    def test_second_invalid_action_goes_directly_to_draft_and_review(self):
        tools, calls = self.run_case([calculate("different_bad")])
        self.assertEqual(2, len(tools))
        self.assertEqual(["planner", "planner", "researcher", "critic"], [role for role, _ in calls])

    def test_reserve_remaining_calls_for_draft_and_review(self):
        tools, calls = self.run_case([], model_limit=3)
        self.assertEqual(1, len(tools))
        self.assertEqual(["planner","researcher","critic"], [role for role, _ in calls])

    def test_corrected_result_reaches_review_with_two_calls_left(self):
        tools, calls = self.run_case([calculate("accepted")], model_limit=4)
        self.assertEqual(2, len(tools))
        self.assertEqual(["planner","planner","researcher","critic"], [role for role, _ in calls])
