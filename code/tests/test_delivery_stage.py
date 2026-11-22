"""Delivery stages reflect this archived draft and only its own local receipts."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_engine import FACT, FixtureTools, ScriptedModel, full_script
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine
from verifin.model import ModelError, OutputContractError, ProviderOutputError
from verifin.reporting import render_markdown, report_view
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


class DeliveryStageTests(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore(":memory:")
        self.addCleanup(self.store.close)
        self.sid = self.store.create_session()["id"]

    def test_current_draft_uses_only_a_returned_contract_failure(self):
        script = full_script()
        for error, stage in (
            (OutputContractError("bad receipt", "unknown_reference"), "critic_receipt_invalid"),
            (ModelError("connection failed"), "draft_unverified"),
            (ProviderOutputError("max_output_tokens"), "draft_unverified"),
        ):
            with self.subTest(stage=stage, error=error):
                sid = self.store.create_session()["id"]
                model = ScriptedModel([*script[:3], ("critic", error)])
                r = ResearchEngine(self.store, model, FixtureTools()).ask(sid, "Revenue?")["report"]
                self.assertFalse(r["verification_available"])
                self.assertEqual(r["summary_kind"], "unverified_researcher_draft")
                self.assertEqual(r["delivery_stage"], stage)
                self.assertEqual(r.get("critic_receipt_error"), "unknown_reference" if isinstance(error, OutputContractError) else None)
                self.assertEqual(r["episode_material_ids"], {"evidence": [FACT["id"]], "calculations": []})
                self.assertEqual(r, self.store.get_report(sid, r["version"]))
                # Earlier archives already contain the delivery stage, but omit the length diagnostic.
                archived = {k: v for k, v in r.items() if k != "critic_output_error"}
                view = self.store.report_with_stage(sid, archived)
                expected = "max_output_tokens" if isinstance(error, ProviderOutputError) else None
                self.assertEqual(view.get("critic_output_error"), expected)
                self.assertEqual(view, r)

    def test_no_committed_draft_in_new_turn_is_independent_of_history(self):
        script = full_script()
        prior = ResearchEngine(self.store, ScriptedModel(script), FixtureTools()).ask(self.sid, "Revenue?")["report"]
        self.assertEqual(prior["delivery_stage"], "reviewed")
        fail = ScriptedModel([("planner", ModelError("fixture"))])
        r = ResearchEngine(self.store, fail, FixtureTools()).ask(self.sid, "Any update?")["report"]
        self.assertEqual(r["delivery_stage"], "no_draft")
        self.assertEqual(r["summary_kind"], "see_report_findings")
        self.assertEqual(r["episode_material_ids"], {"evidence": [], "calculations": []})
        self.assertIn(FACT["id"], {item["id"] for item in r["evidence"]})

    def test_failed_draft_checkpoint_does_not_publish_rolled_back_summary(self):
        engine = ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools())
        checkpoint = engine._checkpoint
        def fail_once(sid, episode):
            if episode.get("draft_saved") and episode["status"] == "running":
                raise ValueError("checkpoint failed")
            return checkpoint(sid, episode)
        with patch.object(engine, "_checkpoint", side_effect=fail_once):
            r = engine.ask(self.sid, "Revenue?")["report"]
        self.assertEqual(r["delivery_stage"], "no_draft")
        self.assertEqual(r["claims"], [])
        self.assertEqual(r["summary_kind"], "see_report_findings")

    def test_empty_successful_draft_is_distinct_from_a_failed_researcher(self):
        script = full_script()
        for response, expected in ((ModelError("fixture"), "no_draft"),
                ({"claims": [], "answered_requirement_ids": [], "unanswered_requirement_ids": ["req:revenue"],
                  "summary": "The answer is missing."}, "draft_unverified")):
            with self.subTest(expected=expected):
                sid = self.store.create_session()["id"]
                model = ScriptedModel([*script[:2], ("researcher", response)])
                result = ResearchEngine(self.store, model, FixtureTools()).ask(sid, "Revenue?", budget=Budget(max_model_calls=3))
                self.assertEqual(result["report"]["delivery_stage"], expected)
                self.assertEqual(result["report"]["claims"], [])

    def test_restored_verified_draft_takes_precedence_over_failed_revised_receipt(self):
        script = full_script()
        redraft = {**script[2][1], "summary": "A later draft."}
        bad_receipt = ("critic", OutputContractError("invalid receipt", "invalid_critic_json"))
        model = ScriptedModel([*script[:-1], script[1], ("researcher", redraft), bad_receipt, bad_receipt])
        report = ResearchEngine(self.store, model, FixtureTools()).ask(self.sid, "Revenue?")["report"]
        self.assertEqual([role for role, ctx in model.calls][-2:], ["critic", "critic"])
        self.assertTrue(report["verification_available"])
        self.assertEqual(report["delivery_stage"], "reviewed")
        self.assertEqual(report["summary"], script[2][1]["summary"])
        self.assertNotIn("critic_receipt_error", report)

    def test_archived_receipt_tracks_request_version_and_publication_time(self):
        store, sid = self.store, self.sid
        request, episode = "Which period?", "old"
        claim = {"id": "c", "version": 1, "created_at": "2025-01-01T00:00:01+00:00"}
        record = store.put_evidence(sid, FACT)
        report = {"episode_id": episode, "request": request, "scope_version": 2, "summary": "Draft.",
                  "verification_available": False, "claims": [claim], "evidence": [record], "calculations": []}
        def event(kind, **fields):
            store.add_event(sid, {"type": kind, "episode_id": episode, **fields})
        def checkpoint(text, summary="", claim_ids=None):
            event("episode_checkpoint", episode={"id": episode, "request": text, "summary": summary,
                       "active_claim_ids": claim_ids or [], "draft_scope_version": 2})
        checkpoint("Earlier question")
        event("tool_call", ok=True, record_versions={FACT["id"]: 1})
        checkpoint(request)
        event("model_call", role="researcher", ok=True)
        checkpoint(request, "Draft.", ["c"])
        event("step_started", role="critic")
        event("model_call", role="critic", ok=False, contract_error="unknown_issue_category")
        original = store.save_report(sid, report)
        # Another result with the same episode ID arrived after the archive.
        event("model_call", role="critic", ok=True)
        event("tool_call", ok=True, record_versions={FACT["id"]: 1})
        view = store.report_with_stage(sid, original)
        self.assertEqual(view["delivery_stage"], "critic_receipt_invalid")
        self.assertEqual(view["episode_material_ids"], {"evidence": [], "calculations": []})
        self.assertEqual(store.get_report(sid, 1), original)
        for other in (
            {**original, "scope_version": 3},
            {**original, "summary": "Revised."},
            {**original, "claims": [{**claim, "version": 2, "created_at": "2999-01-01T00:00:00+00:00"}]},
        ):
            self.assertEqual(store.report_with_stage(sid, other)["delivery_stage"], "draft_unverified")

    def test_failed_legacy_ingestion_cannot_satisfy_the_current_material_set(self):
        store, sid = self.store, self.sid
        record = store.put_evidence(sid, FACT)
        store.add_event(sid, {"type": "episode_checkpoint", "episode": {"id": "old", "request": "Sales?"}})
        store.add_event(sid, {"type": "tool_call", "episode_id": "old", "ok": True,
                              "observation": {"evidence": [FACT], "calculations": []}})
        store.add_event(sid, {"type": "tool_ingestion_error", "episode_id": "old"})
        archived = store.save_report(sid, {"episode_id": "old", "request": "Sales?", "evidence": [record]})
        self.assertEqual(store.report_with_stage(sid, archived)["episode_material_ids"]["evidence"], [])

    def test_materials_require_the_delivered_record_version(self):
        r = ResearchEngine(self.store, ScriptedModel(full_script()), FixtureTools()).ask(self.sid, "Revenue?")["report"]
        r = copy.deepcopy(r)
        r.pop("episode_material_ids")
        r["evidence"][0]["version"] += 1
        self.assertEqual([], self.store.report_with_stage(self.sid, r)["episode_material_ids"]["evidence"])


    def test_reopened_q1_draft_keeps_its_sources_and_marks_budget_interruption(self):
        question = ("把分析期间改为微软 FY2025 Q1（截至 2024 年 9 月 30 日）。"
                    "这一季的营收、营业利润和营业利润率分别是多少？请给出出处和算式。")
        clock = [0.0]
        dates = {"period_start": "2024-07-01", "period_end": "2024-09-30"}

        class Q1Draft:
            def complete(self, role, ctx):
                if role == "researcher":
                    amounts = {r["metric"]: r for r in ctx["evidence"] if r.get("period_end") == dates["period_end"]}
                    margin, = ctx["calculations"]
                    text = (f"营收 {amounts['revenue']['value'] / 10**8:.2f} 亿美元，"
                            f"营业利润 {amounts['operating_income']['value'] / 10**8:.2f} 亿美元，"
                            f"营业利润率 {margin['value']:.2f}%。")
                    clock[0] = 241
                    return {"summary": text, "claims": [{"id": "q1", "text": text, "constraints": {},
                            "requirement_ids": ["q1-lookup"], "evidence_ids": [r["id"] for r in amounts.values()],
                            "calculation_ids": [margin["id"]]}], "answered_requirement_ids": ["q1-lookup"],
                            "unanswered_requirement_ids": []}
                if role != "planner":
                    raise AssertionError("Critic should not start after the deadline")
                available = {r["metric"]: r["id"] for r in ctx["evidence"] if r.get("period_end") == dates["period_end"]}
                for metric in ("revenue", "operating_income"):
                    if metric not in available:
                        return {"action": "tool", "tool": "financial_facts",
                                "arguments": {"entity": "MSFT", "metric": metric, **dates},
                                "scope": dates, "requirements": [{"id": "q1-lookup", "text": question}]}
                if not ctx["calculations"]:
                    return {"action": "tool", "tool": "calculate", "arguments": {
                        "operation": "margin", "operand_ids": [available["operating_income"], available["revenue"]]}}
                return {"action": "research"}

        with TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite"
            with SessionStore(path) as store:
                sid = store.create_session()["id"]
                first = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(
                    sid, "What was Microsoft's FY2025 Q2 revenue?")["report"]
                self.assertEqual(first["delivery_stage"], "reviewed")
            with SessionStore(path) as store:
                tools = ToolRegistry(store, DATA / "replay", cache_dir=Path(directory) / "cache")
                report = ResearchEngine(store, Q1Draft(), tools, clock=lambda: clock[0], auto_skills=False).ask(
                    sid, question, budget=Budget(16, 16, 240))["report"]
                self.assertEqual(store.get_report(sid, 1), first)
                self.assertEqual(store.get_report(sid, 2), report)
        self.assertEqual((report["status"], report["stop_reason"], report["delivery_stage"]),
                         ("partial", "time_budget_exhausted", "draft_unverified"))
        self.assertFalse(report["verification_available"])
        markdown = render_markdown(report)
        for text in ("时间预算已用完，尚未完成 Critic 核验", "未核验草稿", "655.85", "305.52", "46.58"):
            self.assertIn(text, markdown)
        self.assertTrue(all(row["status"] == "draft_unverified" for row in report_view(report)["question_answers"]))
        self.assertEqual(len(report["episode_material_ids"]["evidence"]), 2)


    def test_chinese_margin_comparison_shows_missing_cutoff_after_planner_failure(self):
        question = ("刚才算出微软 FY2025 Q2 的营业利润率是 45.46%。和上年同期相比变化了多少个百分点？"
                    "微软在财报中披露了哪些原因？请引用原文，并标明哪些原因没有披露。")
        args = {"entity": "MSFT", "period_start": "2024-10-01", "period_end": "2024-12-31"}
        script = [
            ("planner", {"action": "tool", "tool": "reconcile_operating_margin", "arguments": args,
                         "scope": {"entities": ["MSFT"]}, "requirements": [{"id": "compare", "text": question}]}),
            ("planner", ModelError("uncategorized fixture")),
        ]
        with TemporaryDirectory() as directory:
            tools = ToolRegistry(self.store, DATA / "replay", cache_dir=Path(directory) / "cache")
            report = ResearchEngine(self.store, ScriptedModel(script), tools).ask(self.sid, question)["report"]
        self.assertEqual((report["stop_reason"], report["delivery_stage"]), ("execution_error", "no_draft"))
        self.assertEqual(report["tool_fault"]["tool"], "reconcile_operating_margin")
        self.assertIn("同比比较需要明确的信息截止日", report_view(report)["stage_message"])
        self.assertFalse(report["verification_available"])

    def test_same_question_retains_reviewed_answer_after_revised_draft_hits_deadline(self):
        script = full_script()
        revised = {**script[2][1], "summary": "An unreviewed revision."}
        clock = [0.0]
        researches = []
        def advance(role, context):
            if role == "researcher":
                researches.append(role)
                if len(researches) == 2:
                    clock[0] = 241
        model = ScriptedModel([*script[:-1], script[1], ("researcher", revised)], callback=advance)
        report = ResearchEngine(self.store, model, FixtureTools(), clock=lambda: clock[0]).ask(
            self.sid, "Revenue?", budget=Budget(16, 16, 240))["report"]
        self.assertEqual((report["stop_reason"], report["delivery_stage"]), ("time_budget_exhausted", "reviewed"))
        self.assertTrue(report["verification_available"])
        self.assertEqual(report["summary"], script[2][1]["summary"])
        self.assertNotIn(revised["summary"], render_markdown(report))
