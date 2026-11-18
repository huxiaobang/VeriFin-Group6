"""Fixed-workflow protocol tests; scripted synthesis is not model evaluation."""
import copy
from pathlib import Path
import tempfile
import unittest

from tests.critic_fixture import fixture_verdicts
from verifin.engine import Budget, ResearchEngine
from verifin.model import validate_response
from verifin.planning import FixedWorkflowPlanner, TaskSpec, TaskSpecError, select_relative_period
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


DATA = Path(__file__).resolve().parents[1] / "data" / "replay"
Q1 = {"start": "2024-07-01", "end": "2024-09-30"}
Q2 = {"start": "2024-10-01", "end": "2024-12-31"}


def task(kind="fact", metric="revenue", periods=None, entity="MSFT", **extra):
    item = {"id": "requested", "text": "Requested financial observation", "kind": kind,
            "entities": [entity], **extra}
    if metric:
        item["metric"] = metric
    if kind != "price":
        item["periods"] = periods or [Q2]
    return {"version": 1, "cutoff": "2025-01-31", "source": "replay", "items": [item]}


class ProtocolModel:
    """Only exercises interfaces. Its planner uses the same deterministic fixture.

    The paired runner must label this scripted mode, never infer E2 quality from
    the fixture. Financial values come solely from real returned records.
    """
    mode = "scripted_protocol_fixture"
    model = "not-an-llm-protocol-fixture"
    base_url = None
    max_output_tokens = 3000
    max_context_chars = 500_000
    enable_thinking = False
    timeout = 30

    def __init__(self, *, fail_role=None, issue_once=False):
        self.planner = FixedWorkflowPlanner()
        self.calls = []
        self.last_usage = {}
        self.fail_role = fail_role
        self.issue_once = issue_once
        self.reviews = 0

    def complete(self, role, context):
        self.calls.append((role, copy.deepcopy(context)))
        self.last_usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        if role == self.fail_role:
            raise RuntimeError("Deliberate fixture provider failure")
        if role == "planner":
            return validate_response(role, self.planner.decide(context), context)
        if role == "critic":
            self.reviews += 1
            issues = []
            if self.issue_once and self.reviews == 1 and context["claims"]:
                issues = [{"target_id": context["claims"][0]["id"], "issue_type": "causal_overstatement",
                           "evidence_refs": [], "missing_support": "Fixture tests rewrite scheduling only",
                           "proposed_action": "Remove the asserted cause"}]
            review = {"issues": issues,
                "reviewed_claim_ids": [c["id"] for c in context["claims"]],
                "reviewed_requirement_ids": [r["id"] for r in context["requirements"]]}
            if context.get("request_parts") and len(context["claims"]) == 1:
                claim, = context["claims"]
                review["request_coverage"] = [{"part":"part-1","status":"answered","claim_id":claim["id"],
                                               "answer_sentence":claim["text"]}]
            return validate_response(role, fixture_verdicts(role, review, context), context)
        if role != "researcher":
            raise AssertionError("Unexpected model role")
        claims, answered, unanswered = [], [], []
        for item in context["scope"]["planning_task"]["items"]:
            kind = item["kind"]
            records = context["calculations"] if kind in {"margin", "growth", "compare_periods", "price"} else context["evidence"]
            expected_metric = {"margin": item.get("metric", "") + "_margin",
                               "growth": item.get("metric", "") + "_growth",
                               "compare_periods": item.get("metric", "") + "_difference", "price": "price_return"}.get(kind, item.get("metric"))
            records = [r for r in records if not r.get("stale") and (r.get("metric") == expected_metric if kind != "explanation" else r.get("kind") == "document")]
            if not records:
                unanswered.append(item["id"])
                continue
            # This fixture intentionally does not judge natural-language support.
            # It only emits a source-bound scalar or original passage reference.
            record = sorted(records, key=lambda r: r.get("period_end") or "")[-1]
            calc = record in context["calculations"]
            claim = {"id": "fixture:" + item["id"], "text": "Protocol fixture observation: " + str(record.get("value", record.get("text", "")))[:150],
                     "evidence_ids": [] if calc else [record["id"]], "calculation_ids": [record["id"]] if calc else [],
                     "requirement_ids": [item["id"]], "constraints": {k: record[k] for k in ("entity", "metric", "period_start", "period_end", "unit") if record.get(k) is not None}}
            if "value" in record:
                claim.update(asserted_value=record["value"], asserted_unit=record["unit"])
            claims.append(claim)
            answered.append(item["id"])
        return validate_response(role, {"claims": claims, "answered_requirement_ids": answered,
            "unanswered_requirement_ids": unanswered, "summary": "SCRIPTED PROTOCOL FIXTURE; not an assessed financial answer."}, context)


class TaskSpecTests(unittest.TestCase):
    def test_public_spec_is_immutable_and_round_trips(self):
        value = task()
        spec = TaskSpec.from_dict(value)
        value["items"][0]["metric"] = "assets"
        self.assertEqual(spec.to_dict()["items"][0]["metric"], "revenue")
        self.assertEqual(TaskSpec.from_dict(spec.to_dict()).sha256, spec.sha256)
        self.assertEqual(spec.scope()["requirements"], [{"id": "requested", "text": "Requested financial observation"}])

    def test_gold_and_tool_sequence_rejected_at_any_depth(self):
        for key in ("gold", "expected_answer", "gold_actions", "tool_sequence", "annotations"):
            value = task()
            value["items"][0][key] = "private"
            with self.subTest(key=key), self.assertRaises(TaskSpecError):
                TaskSpec.from_dict(value)

    def test_ambiguous_periods_and_undefined_metrics_require_resolution(self):
        for changes in ({"periods": [{"fiscal_quarter": "FY25Q2"}]}, {"metric": "goodness"},
                        {"entities": []}, {"periods": [{"start": "2024-12-31", "end": "2024-01-01"}]}):
            value = task()
            value["items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(TaskSpecError):
                TaskSpec.from_dict(value)

    def test_instant_and_explicit_taxonomy_concept_periods(self):
        TaskSpec.from_dict(task(metric="assets", periods=[{"end": "2024-12-31"}]))
        TaskSpec.from_dict(task(metric="us-gaap:Assets", periods=[{"end": "2024-12-31"}]))
        with self.assertRaises(TaskSpecError):
            TaskSpec.from_dict(task(metric="assets"))
        with self.assertRaises(TaskSpecError):
            TaskSpec.from_dict(task(metric="revenue", periods=[{"end": "2024-12-31"}]))

    def test_relative_period_filters_cumulative_and_detects_ambiguous_starts(self):
        rows = [{"period_start": "2024-07-01", "period_end": "2024-12-31"},
                {"period_start": "2024-10-01", "period_end": "2024-12-31"},
                {"period_start": "2024-07-01", "period_end": "2024-09-30"}]
        selector = {"relative": "latest", "kind": "quarter", "offset": 0}
        exact, problem = select_relative_period(selector, rows)
        self.assertEqual(exact, Q2)
        self.assertIsNone(problem)
        self.assertEqual(select_relative_period({**selector, "offset": 1}, rows)[0], Q1)
        rows.append({"period_start": "2024-10-02", "period_end": "2024-12-31"})
        self.assertIsNone(select_relative_period(selector, rows)[0])


class FixedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / "session.sqlite")
        self.sid = self.store.create_session()["id"]
        self.tools = ToolRegistry(self.store, DATA, cache_dir=self.root / "cache")
        self.model = ProtocolModel()
        self.engine = ResearchEngine(self.store, self.model, self.tools, planner=FixedWorkflowPlanner())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_task(self, value, budget=None):
        self.store.update_scope(self.sid, TaskSpec.from_dict(value).scope())
        return self.engine.ask(self.sid, "Public fixture request", budget=budget or Budget(max_model_calls=16, max_tool_calls=20))

    def tools_in(self, result):
        return [e for e in self.store.events(self.sid) if e.get("type") == "tool_call" and e.get("episode_id") == result["episode"]["id"]]

    def test_exact_fact_calls_only_shared_synthesis_and_critic_models(self):
        result = self.run_task(task(), Budget(max_model_calls=2))
        self.assertEqual(result["status"], "complete", result["episode"])
        self.assertEqual([r for r, _ in self.model.calls], ["researcher", "critic"])
        self.assertEqual(result["episode"]["model_calls"], 2)
        self.assertEqual(result["episode"]["planner_steps"], 4)
        self.assertEqual([e["tool"] for e in self.tools_in(result)], ["discover_company", "financial_facts"])

    def test_latest_quarter_resolved_from_source_and_cutoff(self):
        result = self.run_task(task(periods=[{"relative": "latest", "kind": "quarter"}]))
        self.assertEqual(result["status"], "complete", result["episode"])
        calls = self.tools_in(result)
        self.assertNotIn("period_end", calls[1]["arguments"])
        self.assertEqual(calls[2]["arguments"]["period_start"], "2024-10-01")
        self.assertEqual(calls[2]["arguments"]["period_end"], "2024-12-31")

    def test_margin_growth_and_price_are_real_saved_calculations(self):
        for value, operation in [(task("margin", "operating_income"), "margin"),
                                  (task("growth", periods=[Q1, Q2]), "growth"),
                                  (task("price", metric=None, window={"start": "2025-01-28", "end": "2025-01-30"}), "price_return")]:
            with self.subTest(operation=operation):
                result = self.run_task(value)
                self.assertEqual(result["status"], "complete", result["episode"])
                calculations = result["report"]["calculations"]
                self.assertTrue(any(c["operation"] == operation and not c["stale"] for c in calculations))

    def test_followup_reuses_evidence_and_reopens_new_scope(self):
        first = self.run_task(task("margin", "operating_income"))
        snapshot = copy.deepcopy(first["report"])
        second = self.run_task(task("growth", periods=[Q1, Q2]))
        self.assertEqual(second["status"], "complete", second["episode"])
        self.assertEqual([e["tool"] for e in self.tools_in(second)], ["financial_facts", "calculate"])
        self.assertEqual(self.store.list_reports(self.sid)[0], snapshot)
        self.assertEqual(self.tools_in(second)[0]["arguments"]["period_end"], "2024-09-30")

    def test_identical_followup_needs_no_tools_but_rechecks_new_claims(self):
        self.run_task(task("margin", "operating_income"))
        self.model.calls.clear()
        second = self.run_task(task("margin", "operating_income"))
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["episode"]["tool_calls"], 0)
        self.assertEqual([role for role, _ in self.model.calls], ["researcher", "critic"])

    def test_name_resolution_applies_scope_before_calculation(self):
        result = self.run_task(task("margin", "operating_income", entity="Microsoft"))
        self.assertEqual(result["status"], "complete", result["episode"])
        self.assertEqual(result["report"]["scope"]["entities"], ["MSFT"])
        self.assertFalse(result["report"]["calculations"][0]["stale"])

    def test_missing_issuer_is_explicit_partial_without_company_allowlist(self):
        result = self.run_task(task(entity="UNSEEN-ISSUER-XYZ"))
        self.assertEqual(result["status"], "partial")
        calls = self.tools_in(result)
        self.assertEqual(calls[0]["arguments"]["query"], "UNSEEN-ISSUER-XYZ")
        self.assertFalse(calls[0]["ok"])
        self.assertEqual(result["report"]["unanswered"][0]["id"], "requested")

    def test_source_failure_counts_and_no_annual_substitute(self):
        result = self.run_task(task(periods=[{"start": "2024-10-02", "end": "2024-12-31"}]))
        self.assertEqual(result["status"], "partial")
        calls = self.tools_in(result)
        self.assertTrue(any(e["tool"] == "financial_facts" and not e["ok"] for e in calls))
        self.assertEqual(result["episode"]["tool_calls"], len(calls))
        # Searching originals may expose real cells for other periods. The typed
        # fact for the requested start date is still missing and must stay so.
        self.assertFalse([e for e in result["report"]["evidence"] if e["kind"] == "financial_fact"
                          and e.get("period_start") == "2024-10-02" and e.get("period_end") == "2024-12-31"])
        self.assertFalse(result["report"]["calculations"])
        self.assertIn("search_documents", [e["tool"] for e in calls])

    def test_tool_and_planner_limits_stop_before_unbudgeted_action(self):
        for budget, reason in [(Budget(max_tool_calls=1), "tool_budget_exhausted"),
                               (Budget(max_planner_steps=1), "planner_budget_exhausted")]:
            with self.subTest(reason=reason):
                result = self.run_task(task(), budget)
                self.assertEqual(result["episode"]["stop_reason"], reason)
                self.assertEqual(result["episode"]["tool_calls"], 1)
                self.assertEqual(result["episode"]["model_calls"], 0)

    def test_model_failure_keeps_preverification_candidate_pending(self):
        self.model.fail_role = "critic"
        result = self.run_task(task())
        self.assertEqual(result["episode"]["stop_reason"], "execution_error")
        self.assertEqual(result["episode"]["model_calls"], 2)
        self.assertFalse(result["report"]["verification_available"])
        self.assertEqual(result["report"]["claims"][0]["status"], "Pending Verification")

    def test_critic_issue_triggers_bounded_same_model_rewrite(self):
        self.model.issue_once = True
        result = self.run_task(task())
        self.assertEqual(result["status"], "complete", result["episode"])
        self.assertEqual([role for role, _ in self.model.calls], ["researcher", "critic", "researcher", "critic"])
        self.assertEqual(result["episode"]["tool_calls"], 2)

    def test_unresolved_input_clarifies_without_spending_model_calls(self):
        result = self.engine.ask(self.sid, "Is performance good?")
        self.assertEqual(result["status"], "waiting_for_clarification")
        self.assertEqual(result["episode"]["model_calls"], 0)
        result2 = self.engine.ask(self.sid, "Still unclear")
        self.assertEqual(result2["episode"]["id"], result["episode"]["id"])
        self.assertEqual(result2["status"], "waiting_for_clarification")
        self.assertEqual(result2["episode"]["planner_steps"], 2)

    def test_unresolved_relative_period_waits_for_changed_public_spec(self):
        public = task(periods=[{"relative": "latest", "kind": "quarter"}])
        public["cutoff"] = "1900-01-01"
        result = self.run_task(public)
        self.assertEqual(result["status"], "waiting_for_clarification")
        second = self.engine.ask(self.sid, "Please continue")
        self.assertEqual(second["status"], "waiting_for_clarification")
        self.assertEqual(second["episode"]["tool_calls"], result["episode"]["tool_calls"])
        self.assertEqual(second["episode"]["model_calls"], 0)

    def test_price_window_does_not_substitute_weekend_endpoints(self):
        result = self.run_task(task("price", metric=None, window={"start": "2025-01-26", "end": "2025-01-30"}))
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["report"]["calculations"])
        price_calls = [e for e in self.tools_in(result) if e["tool"] == "market_prices"]
        self.assertTrue(price_calls)
        self.assertTrue(all(c["arguments"]["start_date"] == "2025-01-26" and not c["ok"] for c in price_calls))

    def test_explanation_retrieves_original_passages(self):
        result = self.run_task(task("explanation", metric=None, periods=[{"end": "2024-12-31"}], query="cloud gross margin"))
        self.assertEqual(result["status"], "complete", result["episode"])
        self.assertEqual([e["tool"] for e in self.tools_in(result)], ["discover_company", "search_documents"])
        self.assertTrue(result["report"]["evidence"][0]["text"])
        self.assertTrue(result["report"]["evidence"][0]["locator"])

    def test_company_comparison_keeps_distinct_fiscal_dates(self):
        value = task("compare_companies")
        item = value["items"][0]
        item["entities"] = ["MSFT", "NVDA"]
        item.pop("periods")
        item["periods_by_entity"] = {"MSFT": [Q2], "NVDA": [{"start": "2024-07-29", "end": "2024-10-27"}]}
        result = self.run_task(value)
        self.assertEqual(set(result["report"]["scope"]["entities"]), {"MSFT", "NVDA"})
        self.assertEqual({e["period_end"] for e in result["report"]["evidence"]}, {"2024-12-31", "2024-10-27"})
        self.assertNotIn("calculate", [e["tool"] for e in self.tools_in(result)])


if __name__ == "__main__":
    unittest.main()
