import copy
import unittest

from verifin.evaluation import EvaluationError, FrozenCase, evaluate_frozen_case, revision_input, score_detection
from verifin.model import ModelError
from tests.test_verification import fixture


class ReviewFixture:
    model = "test-model-snapshot"
    base_url = "https://example.test/v1"
    max_output_tokens = 4096
    max_context_chars = 200000
    enable_thinking = False
    timeout = 60
    mode = "scripted_fixture"

    def __init__(self):
        self.calls = []
        self.last_usage = {}

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        if role == "generic_reviewer":
            return {"review": "The number is overstated relative to the original source.",
                    "suggested_actions": "Correct the number and review the affected conclusion."}
        return {"issues": [{"target_id": "c1", "issue_type": "numerical_inconsistency", "evidence_refs": ["e1"],
                            "missing_support": "The original says 100, but the claim says 110.",
                            "proposed_action": "Correct the claim to the original value."}],
                "reviewed_claim_ids": ["c1"], "reviewed_requirement_ids": ["r1"],
                "claim_verdicts":[{"claim_id":"c1","status":"Insufficient Evidence","evidence_refs":["e1"],
                                  "basis":"The original gives 100 and the draft gives 110."}]}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.context["claims"][0].update(text="Quarterly revenue was USD 110.", asserted_value=110, status="Supported")
        self.context["tools"] = [{"name": "financial_facts"}, {"name": "calculate"}]
        self.case = FrozenCase.from_context("heldout-case-1", self.context)

    def run_case(self, model=None, **kwargs):
        return evaluate_frozen_case(self.case, model or ReviewFixture(), **kwargs)

    def annotation(self):
        return {"case_id": self.case.case_id, "context_sha256": self.case.context_sha256,
                "source": "independent_human", "annotators": ["annotator-a", "annotator-b"],
                "adjudicated": True, "guideline_version": "v1"}

    def gold(self):
        return {**self.annotation(), "issues": [{"id": "gold:number", "target_id": "c1",
                                                 "description": "Numeric claim is inconsistent with the original source."}]}

    def judgments(self, result, findings=None):
        return {**self.annotation(), "condition": result["condition"], "output_sha256": result["output_sha256"],
                "segmentation_complete": True,
                "findings": findings if findings is not None else [{"id": "f1", "text": "Overstated value.", "gold_issue_id": "gold:number"}]}

    def test_case_freezes_original_sources_and_strips_self_status_badges(self):
        self.assertNotIn("status", self.case.context()["claims"][0])
        self.context["evidence"][0]["value"] = 900
        detached = self.case.context()
        detached["evidence"][0]["value"] = 999
        self.assertEqual(self.case.context()["evidence"][0]["value"], 100)

    def test_gold_cannot_be_injected_into_model_context(self):
        for key in ("gold", "annotations", "expected_answer"):
            context = copy.deepcopy(self.context)
            context[key] = {"answer": "Correct it to 100"}
            with self.subTest(key=key), self.assertRaises(EvaluationError):
                FrozenCase.from_context("case", context)

    def test_reviewed_episode_or_reviewer_message_cannot_be_frozen(self):
        for extra in ({"episode": {"reviewed": True}},
                      {"critic_review": {"issues": []}},
                      {"messages": [{"role": "critic", "content": "The correct number is 100."}]}):
            with self.subTest(extra=extra), self.assertRaises(EvaluationError):
                FrozenCase.from_context("case", {**self.context, **extra})

    def test_past_reports_removed_original_episode_request_retained(self):
        context = {**self.context, "episode": {"reviewed": False, "request": "Compare quarterly revenue."},
                   "reports": [{"issues": [{"missing_support": "Previously reviewed result"}]}],
                   "messages": [{"role": "user", "content": "Use the December quarter."}]}
        frozen = FrozenCase.from_context("case", context).context()
        self.assertEqual(frozen["request"], "Compare quarterly revenue.")
        self.assertNotIn("reports", frozen)
        self.assertNotIn("episode", frozen)

    def test_conflicting_scope_or_requirement_copies_are_rejected(self):
        for extra in ({"scope": {**self.context["scope"], "requirements": []}},
                      {"session": {"scope_version": 1, "scope": {"entities": ["NVDA"]}}}):
            with self.subTest(extra=extra), self.assertRaises(EvaluationError):
                FrozenCase.from_context("case", {**self.context, **extra})

    def test_review_conditions_have_identical_inputs_and_settings(self):
        model = ReviewFixture()
        run = self.run_case(model)
        self.assertEqual([r for r, _ in model.calls], ["generic_reviewer", "critic"])
        self.assertEqual(model.calls[0][1], model.calls[1][1])
        generic, structured = [run["conditions"][key] for key in ("generic_review", "structured_critic")]
        self.assertEqual(generic["settings_sha256"], structured["settings_sha256"])
        self.assertEqual(generic["review_input_sha256"], structured["review_input_sha256"])
        self.assertEqual(run["conditions"]["rules_only"]["model_attempts"], 0)
        self.assertEqual(run["conditions"]["no_verification"]["feedback"], {"rule_findings": []})
        self.assertEqual(run["model_mode"], "scripted_fixture")
        self.assertFalse(run["truth_scored"])
        self.assertFalse(run["revision_evaluated"])

    def test_both_review_orders_supported_without_changing_case(self):
        model = ReviewFixture()
        run = self.run_case(model, review_order=("structured_critic", "generic_review"))
        self.assertEqual([r for r, _ in model.calls], ["critic", "generic_reviewer"])
        self.assertEqual(run["context_sha256"], self.case.context_sha256)

    def test_served_identity_and_usage_survive_failed_review_and_show_model_drift(self):
        class ServedModel(ReviewFixture):
            api_mode = "responses"
            service_tier = "default"

            def complete(self, role, context):
                result = super().complete(role, context)
                self.last_response_metadata = {"model": "served-a" if role == "generic_reviewer" else "served-b",
                                               "service_tier": "default", "untrusted_extra": "not saved"}
                self.last_usage.update(cached_input_tokens=25, cache_write_tokens=0)
                if role == "generic_reviewer":
                    raise ModelError("incomplete provider output")
                return result

        run = self.run_case(ServedModel())
        generic = run["conditions"]["generic_review"]
        event = generic["events"][0]
        self.assertEqual(generic["status"], "unavailable")
        self.assertFalse(event["ok"])
        self.assertEqual(event["base_url"], ServedModel.base_url)
        self.assertEqual(event["api_mode"], "responses")
        self.assertEqual(event["response_metadata"], {"model": "served-a", "service_tier": "default"})
        self.assertEqual(event["usage"]["cached_input_tokens"], 25)
        self.assertEqual(run["conditions"]["structured_critic"]["events"][0]["response_metadata"]["model"], "served-b")
        self.assertEqual(run["served_model_consistency"]["status"], "mismatch")
        self.assertEqual(run["conditions"]["no_verification"]["events"], [])

    def test_caller_cannot_mutate_rules_in_other_condition_outputs(self):
        run = self.run_case()
        run["conditions"]["rules_only"]["feedback"]["rule_findings"].clear()
        self.assertTrue(run["conditions"]["generic_review"]["feedback"]["rule_findings"])

    def test_settings_drift_is_not_a_matched_pair(self):
        class Drifting(ReviewFixture):
            def complete(self, role, context):
                output = super().complete(role, context)
                self.max_output_tokens = 8192
                return output
        with self.assertRaisesRegex(EvaluationError, "settings changed"):
            self.run_case(Drifting())

    def test_failure_counted_without_retry_or_fake_empty_review(self):
        class Broken(ReviewFixture):
            def complete(self, role, context):
                self.calls.append((role, context))
                self.last_usage = {}
                raise ModelError("secret provider content")
        model = Broken()
        run = self.run_case(model)
        self.assertEqual(len(model.calls), 2)
        result = run["conditions"]["generic_review"]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["model_attempts"], 1)
        self.assertNotIn("review", result["feedback"])
        self.assertNotIn("secret", str(run))
        with self.assertRaises(EvaluationError):
            score_detection(self.case, result, self.gold(), self.judgments(result, []))

    def test_scores_only_supplied_adjudicated_findings(self):
        result = self.run_case()["conditions"]["generic_review"]
        judgments = self.judgments(result, [
            {"id": "f1", "text": "The value is wrong.", "gold_issue_id": "gold:number"},
            {"id": "f2", "text": "Repeated same problem.", "gold_issue_id": "gold:number"},
            {"id": "f3", "text": "Unsupported reviewer complaint.", "gold_issue_id": None},
        ])
        score = score_detection(self.case, result, self.gold(), judgments)
        self.assertEqual((score["true_positives"], score["false_positives"], score["false_negatives"]), (1, 2, 0))
        self.assertEqual(score["duplicate_matches"], 1)
        self.assertEqual(score["precision"], 1 / 3)
        self.assertEqual(score["recall"], 1)

    def test_no_verification_has_zero_detected_issues_not_fake_success(self):
        result = self.run_case()["conditions"]["no_verification"]
        score = score_detection(self.case, result, self.gold(), self.judgments(result, []))
        self.assertEqual(score["false_negatives"], 1)
        self.assertIsNone(score["precision"])
        self.assertEqual(score["recall"], 0)
        self.assertEqual(score["f1"], 0)

    def test_model_labels_and_unadjudicated_gold_are_rejected(self):
        result = self.run_case()["conditions"]["structured_critic"]
        for field, value in (("source", "model_output"), ("annotators", ["one-human"]),
                             ("adjudicated", False), ("context_sha256", "wrong-case")):
            gold = self.gold()
            gold[field] = value
            with self.subTest(field=field), self.assertRaises(EvaluationError):
                score_detection(self.case, result, gold, self.judgments(result))
        with self.assertRaises(EvaluationError):
            score_detection(self.case, result, {"status": "Supported"}, self.judgments(result))

    def test_output_binding_rejects_judgments_for_altered_feedback(self):
        result = self.run_case()["conditions"]["generic_review"]
        judgments = self.judgments(result)
        result["feedback"]["review"]["review"] = "A different output"
        with self.assertRaisesRegex(EvaluationError, "exact condition output"):
            score_detection(self.case, result, self.gold(), judgments)

    def test_revision_adapter_has_same_shape_and_never_executes(self):
        run = self.run_case()
        inputs = [revision_input(self.case, r) for r in run["conditions"].values()]
        self.assertTrue(all(set(x) == {"context", "feedback"} for x in inputs))
        self.assertTrue(all(x["context"] == inputs[0]["context"] for x in inputs))
        self.assertTrue(all(x["context"]["tools"] == self.context["tools"] for x in inputs))


if __name__ == "__main__":
    unittest.main()
