"""Paired-execution integrity checks; all model outputs are explicit fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from verifin.engine import Budget
from verifin.evaluation import EvaluationError
from verifin.planning_evaluation import FrozenPlanningCase, preview_fixed_workflow, run_planning_pair
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry
from tests.test_planning import DATA, ProtocolModel, Q1, Q2, task


def case_with(*tasks):
    return FrozenPlanningCase.from_dict({"case_id": "protocol-fixture",
        "history": [{"role": "user", "content": "Use the public information cutoff."}],
        "turns": [{"message": "Fixture public request " + str(i), "task": t} for i, t in enumerate(tasks, 1)]})


class PlanningPairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "run"
        self.models = []

    def tearDown(self):
        self.temp.cleanup()

    def model_factory(self):
        model = ProtocolModel()
        self.models.append(model)
        return model

    def run_pair(self, case=None, **kwargs):
        return run_planning_pair(case or case_with(task()), self.model_factory, DATA,
                                 output_dir=self.output, **kwargs)

    def test_pair_records_true_model_counts_and_pre_post_artifacts(self):
        run = self.run_pair()
        self.assertFalse(run["truth_scored"])
        self.assertEqual(run["model_mode"], "scripted_protocol_fixture")
        self.assertEqual(run["conditions"]["fixed"]["diagnostics"]["model_attempts"], 2)
        self.assertEqual(run["conditions"]["adaptive"]["diagnostics"]["model_attempts"], 6)
        for name in ("adaptive", "fixed"):
            arm = run["conditions"][name]
            turn = arm["turns"][0]
            self.assertEqual(turn["status"], "complete", turn)
            self.assertFalse(turn["before_verification"]["episode"]["reviewed"])
            self.assertEqual(turn["before_verification"]["claims"][0]["status"], "Pending Verification")
            self.assertTrue(turn["report"]["verification_available"])
            self.assertEqual(arm["diagnostics"]["tool_attempts"], 2)
            self.assertEqual(arm["diagnostics"]["planner_steps"], 4)
            self.assertEqual(arm["diagnostics"]["network_requests"], 0)
            self.assertTrue((self.output / name / "session.sqlite").exists())
            saved = json.loads((self.output / name / "turn-01.json").read_text())
            self.assertEqual(saved, turn)
        # Both models receive the same public specification, request and sources.
        contexts = [next(ctx for role, ctx in m.calls if role == "researcher") for m in self.models]
        self.assertEqual(contexts[0]["scope"], contexts[1]["scope"])
        self.assertEqual([(m["role"], m["content"]) for m in contexts[0]["messages"]],
                         [(m["role"], m["content"]) for m in contexts[1]["messages"]])
        self.assertEqual({e["snapshot_hash"] for e in contexts[0]["evidence"]},
                         {e["snapshot_hash"] for e in contexts[1]["evidence"]})
        self.assertNotEqual(contexts[0]["session"]["id"], contexts[1]["session"]["id"])

    def test_same_budget_can_stop_adaptive_model_attempts_without_faking_fixed_calls(self):
        run = self.run_pair(budget=Budget(max_model_calls=2))
        adaptive = run["conditions"]["adaptive"]["turns"][0]
        fixed = run["conditions"]["fixed"]["turns"][0]
        self.assertEqual(adaptive["episode"]["stop_reason"], "model_budget_exhausted")
        self.assertEqual(fixed["status"], "complete")
        self.assertEqual(run["conditions"]["adaptive"]["diagnostics"]["model_attempts"], 2)
        self.assertEqual(run["conditions"]["fixed"]["diagnostics"]["model_attempts"], 2)
        self.assertIsNone(adaptive["before_verification"])

    def test_multiturn_reuses_within_arm_only_and_preserves_report_versions(self):
        case = case_with(task("margin", "operating_income"), task("growth", periods=[Q1, Q2]))
        run = self.run_pair(case)
        for name in ("adaptive", "fixed"):
            arm = run["conditions"][name]
            self.assertEqual([t["status"] for t in arm["turns"]], ["complete", "complete"])
            calls = [e for e in arm["turns"][1]["events"] if e["type"] == "tool_call"]
            self.assertEqual([c["tool"] for c in calls], ["financial_facts", "calculate"])
            with SessionStore(arm["database"]) as store:
                sid = store.list_sessions()[0]["id"]
                reports = store.list_reports(sid)
                self.assertEqual(reports[0], arm["turns"][0]["report"])
                self.assertEqual([r["version"] for r in reports], [1, 2])
                self.assertEqual(len(store.list_sessions()), 1)
        left = run["conditions"]["adaptive"]["turns"][0]["report"]
        right = run["conditions"]["fixed"]["turns"][0]["report"]
        self.assertNotEqual(left["episode_id"], right["episode_id"])

    def test_provider_failure_is_counted_and_preverification_candidate_retained(self):
        def factory():
            return ProtocolModel(fail_role="critic")
        run = run_planning_pair(case_with(task()), factory, DATA, output_dir=self.output)
        for arm in run["conditions"].values():
            self.assertEqual(arm["diagnostics"]["failed_model_attempts"], 1)
            turn = arm["turns"][0]
            self.assertIsNotNone(turn["before_verification"])
            self.assertFalse(turn["report"]["verification_available"])
            self.assertEqual(turn["report"]["stop_reason"], "execution_error")

    def test_source_failure_configuration_is_equal_and_failures_visible(self):
        class FailingTools(ToolRegistry):
            evaluation_config = {"fault": "financial_facts_unavailable"}
            def execute(self, name, arguments, session_id, scope):
                if name == "financial_facts":
                    return {"ok": False, "summary": "Declared fixture source failure", "evidence": [], "calculations": [], "data": {"network_requests": 0}}
                return super().execute(name, arguments, session_id, scope)
        run = self.run_pair(tools_factory=lambda store, source, cache: FailingTools(store, source, cache_dir=cache))
        for arm in run["conditions"].values():
            self.assertEqual(arm["turns"][0]["status"], "partial")
            self.assertGreaterEqual(arm["diagnostics"]["failed_tool_attempts"], 1)
            self.assertFalse(arm["diagnostics"]["truth_scored"])
        self.assertEqual(run["tool_settings"]["configuration"]["fault"], "financial_facts_unavailable")

    def test_changed_model_settings_rejected_before_any_model_call(self):
        def factory():
            model = self.model_factory()
            model.max_output_tokens += len(self.models)
            return model
        with self.assertRaisesRegex(EvaluationError, "identical model"):
            run_planning_pair(case_with(task()), factory, DATA, output_dir=self.output)
        self.assertEqual(sum(len(m.calls) for m in self.models), 0)

    def test_adaptive_cannot_drop_public_requirements_or_extend_cutoff(self):
        for field, value in [("requirements", []), ("cutoff", "2030-01-01")]:
            class WeakenedTaskModel(ProtocolModel):
                def complete(self, role, context):
                    result = super().complete(role, context)
                    if role == "planner":
                        result["scope"] = {field: value}
                    return result
            with tempfile.TemporaryDirectory() as directory:
                run = run_planning_pair(case_with(task()), WeakenedTaskModel, DATA, output_dir=directory)
                adaptive = run["conditions"]["adaptive"]
                self.assertEqual(adaptive["turns"][0]["episode"]["stop_reason"], "execution_error")
                self.assertEqual(adaptive["diagnostics"]["model_attempts"], 1)
                self.assertEqual(adaptive["diagnostics"]["failed_model_attempts"], 1)
                self.assertEqual(run["conditions"]["fixed"]["turns"][0]["status"], "complete")

    def test_changed_tools_or_fault_config_rejected_before_model_calls(self):
        registries = []
        def factory(store, source, cache):
            registry = ToolRegistry(store, source, cache_dir=cache)
            registry.evaluation_config = {"different": len(registries)}
            registries.append(registry)
            return registry
        with self.assertRaisesRegex(EvaluationError, "differ between paired"):
            self.run_pair(tools_factory=factory)
        self.assertEqual(sum(len(m.calls) for m in self.models), 0)

    def test_shared_model_object_rejected(self):
        model = ProtocolModel()
        with self.assertRaisesRegex(EvaluationError, "fresh model"):
            run_planning_pair(case_with(task()), lambda: model, DATA, output_dir=self.output)
        self.assertFalse(model.calls)

    def test_shared_or_prepopulated_tool_cache_rejected_before_inference(self):
        def shared(store, source, cache):
            return ToolRegistry(store, source, cache_dir=self.output / "shared-cache")
        with self.assertRaisesRegex(EvaluationError, "own assigned cache"):
            self.run_pair(tools_factory=shared)
        self.assertEqual(sum(len(m.calls) for m in self.models), 0)
        failure = json.loads((self.output / "failure.json").read_text())
        self.assertEqual(failure["status"], "protocol_failure")

    def test_different_extra_artifact_hash_rejected_before_inference(self):
        calls = []
        def factory(store, source, cache):
            calls.append(1)
            return ToolRegistry(store, source, cache_dir=cache, retrieval_vectors={"fixture": len(calls)})
        with self.assertRaisesRegex(EvaluationError, "differ between paired"):
            self.run_pair(tools_factory=factory)
        self.assertEqual(sum(len(m.calls) for m in self.models), 0)
        self.assertTrue((self.output / "adaptive" / "inputs" / "retrieval_vectors.json").exists())

    def test_attached_live_market_adapter_is_rejected(self):
        def factory(store, source, cache):
            return ToolRegistry(store, source, cache_dir=cache, market_adapter=object())
        with self.assertRaisesRegex(EvaluationError, "attached live market"):
            self.run_pair(tools_factory=factory)
        self.assertEqual(sum(len(m.calls) for m in self.models), 0)

    def test_clarification_episode_keeps_consumed_allowance_and_no_double_count(self):
        run = self.run_pair(case_with(None, task()), budget=Budget(max_model_calls=12))
        for name, arm in run["conditions"].items():
            first, second = arm["turns"]
            self.assertEqual(first["status"], "waiting_for_clarification")
            self.assertEqual(first["episode"]["id"], second["episode"]["id"])
            self.assertEqual(arm["diagnostics"]["episodes"], 1)
            self.assertEqual(arm["diagnostics"]["model_attempts"], second["episode"]["model_calls"])
            self.assertEqual(second["episode"]["planner_steps"], 5)

    def test_output_directory_cannot_overwrite_a_run(self):
        self.output.mkdir()
        (self.output / "old.txt").write_text("preserve")
        with self.assertRaisesRegex(EvaluationError, "empty output"):
            self.run_pair()
        self.assertEqual((self.output / "old.txt").read_text(), "preserve")

    def test_reverse_condition_order_has_same_sources_and_configuration(self):
        run = self.run_pair(condition_order=("fixed", "adaptive"))
        self.assertEqual(run["condition_order"], ["fixed", "adaptive"])
        self.assertEqual(len({a["source_manifest_sha256"] for a in run["conditions"].values()}), 1)
        self.assertEqual(len({a["settings_sha256"] for a in run["conditions"].values()}), 1)


class PlanningInputAndPreviewTests(unittest.TestCase):
    def test_cases_are_immutable_and_private_or_live_inputs_rejected(self):
        case = case_with(task())
        changed = case.context()
        changed["turns"][0]["message"] = "changed"
        self.assertNotEqual(changed, case.context())
        value = case.to_dict()
        value["gold"] = []
        with self.assertRaises(EvaluationError):
            FrozenPlanningCase.from_dict(value)
        value = case.to_dict()
        value["turns"][0]["task"]["source"] = "live"
        with self.assertRaisesRegex(EvaluationError, "frozen replay"):
            FrozenPlanningCase.from_dict(value)

    def test_tampered_case_hash_fails(self):
        case = case_with(task())
        with self.assertRaisesRegex(EvaluationError, "hash mismatch"):
            FrozenPlanningCase(case.case_id, "0" * 64, case._json).context()

    def test_preview_has_no_model_or_verification_output(self):
        with tempfile.TemporaryDirectory() as directory:
            result = preview_fixed_workflow(task("margin", "operating_income"), DATA, output_dir=directory)
            self.assertEqual(result["status"], "preview_only")
            self.assertEqual(result["stop_reason"], "synthesis_required")
            self.assertEqual(result["model_calls"], 0)
            self.assertFalse(result["context"]["claims"])
            self.assertFalse(result["context"]["reports"])
            self.assertFalse(result["truth_scored"])
            self.assertEqual(result["context"]["calculations"][0]["operation"], "margin")

    def test_preview_honors_tool_and_planner_budget(self):
        for kwargs, reason in [({"max_tool_calls": 1}, "tool_budget_exhausted"),
                               ({"max_planner_steps": 1}, "planner_budget_exhausted")]:
            with tempfile.TemporaryDirectory() as directory:
                result = preview_fixed_workflow(task(), DATA, output_dir=directory, **kwargs)
                self.assertEqual(result["stop_reason"], reason)
                self.assertEqual(result["tool_calls"], 1)
                self.assertEqual(result["model_calls"], 0)

    def test_preview_freezes_generic_market_input_without_sec_company_allowlist(self):
        from tests.test_market import sample
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / "market.json"
            market.write_text(json.dumps(sample()))
            public = task("price", metric=None, entity="INDEPENDENT",
                          window={"start": "2025-01-31", "end": "2025-02-03"})
            public["cutoff"] = "2025-02-03T17:00:00Z"
            result = preview_fixed_workflow(public, DATA, output_dir=root / "preview",
                tools_factory=lambda store, source, cache: ToolRegistry(store, source, cache_dir=cache, market_data=market))
            self.assertEqual(result["stop_reason"], "synthesis_required")
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["context"]["calculations"][0]["value"], 10)
            self.assertEqual(result["context"]["calculations"][0]["entity"], "INDEPENDENT")
            self.assertTrue((root / "preview" / "inputs" / "market_data.json").exists())
            self.assertEqual((root / "preview" / "inputs" / "market_data.json").read_bytes(), market.read_bytes())
            self.assertIn("market_data", result["tool_settings"]["extra_artifacts"])


if __name__ == "__main__":
    unittest.main()
