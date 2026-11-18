import copy
import csv
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from verifin.evaluation import CONDITIONS, EvaluationError, _hash
from verifin.outcome_scoring import build_outcome_packet, score_outcome, summarize_resources, _seal
from verifin.study import (aggregate_results, assign_grouped_splits, blank_heldout_plan, case_manifest_entry,
                           create_study_plan, export_study_summary, freeze_study_plan, validate_study_plan,
                           write_annotation_bundle)
from tests.test_outcome_scoring import adjudication, runs


def entry(case_id, group=None, **changes):
    return {"case_id": case_id, "context_sha256": _hash({"fixture": case_id}), "group_id": group or case_id,
            "kind": "natural", "event_id": "event:" + case_id, "issuer_ids": [case_id.upper()],
            "dialogue_id": None, "parent_case_id": None, "user_turns": 2, **changes}


def make_plan(entries, heldout, **changes):
    return create_study_plan(entries, study_id="fixture-study", heldout_group_ids=heldout, expected_heldout_cases=1,
                             model_settings={"model": "fixture-model", "max_output_tokens": 1000},
                             budgets={"max_model_calls": 10, "max_tool_calls": 8, "max_seconds": 180},
                             tolerances={"min_coverage": 0.8, "max_model_cost": 1, "currency": "USD"}, **changes)


class SplitPlanTests(unittest.TestCase):
    def test_each_related_edge_prevents_cross_split_leakage(self):
        for field in ("event_id", "dialogue_id", "context_sha256"):
            a, b = entry("a"), entry("b")
            shared = _hash("same") if field == "context_sha256" else "same"
            a[field] = b[field] = shared
            with self.subTest(field=field), self.assertRaisesRegex(EvaluationError, "leak"):
                assign_grouped_splits([a, b], heldout_group_ids=["b"])

    def test_parent_perturbation_and_all_turns_stay_in_one_split(self):
        a, b = entry("a"), entry("b", kind="injected", parent_case_id="a")
        with self.assertRaisesRegex(EvaluationError, "parent"):
            assign_grouped_splits([a, b], heldout_group_ids=["b"])
        assigned = assign_grouped_splits([a, b], heldout_group_ids=["a", "b"])
        self.assertEqual({r["split"] for r in assigned}, {"heldout"})

    def test_injected_orphan_and_parent_cycle_rejected(self):
        for entries in ([entry("a", kind="injected")], [entry("a", parent_case_id="b"), entry("b", parent_case_id="a")]):
            with self.subTest(entries=entries), self.assertRaises(EvaluationError):
                assign_grouped_splits(entries, heldout_group_ids=["a"])

    def test_group_selection_unknown_overlap_and_duplicates_rejected(self):
        for heldout, validation in [(["missing"], []), (["a"], ["a"]), (["a", "a"], [])]:
            with self.subTest(heldout=heldout), self.assertRaises(EvaluationError):
                assign_grouped_splits([entry("a")], heldout_group_ids=heldout, validation_group_ids=validation)

    def test_blank_twelve_slots_do_not_invent_cases_events_or_annotators(self):
        blank = blank_heldout_plan()
        self.assertEqual(len(blank["slots"]), 12)
        self.assertEqual(blank["completed_cases"], 0)
        self.assertTrue(all(row["case_id"] is None and row["event_id"] is None and row["annotator_ids"] == [] for row in blank["slots"]))

    def test_empty_plan_is_draft_and_cannot_freeze(self):
        plan = create_study_plan([], study_id="pending", heldout_group_ids=[])
        self.assertFalse(plan["readiness"]["ready_to_freeze"])
        with self.assertRaises(EvaluationError):
            freeze_study_plan(plan)
        self.assertFalse(aggregate_results(plan, [])["annotation_complete"])

    def test_injected_or_duplicate_context_cannot_fill_natural_case_target(self):
        a = entry("a")
        b = entry("b", kind="injected", parent_case_id="a")
        plan = create_study_plan([a, b], study_id="pending", heldout_group_ids=["a", "b"], expected_heldout_cases=2)
        self.assertEqual(plan["readiness"]["distinct_natural_heldout_cases"], 1)

    def test_unseen_issuer_and_dialogue_requirements_are_checked(self):
        a, b = entry("a"), entry("b", issuer_ids=["A"], user_turns=1)
        plan = make_plan([a, b], ["b"])
        self.assertIn("no_heldout_issuer_absent_from_development_and_validation", plan["readiness"]["missing"])
        self.assertIn("no_heldout_dialogue", plan["readiness"]["missing"])

    def test_freeze_preserves_case_order_and_balanced_orders_and_seals_plan(self):
        entries = [entry("a"), entry("b"), entry("c")]
        plan = make_plan(entries, ["b", "c"], repetitions=2, seed=42)
        frozen = freeze_study_plan(plan)
        self.assertEqual(frozen["schedule"], plan["schedule"])
        self.assertEqual(validate_study_plan(frozen, require_frozen=True), frozen)
        self.assertEqual({tuple(r["review_order"]) for r in frozen["schedule"]}, {("generic_review", "structured_critic"), ("structured_critic", "generic_review")})
        changed = copy.deepcopy(frozen)
        changed["cases"][0]["split"] = "heldout"
        with self.assertRaises(EvaluationError):
            validate_study_plan(changed)

    def test_model_tool_time_limits_required_before_freeze(self):
        plan = make_plan([entry("a")], ["a"])
        plain = {k: v for k, v in plan.items() if k != "plan_sha256"}
        # Rebuild through the public constructor instead of inventing a valid seal.
        for budget in ({"max_model_calls": 1}, {"max_model_calls": 0, "max_tool_calls": 1, "max_seconds": 1}):
            p = create_study_plan([entry("a")], study_id="pending", heldout_group_ids=["a"], budgets=budget)
            self.assertIn("finite_positive_model_tool_time_budgets_required", p["readiness"]["missing"])


class MaterialSummaryTests(unittest.TestCase):
    def setUp(self):
        self.case, self.detection, self.revisions = runs()
        self.packet = build_outcome_packet(self.case, self.detection, self.revisions, "generic_review")
        self.score = score_outcome(self.packet, adjudication(self.packet))
        row = case_manifest_entry(self.case, group_id="held", kind="natural", event_id="fixture-event", issuer_ids=["MSFT"])
        self.plan = make_plan([row], ["held"], require_dialogue=False)

    def test_reviewers_receive_separate_pending_templates_without_condition_label(self):
        with tempfile.TemporaryDirectory() as directory:
            result = write_annotation_bundle(self.packet, directory, ["actual-person-a", "actual-person-b"])
            self.assertEqual(result["completed_annotations"], 0)
            self.assertFalse(result["truth_scored"])
            a = json.loads((Path(directory) / "reviewer-1/annotation.json").read_text())
            b = json.loads((Path(directory) / "reviewer-2/annotation.json").read_text())
            visible = json.loads((Path(directory) / "reviewer-1/input.json").read_text())
            self.assertNotIn("binding", visible)
            self.assertNotIn("condition", visible)
            self.assertFalse(a["independent"])
            self.assertIsNone(b["completed_at"])
            self.assertNotEqual(a["annotator_id"], b["annotator_id"])
            with self.assertRaises(EvaluationError):
                write_annotation_bundle(self.packet, directory, ["actual-person-a", "actual-person-b"])

    def test_missing_conditions_remain_rows_and_fail_strict_completion(self):
        summary = aggregate_results(self.plan, [self.score])
        self.assertEqual(len(summary["rows"]), 4)
        self.assertEqual(len(summary["missing_outcomes"]), 3)
        self.assertFalse(summary["annotation_complete"])
        self.assertTrue(all(r["candidate_correct_content_coverage"] is None for r in summary["rows"] if r["status"] == "pending_annotation"))
        with self.assertRaises(EvaluationError):
            aggregate_results(self.plan, [self.score], require_complete=True)

    def test_duplicate_or_other_context_scores_rejected(self):
        with self.assertRaises(EvaluationError):
            aggregate_results(self.plan, [self.score, self.score])
        forged = {k: v for k, v in copy.deepcopy(self.score).items() if k != "score_sha256"}
        forged["binding"]["context_sha256"] = _hash("other")
        with self.assertRaises(EvaluationError):
            aggregate_results(self.plan, [_seal(forged, "score_sha256")])

    def test_paired_conditions_require_same_gold_units_and_baseline_judgments(self):
        other = {k: v for k, v in copy.deepcopy(self.score).items() if k != "score_sha256"}
        other["binding"]["condition"] = "structured_critic"
        other["baseline_assessment_sha256"] = _hash("different baseline rubric")
        with self.assertRaisesRegex(EvaluationError, "same human"):
            aggregate_results(self.plan, [self.score, _seal(other, "score_sha256")])

    def test_exact_resource_run_binding_and_explicit_costs(self):
        prices = {"model": "fixture-model", "currency": "USD", "input_per_million": 2, "output_per_million": 4,
                  "pricing_scheme": "flat_rate",
                  "source": "Fictional test price", "effective_date": "2026-10-15"}
        resources = summarize_resources(self.detection, self.revisions, prices=prices)
        summary = aggregate_results(self.plan, [self.score], resources=[resources])
        row = next(r for r in summary["rows"] if r["condition"] == "generic_review")
        self.assertEqual(row["model_estimated_cost"], "0.0008")
        self.assertEqual(row["model_calls"], 3)
        forged = {k: v for k, v in copy.deepcopy(resources).items() if k != "resources_sha256"}
        forged["revision_run_sha256"] = _hash("different run")
        with self.assertRaisesRegex(EvaluationError, "different runs"):
            aggregate_results(self.plan, [self.score], resources=[_seal(forged, "resources_sha256")])

    def test_natural_injected_and_fixture_modes_remain_separate(self):
        summary = aggregate_results(self.plan, [self.score])
        group = next(g for g in summary["groups"] if g["condition"] == "generic_review")
        self.assertEqual(group["kind"], "natural")
        self.assertEqual(group["model_mode"], "scripted_fixture")
        self.assertEqual({g["kind"] for g in summary["paired"]}, {"natural", "injected"})

    def test_csv_json_export_keeps_unknown_empty_and_escapes_formula_cells(self):
        summary = aggregate_results(self.plan, [self.score])
        plain = {k: v for k, v in summary.items() if k != "summary_sha256"}
        plain["rows"][0]["case_id"] = "=HYPERLINK(\"https://example.invalid\")"
        safe_summary = _seal(plain, "summary_sha256")
        with tempfile.TemporaryDirectory() as directory:
            result = export_study_summary(safe_summary, directory)
            self.assertEqual(len(result["files"]), 4)
            with (Path(directory) / "cases.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows[0]["case_id"].startswith("'="))
            pending = next(r for r in rows if r["status"] == "pending_annotation")
            self.assertEqual(pending["candidate_correct_content_coverage"], "")
            self.assertEqual(json.loads((Path(directory) / "summary.json").read_text()), safe_summary)

    def test_materials_example_runs_offline_and_preserves_pending_human_work(self):
        from examples.study.prepare_materials import main
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            target = Path(directory) / "pending"
            self.assertEqual(main(["--output", str(target)]), 0)
            slots = json.loads((target / "heldout-slots.json").read_text())
            self.assertEqual(len(slots["slots"]), 12)
            self.assertEqual(slots["completed_annotations"], 0)
            plan = json.loads((target / "draft-plan.json").read_text())
            self.assertEqual(validate_study_plan(plan)["status"], "draft")
            root = Path(directory)
            for name, data in (("case", {"case_id": self.case.case_id, "context_sha256": self.case.context_sha256, "context": self.case.context()}),
                               ("detection", self.detection), ("revisions", self.revisions)):
                (root / (name + ".json")).write_text(json.dumps(data))
            bundle = root / "bundle"
            self.assertEqual(main(["--output", str(bundle), "--case", str(root / "case.json"),
                                   "--detection", str(root / "detection.json"), "--revisions", str(root / "revisions.json"),
                                   "--annotator", "fixture-reviewer-a", "--annotator", "fixture-reviewer-b"]), 0)
            annotation = json.loads((bundle / "reviewer-1/annotation.json").read_text())
            self.assertFalse(annotation["independent"])
            self.assertIsNone(annotation["completed_at"])


if __name__ == "__main__":
    unittest.main()
