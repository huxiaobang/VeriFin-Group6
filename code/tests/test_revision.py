"""Scripted fixtures validate revision control flow, not model effectiveness."""
import copy
import json
from pathlib import Path
import unittest

from verifin.evaluation import EvaluationError, FrozenCase, evaluate_frozen_case, run_revisions
from verifin.model import ModelError
from verifin.revision import CommonRevisionExecutor
from verifin.tools import ToolRegistry
from tests.test_evaluation import ReviewFixture
from tests.test_verification import fixture


DATA = Path(__file__).resolve().parents[1] / "data"


class ScriptedRevisionModel(ReviewFixture):
    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response(role, context) if callable(response) else copy.deepcopy(response)


class StubTools:
    timeout = 30

    def __init__(self, store, reply=None):
        self.store = store
        self.path = store.path if store else None
        self.calls = []
        self.reply = reply or {"ok": False, "summary": "No source available", "evidence": [],
                               "calculations": [], "data": {"network_requests": 1}}

    def list_tools(self):
        return [{"name": "fetch", "arguments": {}}]

    def execute(self, name, args, sid, scope):
        self.calls.append((name, copy.deepcopy(args), sid, copy.deepcopy(scope)))
        return copy.deepcopy(self.reply)


def corrected(context, claims=None, unanswered=None):
    return {"claims": copy.deepcopy(context["claims"] if claims is None else claims),
            "answered_requirement_ids": [] if unanswered else [r["id"] for r in context["requirements"]],
            "unanswered_requirement_ids": unanswered or [], "summary": "Candidate revision; requires human assessment."}


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.context["request"] = "State quarterly revenue."
        self.context["tools"] = StubTools(None).list_tools()
        self.registries = []

    def factory(self, store):
        registry = StubTools(store)
        self.registries.append(registry)
        return registry

    def test_revision_records_served_identity_on_success_and_failure(self):
        for failed in (False, True):
            class ServedModel(ScriptedRevisionModel):
                api_mode = "responses"
                service_tier = "default"

                def complete(self, role, context):
                    self.last_response_metadata = {"model": "served-snapshot", "service_tier": "default"}
                    return super().complete(role, context)

            model = ServedModel([ModelError("incomplete") if failed else {"action": "finish"}])
            result = CommonRevisionExecutor(model, self.factory).revise(self.context, {}, budget={})
            event = result["events"][0]
            self.assertEqual(event["ok"], not failed)
            self.assertEqual(event["base_url"], model.base_url)
            self.assertEqual(event["response_metadata"]["model"], "served-snapshot")
            self.assertEqual(event["usage"]["prompt_tokens"], 10)
            self.assertEqual(result["served_model_consistency"]["status"], "matched")

    def revise(self, replies, context=None, feedback=None, budget=None, **kwargs):
        model = ScriptedRevisionModel(replies)
        result = CommonRevisionExecutor(model, self.factory, **kwargs).revise(
            context or self.context, feedback or {"review": {"review": "Check the number.", "suggested_actions": "Correct if needed."}},
            budget={} if budget is None else budget)
        return model, result

    def test_both_feedback_shapes_use_identical_isolated_common_path(self):
        original = copy.deepcopy(self.context)
        feedbacks = [{"review": {"review": "The number is wrong.", "suggested_actions": "Correct it."}},
                     {"review": {"issues": [{"target_id": "c1", "proposed_action": "Correct the number."}]}}]
        calls = []
        for feedback in feedbacks:
            model, result = self.revise([{"action": "research"}, corrected(self.context), {"action": "finish"}], feedback=feedback)
            self.assertEqual([r for r, _ in model.calls], ["revision_planner", "researcher", "revision_planner"])
            self.assertEqual(result["status"], "candidate")
            self.assertFalse(result["truth_scored"])
            self.assertFalse(result["verification_available"])
            self.assertEqual(result["claims"][0]["status"], "Pending Verification")
            self.assertEqual(result["usage"]["model_calls"], 3)
            self.assertEqual(result["usage"]["total_tokens"], 45)
            self.assertEqual(result["model_mode"], "scripted_fixture")
            self.assertTrue(result["workspace_disposed"])
            self.assertNotIn("revision_feedback", result["context"])
            initial = copy.deepcopy(model.calls[0][1])
            initial.pop("revision_feedback")
            calls.append(initial)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.context, original)
        self.assertNotEqual(self.registries[0].path, self.registries[1].path)
        self.assertTrue(all(not Path(r.path).exists() for r in self.registries))

    def test_empty_candidate_preserves_required_unanswered(self):
        model, result = self.revise([{"action": "research"}, corrected(self.context, [], ["r1"]), {"action": "finish"}])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["unanswered"], self.context["requirements"])
        self.assertEqual(result["claims"], [])

    def test_scope_or_requirements_cannot_be_rewritten_or_dropped(self):
        for decision, expected in [({"action": "finish", "requirements": []}, "requirements_change_refused"),
                                   ({"action": "finish", "scope": {"requirements": []}}, "scope_change_refused"),
                                   ({"action": "finish", "scope": {"entities": []}}, "scope_change_refused")]:
            with self.subTest(decision=decision):
                model, result = self.revise([decision])
                self.assertEqual(result["stop_reason"], expected)
                self.assertEqual(result["context"]["requirements"], self.context["requirements"])
                self.assertEqual(result["usage"]["model_calls"], 1)

    def test_model_failure_is_counted_sanitized_and_not_retried(self):
        model, result = self.revise([ModelError("secret key provider text")])
        self.assertEqual(result["stop_reason"], "execution_error")
        self.assertEqual(result["error_type"], "ModelError")
        self.assertEqual(result["usage"]["model_calls"], 1)
        self.assertEqual(len(model.calls), 1)
        self.assertNotIn("secret", json.dumps(result))
        self.assertFalse(result["events"][0]["ok"])
        self.assertEqual(model.timeout, 60)

    def test_model_budget_stops_before_research_synthesis_attempt(self):
        model, result = self.revise([{"action": "research"}], budget={"max_model_calls": 1})
        self.assertEqual(result["stop_reason"], "model_budget_exhausted")
        self.assertEqual(len(model.calls), 1)

    def test_failed_tool_attempts_count_and_tool_limit_prevents_extra_execute(self):
        decision = {"action": "tool", "tool": "fetch", "arguments": {}}
        model, result = self.revise([decision, decision], budget={"max_tool_calls": 1})
        self.assertEqual(result["stop_reason"], "tool_budget_exhausted")
        self.assertEqual(result["usage"]["tool_calls"], 1)
        self.assertEqual(result["usage"]["network_requests"], 1)
        self.assertEqual(len(self.registries[0].calls), 1)
        self.assertFalse(result["context"]["revision_state"]["last_tool_result"]["ok"])

    def test_failed_tool_loop_stops_for_no_progress(self):
        decision = {"action": "tool", "tool": "fetch", "arguments": {}}
        model, result = self.revise([decision, decision], budget={"max_no_progress": 2})
        self.assertEqual(result["stop_reason"], "no_progress")
        self.assertEqual(result["usage"]["model_calls"], 2)
        self.assertEqual(result["usage"]["tool_calls"], 2)

    def test_time_budget_does_not_schedule_another_call(self):
        now = [0.0]
        def slow(role, context):
            now[0] = 10.0
            return {"action": "research"}
        model, result = self.revise([slow], clock=lambda: now[0], budget={"max_seconds": 5})
        self.assertEqual(result["stop_reason"], "time_budget_exhausted")
        self.assertEqual(result["usage"]["model_calls"], 1)
        self.assertEqual(result["usage"]["active_seconds"], 10.0)

    def test_cancel_before_first_call_leaves_candidate_pending(self):
        model, result = self.revise([], cancel_check=lambda: True)
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertEqual(result["usage"]["model_calls"], 0)
        self.assertEqual(result["claims"][0]["status"], "Pending Verification")

    def test_import_remaps_versions_without_healing_stale_claims(self):
        for defect in ("dependency_version", "scope_version", "explicit_stale"):
            context = copy.deepcopy(self.context)
            context["evidence"][0]["version"] = 7
            context["claims"][0]["evidence_versions"] = {"e1": 7}
            if defect == "dependency_version":
                context["claims"][0]["evidence_versions"] = {"e1": 6}
            elif defect == "scope_version":
                context["session"]["scope_version"] = 4
            else:
                context["claims"][0]["stale"] = True
            with self.subTest(defect=defect):
                _, result = self.revise([{"action": "finish"}], context=context)
                self.assertTrue(result["claims"][0]["stale"])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["version_mapping"]["evidence"]["e1"]["original_version"], 7)
                self.assertEqual(result["version_mapping"]["evidence"]["e1"]["working_version"], 1)

    def test_calculation_dependencies_remap_topologically_and_block_missing_operand(self):
        context = copy.deepcopy(self.context)
        context["evidence"][0]["version"] = 7
        calc1 = {"id": "k1", "operation": "ratio", "operand_ids": ["e1"], "operand_versions": {"e1": 7},
                 "formula": "fixture", "unit": "ratio", "value": 1, "version": 4}
        calc2 = {"id": "k2", "operation": "ratio", "operand_ids": ["k1"], "operand_versions": {"k1": 4},
                 "formula": "fixture", "unit": "ratio", "value": 1, "version": 6}
        context["calculations"] = [calc2, calc1, {**calc1, "id": "blocked", "operand_ids": ["absent"], "operand_versions": {"absent": 2}}]
        _, result = self.revise([{"action": "finish"}], context=context)
        calcs = {r["id"]: r for r in result["context"]["calculations"]}
        self.assertEqual(calcs["k1"]["operand_versions"], {"e1": 1})
        self.assertEqual(calcs["k2"]["operand_versions"], {"k1": 1})
        self.assertFalse(calcs["k2"]["stale"])
        self.assertTrue(calcs["blocked"]["stale"])
        self.assertIsNone(result["version_mapping"]["calculations"]["blocked"]["working_version"])

    def test_real_calculator_saves_derived_record_for_researcher(self):
        context = copy.deepcopy(self.context)
        context.pop("tools")
        context["evidence"][0].update(basis="US-GAAP", currency="USD", business_scope="consolidated")
        context["evidence"].append({**context["evidence"][0], "id": "income", "metric": "operating_income", "value": 25})
        def synthesis(role, current):
            self.assertEqual(role, "researcher")
            calc = current["calculations"][0]
            self.assertEqual(calc["value"], 25)
            self.assertEqual(calc["operand_versions"], {"income": 1, "e1": 1})
            claims = [{"id": "c2", "text": "Operating margin was 25%.", "evidence_ids": [],
                       "calculation_ids": [calc["id"]], "requirement_ids": ["r1"], "constraints": {"unit": "%"},
                       "asserted_value": 25, "asserted_unit": "%"}]
            return corrected(current, claims)
        model = ScriptedRevisionModel([{"action": "tool", "tool": "calculate", "arguments": {"operation": "margin", "operand_ids": ["income", "e1"]}},
                                       {"action": "research"}, synthesis, {"action": "finish"}])
        executor = CommonRevisionExecutor(model, lambda store: ToolRegistry(store, DATA, allow_network=False))
        result = executor.revise(context, {"review": "Compute a margin."}, budget={})
        self.assertEqual(result["stop_reason"], "planner_finished", result)
        self.assertEqual(result["usage"]["tool_calls"], 1)
        self.assertEqual(result["usage"]["network_requests"], 0)
        self.assertEqual(result["claims"][0]["asserted_value"], 25)
        self.assertEqual(result["claims"][0]["status"], "Pending Verification")

    def test_tool_interface_drift_rejected_before_model_calls(self):
        context = copy.deepcopy(self.context)
        context["tools"] = [{"name": "different"}]
        with self.assertRaisesRegex(EvaluationError, "tool registry differs"):
            self.revise([], context=context)

    def test_invalid_budget_rejected_before_calls(self):
        for budget in ({"max_model_calls": 0}, {"max_seconds": float("inf")}, {"surprise": 1}):
            with self.subTest(budget=budget), self.assertRaises(EvaluationError):
                self.revise([], budget=budget)


class RevisionCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.case = FrozenCase.from_context("paired", fixture())
        self.review_model = ReviewFixture()
        self.detection = evaluate_frozen_case(self.case, self.review_model)

    def executor(self, replies):
        model = ScriptedRevisionModel(replies)
        return CommonRevisionExecutor(model, lambda store: StubTools(store))

    def test_coordinator_keeps_conditions_outside_common_executor(self):
        executor = self.executor([{"action": "finish"}, {"action": "finish"}])
        run = run_revisions(self.case, self.detection, executor, budget={"max_model_calls": 2})
        self.assertEqual(list(run["conditions"]), ["generic_review", "structured_critic"])
        self.assertFalse(run["truth_scored"])
        for _, ctx in executor.model.calls:
            self.assertNotIn("condition", ctx)
        for result in run["conditions"].values():
            self.assertEqual(result["input_sha256"], self.case.context_sha256)
            self.assertEqual(result["revision_invocations"], 1)

    def test_failed_detection_does_not_receive_revision_fallback(self):
        self.detection["conditions"]["generic_review"]["status"] = "unavailable"
        executor = self.executor([{"action": "finish"}])
        run = run_revisions(self.case, self.detection, executor, budget={})
        self.assertEqual(run["conditions"]["generic_review"]["revision_invocations"], 0)
        self.assertEqual(len(executor.model.calls), 1)

    def test_no_verification_and_rules_controls_do_not_acquire_an_extra_critic(self):
        executor = self.executor([{"action": "finish"}, {"action": "finish"}])
        run = run_revisions(self.case, self.detection, executor, budget={}, conditions=("no_verification", "rules_only"))
        self.assertEqual([role for role, _ in executor.model.calls], ["revision_planner", "revision_planner"])
        self.assertEqual(executor.model.calls[0][1]["revision_feedback"], {"rule_findings": []})
        self.assertEqual(run["revision_order"], ["no_verification", "rules_only"])

    def test_invalid_second_arm_rejected_before_first_paid_attempt(self):
        self.detection["conditions"]["structured_critic"]["context_sha256"] = "other case"
        executor = self.executor([])
        with self.assertRaises(EvaluationError):
            run_revisions(self.case, self.detection, executor, budget={})
        self.assertEqual(executor.model.calls, [])

    def test_tampered_feedback_or_settings_rejected_before_revision(self):
        for defect in ("feedback", "settings"):
            run = copy.deepcopy(self.detection)
            if defect == "feedback":
                run["conditions"]["generic_review"]["feedback"]["review"]["review"] = "Altered output"
            else:
                run["settings"]["max_output_tokens"] += 1
            executor = self.executor([])
            with self.subTest(defect=defect), self.assertRaises(EvaluationError):
                run_revisions(self.case, run, executor, budget={})
            self.assertEqual(executor.model.calls, [])


if __name__ == "__main__":
    unittest.main()
