"""Explicit synthetic human judgments test protocol mechanics, not E2 quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from verifin.evaluation import EvaluationError, _hash
from verifin.engine import digest
from verifin.planning_evaluation import run_planning_pair
from verifin.planning_scoring import (adjudicate_planning_annotations, build_planning_packet,
    make_planning_annotation_template, score_planning_outcome, summarize_planning_scores,
    validate_planning_annotation, write_planning_annotation_bundle)
from tests.test_planning import DATA, ProtocolModel, task
from tests.test_planning_evaluation import case_with


def fixture_annotation(packet, name="fixture-reviewer-a"):
    row = make_planning_annotation_template(packet, name)
    row.update(independent=True, completed_at="2026-10-15T10:00:00Z")
    for unit in row["assessment"]["units"]:
        unit.update(answerability="answerable", rationale="Synthetic test rubric; not an empirical human annotation.")
    for phase in ("before", "after"):
        answer = row["assessment"][phase]
        if answer is None:
            continue
        answer["claims_segmentation_complete"] = True
        source_claims = {claim["id"]: claim for claim in packet[phase]["claims"]}
        for assertion in answer["assertions"]:
            claim = source_claims[assertion["claim_id"]]
            assertion.update(verdict="supported", rationale="Explicit fixture judgment only.",
                             evidence_refs=claim["evidence_ids"] + claim["calculation_ids"],
                             requirement_unit_ids=["unit:" + rid for rid in claim["requirement_ids"]])
        for judgment in answer["units"]:
            refs = [a["id"] for a in answer["assertions"] if judgment["unit_id"] in a["requirement_unit_ids"]]
            judgment.update(verdict="correct" if refs else "missing", assertion_ids=refs, rationale="Explicit fixture content judgment.")
    for call in row["assessment"]["tool_calls"]:
        call.update(verdict="justified", rationale="Explicit fixture call judgment; successful execution alone is not the reason.")
    return row


def resolve(packet, first=None):
    first = first or fixture_annotation(packet)
    second = copy.deepcopy(first)
    second["annotator_id"] = "fixture-reviewer-b"
    resolution = {"source": "independent_human", "adjudicated": True, "packet_sha256": packet["packet_sha256"],
                  "adjudicators": ["fixture-reviewer-a", "fixture-reviewer-b"], "completed_at": "2026-10-15T11:00:00Z",
                  "assessment": copy.deepcopy(first["assessment"]), "resolution_notes": {}}
    return adjudicate_planning_annotations(packet, [first, second], resolution)


class PlanningScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.case = case_with(task())
        cls.paired_run = run_planning_pair(cls.case, ProtocolModel, DATA, output_dir=Path(cls.temp.name) / "paired")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def packet(self, name="adaptive"):
        return build_planning_packet(self.case, self.paired_run, name)

    def test_packet_bound_to_case_full_run_arm_turn_and_original_stages(self):
        packet = self.packet()
        self.assertEqual(packet["binding"]["run_sha256"], _hash(self.paired_run))
        self.assertEqual(packet["binding"]["turn_sha256"], _hash(self.paired_run["conditions"]["adaptive"]["turns"][0]))
        self.assertEqual(packet["binding"]["events_sha256"], _hash(self.paired_run["conditions"]["adaptive"]["turns"][0]["events"]))
        self.assertNotIn("status", packet["after"]["claims"][0])

    def test_call_time_observation_is_bound_and_preserved_without_unicode_hash_drift(self):
        run = copy.deepcopy(self.paired_run)
        event = next(e for e in run["conditions"]["adaptive"]["turns"][0]["events"] if e["type"] == "tool_call")
        event["observation"]["data"]["test_note"] = "真实来源数据的哈希编码保持一致"
        event["observation_sha256"] = digest(event["observation"])
        packet = build_planning_packet(self.case, run, "adaptive")
        self.assertEqual(packet["tool_calls"][0]["observation"], event["observation"])
        event["observation"]["data"]["test_note"] = "changed"
        with self.assertRaisesRegex(EvaluationError, "observation/hash"):
            build_planning_packet(self.case, run, "adaptive")

    def test_legacy_call_without_observation_is_not_manufactured(self):
        run = copy.deepcopy(self.paired_run)
        for arm in run["conditions"].values():
            for event in arm["turns"][0]["events"]:
                event.pop("observation", None)
                event.pop("observation_sha256", None)
        packet = build_planning_packet(self.case, run, "adaptive")
        self.assertTrue(all(c["observation"] is None for c in packet["tool_calls"]))

    def test_changed_stage_cannot_reuse_saved_hash(self):
        run = copy.deepcopy(self.paired_run)
        run["conditions"]["adaptive"]["turns"][0]["report"]["claims"][0]["text"] = "Tampered answer"
        with self.assertRaisesRegex(EvaluationError, "stage hash"):
            build_planning_packet(self.case, run, "adaptive")

    def test_pending_template_is_not_a_completed_human_judgment(self):
        packet = self.packet()
        template = make_planning_annotation_template(packet, "actual-person")
        self.assertFalse(template["independent"])
        self.assertTrue(all(c["verdict"] is None for c in template["assessment"]["tool_calls"]))
        with self.assertRaises(EvaluationError):
            validate_planning_annotation(packet, template)

    def test_dual_distinct_annotations_and_explicit_adjudication_required(self):
        packet = self.packet()
        adjudicated = resolve(packet)
        with self.assertRaises(EvaluationError):
            adjudicate_planning_annotations(packet, [adjudicated["annotations"][0]] * 2, adjudicated["resolution"])
        resolution = copy.deepcopy(adjudicated["resolution"])
        resolution["adjudicated"] = False
        with self.assertRaises(EvaluationError):
            adjudicate_planning_annotations(packet, adjudicated["annotations"], resolution)

    def test_disagreement_on_mistaken_calls_needs_rationale_and_no_auto_vote(self):
        packet = self.packet()
        resolved = resolve(packet)
        rows = copy.deepcopy(resolved["annotations"])
        rows[1]["assessment"]["tool_calls"][0]["verdict"] = "mistaken"
        with self.assertRaisesRegex(EvaluationError, "Resolution note"):
            adjudicate_planning_annotations(packet, rows, resolved["resolution"])
        resolution = copy.deepcopy(resolved["resolution"])
        resolution["resolution_notes"]["tool_calls"] = "Fixture adjudicators explicitly agree on legitimate discovery."
        adjudicated = adjudicate_planning_annotations(packet, rows, resolution)
        self.assertEqual(adjudicated["annotation_sha256"], [_hash(r) for r in rows])

    def test_all_claims_required_units_and_actual_calls_must_be_judged(self):
        packet = self.packet()
        for phase, field in (("after", "assertions"), ("before", "units"), (None, "tool_calls")):
            annotation = fixture_annotation(packet)
            target = annotation["assessment"][phase] if phase else annotation["assessment"]
            target[field] = []
            with self.subTest(field=field), self.assertRaises(EvaluationError):
                validate_planning_annotation(packet, annotation)

    def test_model_supported_badge_and_controller_completion_are_not_gold(self):
        packet = self.packet()
        self.assertEqual(packet["controller_status"], "complete")
        annotation = fixture_annotation(packet)
        after = annotation["assessment"]["after"]
        after["assertions"][0]["verdict"] = "unsupported"
        after["units"][0]["verdict"] = "incorrect"
        score = score_planning_outcome(packet, resolve(packet, annotation))
        self.assertEqual(score["after"]["correct_content_coverage"], 0)
        self.assertFalse(score["after"]["human_complete_supported_answer"])
        self.assertEqual(score["after"]["unsupported_assertions"], 1)

    def test_repeated_failed_call_is_not_automatically_mistaken(self):
        run = copy.deepcopy(self.paired_run)
        turn = run["conditions"]["adaptive"]["turns"][0]
        event = copy.deepcopy(next(e for e in turn["events"] if e["type"] == "tool_call"))
        event["ok"] = False
        event["summary"] = "Explicit fixture transient failure; human may consider repetition justified."
        turn["events"].append(event)
        packet = build_planning_packet(self.case, run, "adaptive")
        score = score_planning_outcome(packet, resolve(packet))
        self.assertEqual(score["tool_calls"]["mistaken"], 0)
        self.assertEqual(score["tool_calls"]["attempts"], 3)

    def test_mistaken_and_unknown_call_counts_preserve_null_and_bounds(self):
        packet = self.packet()
        annotation = fixture_annotation(packet)
        calls = annotation["assessment"]["tool_calls"]
        calls[0]["verdict"] = "mistaken"
        calls[1]["verdict"] = "unassessable"
        score = score_planning_outcome(packet, resolve(packet, annotation))
        self.assertEqual(score["tool_calls"]["mistaken"], 1)
        self.assertIsNone(score["tool_calls"]["mistaken_rate"])
        self.assertEqual(score["tool_calls"]["mistaken_rate_lower_bound"], .5)
        self.assertEqual(score["tool_calls"]["mistaken_rate_upper_bound"], 1)

    def test_null_stages_and_no_calls_remain_unknown_not_perfect(self):
        case = case_with(None)
        with tempfile.TemporaryDirectory() as directory:
            run = run_planning_pair(case, ProtocolModel, DATA, output_dir=Path(directory) / "run")
            packet = build_planning_packet(case, run, "fixed")
            self.assertIsNone(packet["before"])
            self.assertIsNone(packet["after"])
            score = score_planning_outcome(packet, resolve(packet))
            self.assertIsNone(score["before"])
            self.assertIsNone(score["after"])
            self.assertIsNone(score["tool_calls"]["mistaken_rate"])
            self.assertEqual(score["controller_status"], "waiting_for_clarification")

    def test_summary_preserves_pending_arm_and_rejects_different_run(self):
        packet = self.packet()
        score = score_planning_outcome(packet, resolve(packet))
        summary = summarize_planning_scores(self.paired_run, [score])
        self.assertFalse(summary["annotation_complete"])
        self.assertEqual(len(summary["rows"]), 2)
        self.assertEqual(summary["rows"][1]["annotation_status"], "pending")
        self.assertIsNone(summary["paired"]["after"]["correct_content_coverage"]["adaptive_minus_fixed_mean"])
        self.assertEqual(summary["paired"]["after"]["correct_content_coverage"]["paired_turns"], 0)
        changed = copy.deepcopy(self.paired_run)
        changed["created_at"] = "2026-10-15T12:00:00Z"
        with self.assertRaisesRegex(EvaluationError, "another run"):
            summarize_planning_scores(changed, [score])

    def test_paired_rubric_shared_but_original_answers_may_differ(self):
        adaptive, fixed = self.packet("adaptive"), self.packet("fixed")
        a, b = fixture_annotation(adaptive), fixture_annotation(fixed)
        a["assessment"]["before"]["units"][0]["verdict"] = "incorrect"
        a["assessment"]["before"]["assertions"][0]["verdict"] = "unsupported"
        scores = [score_planning_outcome(adaptive, resolve(adaptive, a)), score_planning_outcome(fixed, resolve(fixed, b))]
        summary = summarize_planning_scores(self.paired_run, scores)
        self.assertTrue(summary["annotation_complete"])
        self.assertEqual(summary["paired"]["before"]["correct_content_coverage"]["adaptive_minus_fixed_mean"], -1)
        b["assessment"]["units"][0]["description"] = "A different scoring target for the other condition"
        with self.assertRaisesRegex(EvaluationError, "same per-turn"):
            summarize_planning_scores(self.paired_run, [scores[0], score_planning_outcome(fixed, resolve(fixed, b))])

    def test_bundle_has_no_explicit_condition_or_model_trace_in_reviewer_input(self):
        packet = self.packet("fixed")
        with tempfile.TemporaryDirectory() as directory:
            result = write_planning_annotation_bundle(packet, directory, ["actual-reviewer-a", "actual-reviewer-b"])
            self.assertEqual(result["completed_annotations"], 0)
            view = json.loads((Path(directory) / "reviewer-1/input.json").read_text())
            self.assertNotIn("binding", view)
            self.assertNotIn("condition", view)
            self.assertNotIn("model_mode", view)
            self.assertNotIn("fixed_planner_step", json.dumps(view))
            self.assertEqual(view["public_history"], self.case.context()["history"])
            self.assertEqual(view["tool_calls"][0]["observation"], packet["tool_calls"][0]["observation"])
            with self.assertRaises(EvaluationError):
                write_planning_annotation_bundle(packet, directory, ["actual-reviewer-a", "actual-reviewer-b"])

    def test_multiturn_review_includes_preceding_request_and_answer_without_future_turn(self):
        case = case_with(task(), task(), task())
        with tempfile.TemporaryDirectory() as directory:
            run = run_planning_pair(case, ProtocolModel, DATA, output_dir=Path(directory) / "run")
            packet = build_planning_packet(case, run, "fixed", turn=2)
            self.assertEqual(packet["prior_requests"], case.context()["turns"][:1])
            self.assertEqual([p["turn"] for p in packet["prior_answers"]], [1])
            self.assertNotIn("Fixture public request 3", json.dumps(packet["prior_requests"]))

    def test_deleting_correct_required_answer_cannot_create_complete_answer(self):
        run = copy.deepcopy(self.paired_run)
        outcome = run["conditions"]["adaptive"]["turns"][0]
        outcome["report"]["claims"] = []
        outcome["after_verification_sha256"] = _hash(outcome["report"])
        packet = build_planning_packet(self.case, run, "adaptive")
        score = score_planning_outcome(packet, resolve(packet))
        self.assertEqual(score["before"]["correct_content_coverage"], 1)
        self.assertEqual(score["after"]["correct_content_coverage"], 0)
        self.assertIsNone(score["after"]["unsupported_assertion_rate"])
        self.assertFalse(score["after"]["human_complete_supported_answer"])


if __name__ == "__main__":
    unittest.main()
