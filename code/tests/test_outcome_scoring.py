"""Human annotations below are explicit fixtures, never actual evaluation results."""
import copy
import unittest

from verifin.evaluation import EvaluationError, FrozenCase, PROTOCOL_VERSION, _hash
from verifin.outcome_scoring import (adjudicate_annotations, build_outcome_packet, make_annotation_template,
                                    score_outcome, summarize_resources, validate_annotation, validate_packet)
from tests.test_verification import fixture


def runs(context=None, candidate=None, *, unavailable=False):
    context = copy.deepcopy(context or fixture())
    case = FrozenCase.from_context("fixture-case", context)
    settings = {"model": "fixture-model", "max_output_tokens": 1000}
    feedback = {"rule_findings": []}
    detect = {"protocol": PROTOCOL_VERSION, "case_id": case.case_id, "context_sha256": case.context_sha256,
              "model_mode": "scripted_fixture", "settings": settings, "conditions": {
                  "generic_review": {"condition": "generic_review", "case_id": case.case_id, "context_sha256": case.context_sha256,
                                     "settings_sha256": _hash(settings), "status": "ok", "feedback": feedback,
                                     "output_sha256": _hash(feedback), "model_attempts": 1, "seconds": 2,
                                     "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}}}
    after = copy.deepcopy(candidate if candidate is not None else context)
    after["scope"] = {**context["scope"], "requirements": context["requirements"]}
    output = {"status": "partial" if unavailable else "candidate", "context": after, "claims": after["claims"],
              "settings": settings, "input_sha256": case.context_sha256, "feedback_sha256": _hash(feedback),
              "stop_reason": "planner_finished", "revision_invocations": 1,
              "usage": {"model_calls": 2, "tool_calls": 1, "network_requests": 0, "active_seconds": 3,
                        "prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230,
                        "prompt_tokens_reported_calls": 2, "completion_tokens_reported_calls": 2, "total_tokens_reported_calls": 2}}
    if unavailable:
        output = {"status": "unavailable", "stop_reason": "executor_error", "revision_invocations": 1, "usage": None}
    revision = {"protocol": PROTOCOL_VERSION, "phase": "candidate_revision", "case_id": case.case_id,
                "context_sha256": case.context_sha256, "settings": settings, "conditions": {"generic_review": output}}
    return case, detect, revision


def packet_for(context=None, candidate=None, **kwargs):
    case, detect, revision = runs(context, candidate, **kwargs)
    return build_outcome_packet(case, detect, revision, "generic_review")


def completed_annotation(packet, person="reviewer-a"):
    annotation = make_annotation_template(packet, person)
    annotation.update(independent=True, completed_at="2026-10-13T10:00:00Z")
    assessment = annotation["assessment"]
    for unit in assessment["units"]:
        unit.update(answerability="answerable", rationale="Fixture independently defined required fact.")
    for phase in ("baseline", "candidate"):
        answer = assessment[phase]
        if answer is None:
            continue
        answer["claims_segmentation_complete"] = True
        claims = {c["id"]: c for c in packet[phase]["claims"]}
        for assertion in answer["assertions"]:
            claim = claims[assertion["claim_id"]]
            assertion.update(verdict="supported", rationale="Fixture source support judgment.", evidence_refs=claim["evidence_ids"],
                             requirement_unit_ids=["unit:" + r for r in claim["requirement_ids"]])
            if phase == "candidate":
                assertion["baseline_assertion_ids"] = ["before:" + assertion["claim_id"] + ":1"] if assertion["claim_id"] in {a["claim_id"] for a in assessment["baseline"]["assertions"]} else []
        for judgment in answer["units"]:
            refs = [a["id"] for a in answer["assertions"] if judgment["unit_id"] in a["requirement_unit_ids"]]
            judgment.update(verdict="correct" if refs else "missing", assertion_ids=refs, rationale="Fixture content judgment.")
    return annotation


def adjudication(packet, annotation=None):
    first = annotation or completed_annotation(packet)
    second = copy.deepcopy(first)
    second["annotator_id"] = "reviewer-b"
    resolution = {"source": "independent_human", "adjudicated": True, "packet_sha256": packet["packet_sha256"],
                  "adjudicators": ["reviewer-a", "reviewer-b"], "completed_at": "2026-10-13T11:00:00Z",
                  "assessment": copy.deepcopy(first["assessment"]), "resolution_notes": {}}
    return adjudicate_annotations(packet, [first, second], resolution)


class OutcomeTests(unittest.TestCase):
    def test_template_is_not_scored_annotation(self):
        packet = packet_for()
        with self.assertRaises(EvaluationError):
            validate_annotation(packet, make_annotation_template(packet, "reviewer-a"))

    def test_exact_context_output_and_run_bindings(self):
        case, detect, revision = runs()
        packet = build_outcome_packet(case, detect, revision, "generic_review")
        self.assertEqual(packet["binding"]["revision_run_sha256"], _hash(revision))
        self.assertEqual(packet["binding"]["revision_output_sha256"], _hash(revision["conditions"]["generic_review"]))
        changed = copy.deepcopy(packet)
        changed["candidate"]["claims"][0]["text"] = "Tampered"
        with self.assertRaises(EvaluationError):
            validate_packet(changed)
        revision["conditions"]["generic_review"]["feedback_sha256"] = "other feedback"
        with self.assertRaises(EvaluationError):
            build_outcome_packet(case, detect, revision, "generic_review")

    def test_self_certified_badges_removed_and_never_used_as_gold(self):
        context = fixture()
        context["claims"][0]["status"] = "Supported"
        packet = packet_for(context)
        self.assertNotIn("status", packet["baseline"]["claims"][0])
        self.assertNotIn("status", packet["candidate"]["claims"][0])
        result = score_outcome(packet, adjudication(packet))
        self.assertEqual(result["source"], "independent_human")
        self.assertEqual(result["quality_change"], "unchanged")

    def test_two_distinct_completed_annotations_and_resolution_required(self):
        packet = packet_for()
        good = adjudication(packet)
        for annotations in ([good["annotations"][0]], [good["annotations"][0]] * 2):
            with self.subTest(n=len(annotations)), self.assertRaises(EvaluationError):
                adjudicate_annotations(packet, annotations, good["resolution"])
        bad = copy.deepcopy(good["resolution"])
        bad["adjudicated"] = False
        with self.assertRaises(EvaluationError):
            adjudicate_annotations(packet, good["annotations"], bad)

    def test_disagreement_needs_explicit_resolution_not_majority_vote(self):
        packet = packet_for()
        good = adjudication(packet)
        rows = copy.deepcopy(good["annotations"])
        rows[1]["assessment"]["candidate"]["assertions"][0]["verdict"] = "unsupported"
        rows[1]["assessment"]["candidate"]["units"][0]["verdict"] = "incorrect"
        with self.assertRaisesRegex(EvaluationError, "resolution note"):
            adjudicate_annotations(packet, rows, good["resolution"])
        resolution = copy.deepcopy(good["resolution"])
        resolution["resolution_notes"]["candidate"] = "Reviewers inspected the cited original and agreed the value is supported."
        result = adjudicate_annotations(packet, rows, resolution)
        self.assertEqual(result["annotation_sha256"], [_hash(row) for row in rows])

    def test_annotation_cannot_hide_original_requirement_or_unjudged_claim(self):
        packet = packet_for()
        for field in ("units", "assertions"):
            annotation = completed_annotation(packet)
            if field == "units":
                annotation["assessment"]["units"] = []
            else:
                annotation["assessment"]["candidate"]["assertions"] = []
            with self.subTest(field=field), self.assertRaises(EvaluationError):
                validate_annotation(packet, annotation)

    def test_correct_content_cannot_rely_on_unsupported_assertion(self):
        packet = packet_for()
        annotation = completed_annotation(packet)
        annotation["assessment"]["candidate"]["assertions"][0]["verdict"] = "unsupported"
        with self.assertRaisesRegex(EvaluationError, "fulfilled unit"):
            validate_annotation(packet, annotation)

    def test_correction_scored_separately_for_content_and_assertions(self):
        packet = packet_for()
        annotation = completed_annotation(packet)
        annotation["assessment"]["baseline"]["assertions"][0]["verdict"] = "unsupported"
        annotation["assessment"]["baseline"]["units"][0]["verdict"] = "incorrect"
        score = score_outcome(packet, adjudication(packet, annotation))
        self.assertEqual(score["changes"]["corrected_required_unit_ids"], ["unit:r1"])
        self.assertEqual(score["changes"]["corrected_assertion_ids"], ["before:c1:1"])
        self.assertEqual(score["quality_change"], "improved")
        self.assertTrue(score["safe_improvement"])

    def test_deleting_correct_required_content_blocks_improvement(self):
        context = fixture()
        context["requirements"].append({"id": "r2", "text": "Required second fact"})
        context["claims"].append({**copy.deepcopy(context["claims"][0]), "id": "c2", "requirement_ids": ["r2"]})
        after = copy.deepcopy(context)
        after["claims"] = after["claims"][:1]
        packet = packet_for(context, after)
        annotation = completed_annotation(packet)
        annotation["assessment"]["baseline"]["assertions"][0]["verdict"] = "unsupported"
        annotation["assessment"]["baseline"]["units"][0]["verdict"] = "incorrect"
        score = score_outcome(packet, adjudication(packet, annotation))
        self.assertEqual(score["quality_change"], "mixed")
        self.assertFalse(score["safe_improvement"])
        self.assertEqual(score["changes"]["deleted_correct_required_unit_ids"], ["unit:r2"])

    def test_removing_entire_bad_required_answer_does_not_count_as_correction(self):
        after = fixture()
        after["claims"] = []
        packet = packet_for(candidate=after)
        annotation = completed_annotation(packet)
        annotation["assessment"]["baseline"]["assertions"][0]["verdict"] = "unsupported"
        annotation["assessment"]["baseline"]["units"][0]["verdict"] = "incorrect"
        score = score_outcome(packet, adjudication(packet, annotation))
        self.assertFalse(score["safe_improvement"])
        self.assertEqual(score["changes"]["corrected_assertion_ids"], [])
        self.assertEqual(score["changes"]["removed_unsupported_required_assertion_ids"], ["before:c1:1"])
        self.assertIsNone(score["candidate"]["unsupported_assertion_rate"])
        self.assertEqual(score["candidate"]["correct_content_coverage"], 0)

    def test_unassessable_is_not_zero_error_or_perfect_coverage(self):
        packet = packet_for()
        annotation = completed_annotation(packet)
        annotation["assessment"]["candidate"]["assertions"][0]["verdict"] = "unassessable"
        annotation["assessment"]["candidate"]["units"][0]["verdict"] = "unassessable"
        score = score_outcome(packet, adjudication(packet, annotation))
        self.assertIsNone(score["candidate"]["unsupported_assertion_rate"])
        self.assertEqual(score["candidate"]["unsupported_assertion_rate_lower_bound"], 0)
        self.assertEqual(score["candidate"]["unsupported_assertion_rate_upper_bound"], 1)
        self.assertIsNone(score["safe_improvement"])

    def test_legitimate_gap_is_separate_from_substantive_coverage(self):
        packet = packet_for()
        annotation = completed_annotation(packet)
        annotation["assessment"]["units"][0]["answerability"] = "legitimate_gap"
        for phase in ("baseline", "candidate"):
            annotation["assessment"][phase]["units"][0]["verdict"] = "legitimate_gap"
        score = score_outcome(packet, adjudication(packet, annotation))
        self.assertIsNone(score["candidate"]["correct_content_coverage"])
        self.assertEqual(score["candidate"]["legitimate_gap_handling_rate"], 1)
        self.assertEqual(score["candidate"]["required_unit_fulfillment"], 1)

    def test_unavailable_run_stays_unavailable_with_no_fabricated_candidate(self):
        packet = packet_for(unavailable=True)
        score = score_outcome(packet, adjudication(packet))
        self.assertIsNone(score["candidate"])
        self.assertIsNone(score["changes"])
        self.assertIsNone(score["safe_improvement"])
        self.assertEqual(score["quality_change"], "unavailable")

    def test_modified_annotation_inside_adjudication_rejected(self):
        packet = packet_for()
        resolved = adjudication(packet)
        resolved["annotations"][0]["annotator_id"] = "other-person"
        with self.assertRaises(EvaluationError):
            score_outcome(packet, resolved)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.case, self.detect, self.revision = runs()
        self.prices = {"model": "fixture-model", "currency": "USD", "input_per_million": "2", "output_per_million": "4",
                       "pricing_scheme": "flat_rate",
                       "source": "Explicit fictional test price; not a real provider rate", "effective_date": "2026-10-13"}

    def row(self, **kwargs):
        return summarize_resources(self.detect, self.revision, **kwargs)["conditions"]["generic_review"]

    def test_complete_usage_and_explicit_decimal_price(self):
        row = self.row(prices=self.prices)
        self.assertEqual(row["model_calls"]["value"], 3)
        self.assertEqual(row["seconds"]["value"], 5)
        self.assertEqual(row["prompt_tokens"]["value"], 300)
        self.assertEqual(row["model_estimated_cost"], "0.0008")

    def test_missing_price_does_not_invent_cost(self):
        self.assertIsNone(self.row()["model_estimated_cost"])

    def test_legacy_two_rates_are_reference_only_without_explicit_flat_contract(self):
        prices = {k: v for k, v in self.prices.items() if k != "pricing_scheme"}
        row = self.row(prices=prices)
        self.assertEqual(row["unadjusted_token_cost_reference"], "0.0008")
        self.assertIsNone(row["model_estimated_cost"])
        self.assertIsNone(row["model_cost_known_lower_bound"])

    def test_cache_usage_blocks_two_rate_monetary_lower_bound_even_if_declared_flat(self):
        self.detect["conditions"]["generic_review"]["usage"].update(cached_input_tokens=100, cache_write_tokens=0)
        row = self.row(prices=self.prices)
        self.assertIsNone(row["model_estimated_cost"])
        self.assertIsNone(row["model_cost_known_lower_bound"])
        self.assertIn("catalog", row["cost_reason"])

    def test_responses_without_cache_details_still_requires_catalog(self):
        self.detect["settings"]["api_mode"] = "responses"
        self.detect["conditions"]["generic_review"]["settings_sha256"] = _hash(self.detect["settings"])
        row = self.row(prices=self.prices)
        self.assertIsNone(row["model_estimated_cost"])
        self.assertIsNone(row["model_cost_known_lower_bound"])

    def catalog_run(self):
        # Mock provider events exercise accounting only; no API call occurred.
        rates = {"input": 2, "output": 4, "cached_input": "0.1", "write_30m": 3, "write_12h": 5}
        catalog = {"schema_version": 1, "currency": "USD", "as_of": "2026-11-06",
                   "source_url": "https://example.test/pricing", "base_url": "https://example.test/v1",
                   "service_tier": "default", "models": {"served-fixture": {
                       "long_context_above": 10000, "short": rates, "long": rates}}}
        def event(output):
            return {"type": "model_call", "mode": "configured_model", "model": "fixture-model",
                    "base_url": catalog["base_url"], "service_tier": "default", "ok": True,
                    "response_metadata": {"model": "served-fixture", "service_tier": "default"},
                    "usage": {"prompt_tokens": 100, "completion_tokens": output,
                              "total_tokens": 100 + output, "cached_input_tokens": 100, "cache_write_tokens": 0}}
        self.detect["model_mode"] = "configured_model"
        self.detect["conditions"]["generic_review"]["events"] = [event(20)]
        self.revision["conditions"]["generic_review"]["events"] = [event(15), event(15)]
        return catalog

    def test_catalog_prices_served_model_and_cache_categories_across_both_stages(self):
        row = self.row(prices=self.catalog_run())
        self.assertEqual(row["cost_method"], "dated_catalog")
        self.assertEqual(row["model_estimated_cost"], "0.00023")
        self.assertTrue(row["cost_ledger"]["coverage_complete"])
        self.assertEqual(row["cost_ledger"]["recorded_model_calls"], 3)

    def test_catalog_missing_event_is_not_a_zero_cost_call(self):
        catalog = self.catalog_run()
        self.revision["conditions"]["generic_review"]["events"].pop()
        row = self.row(prices=catalog)
        self.assertIsNone(row["model_estimated_cost"])
        self.assertEqual(row["model_cost_known_lower_bound"], "0.00016")
        self.assertEqual(row["cost_ledger"]["unrecorded_attempts"], 1)

    def test_old_runs_without_per_call_metadata_remain_unpriced_under_catalog(self):
        catalog = self.catalog_run()
        del self.detect["conditions"]["generic_review"]["events"]
        del self.revision["conditions"]["generic_review"]["events"]
        row = self.row(prices=catalog)
        self.assertIsNone(row["model_estimated_cost"])
        self.assertIsNone(row["model_cost_known_lower_bound"])

    def test_missing_tokens_report_known_lower_bound_not_complete_total(self):
        self.revision["conditions"]["generic_review"]["usage"]["prompt_tokens_reported_calls"] = 1
        row = self.row(prices=self.prices)
        self.assertIsNone(row["prompt_tokens"]["value"])
        self.assertEqual(row["prompt_tokens"]["known_lower_bound"], 300)
        self.assertIsNone(row["model_estimated_cost"])
        self.assertEqual(row["model_cost_known_lower_bound"], "0.0008")

    def test_legacy_aggregate_tokens_not_assumed_complete_for_multiple_calls(self):
        usage = self.revision["conditions"]["generic_review"]["usage"]
        for key in list(usage):
            if key.endswith("reported_calls"):
                del usage[key]
        self.assertFalse(self.row()["prompt_tokens"]["complete"])

    def test_failure_missing_usage_is_not_zero_model_calls(self):
        self.revision["conditions"]["generic_review"].update(status="unavailable", usage=None)
        row = self.row(prices=self.prices)
        self.assertIsNone(row["model_calls"]["value"])
        self.assertEqual(row["model_calls"]["known_lower_bound"], 1)
        self.assertIsNone(row["model_estimated_cost"])

    def test_zero_attempts_are_known_zero_and_empty_token_counts_not_required(self):
        self.detect["conditions"]["generic_review"].update(model_attempts=0, usage={})
        self.revision["conditions"]["generic_review"].update(status="unavailable", revision_invocations=0, usage=None)
        row = self.row(prices=self.prices)
        self.assertEqual(row["prompt_tokens"]["value"], 0)
        self.assertEqual(row["model_estimated_cost"], "0")

    def test_cross_run_mix_or_wrong_model_prices_rejected(self):
        bad = copy.deepcopy(self.prices)
        bad["model"] = "another-model"
        with self.assertRaises(EvaluationError):
            self.row(prices=bad)
        self.revision["case_id"] = "different-case"
        with self.assertRaises(EvaluationError):
            self.row()

    def test_revision_resource_output_cannot_be_swapped_between_conditions(self):
        self.revision["conditions"]["generic_review"]["feedback_sha256"] = _hash("another arm")
        with self.assertRaisesRegex(EvaluationError, "not bound"):
            self.row()

    def test_reporting_counter_cannot_exceed_attempt_count(self):
        self.revision["conditions"]["generic_review"]["usage"]["prompt_tokens_reported_calls"] = 3
        with self.assertRaises(EvaluationError):
            self.row()

    def test_negative_nan_and_bool_prices_rejected(self):
        for value in (-1, True, "NaN", "Infinity"):
            price = {**self.prices, "input_per_million": value}
            with self.subTest(value=value), self.assertRaises(EvaluationError):
                self.row(prices=price)


if __name__ == "__main__":
    unittest.main()
