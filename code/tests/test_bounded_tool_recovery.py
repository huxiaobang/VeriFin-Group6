"""A rejected input can change the next action without losing an independent fact."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_engine import FACT, RESEARCH, REVIEW, SCOPE, FixtureTools, ScriptedModel
from tests.test_tools import DATA
from verifin.engine import Budget, ResearchEngine
from verifin.model import ModelClient
from verifin.reporting import render_markdown
from verifin.storage import SessionStore
from verifin.tools import ToolRegistry


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


class FrozenDocumentRecoveryTests(unittest.TestCase):
    DOCUMENT = "source_doc_d648a9d1ce6b6d52c5dbbf5f"
    SOURCE = "https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q2/press-release-webcast"
    QUESTION = "What is the source for Microsoft's FY2025 Q2 earnings release?"
    FIRST_ID = "span_a82592c28c002063252b1a4a"

    def run_reads(self, *, separate_window=True, auto_skills=True, live=False, scope_change=False, preview=False):
        first = {"document_id": self.DOCUMENT, "entity": "MSFT"}
        plans = [("planner", {"action": "tool", "tool": "read_document", "arguments": first}),
                 ("planner", {"action": "tool", "tool": "read_document",
                              "arguments": {**first, "offset": 0, "limit_chars": 4500},
                              **({"scope": {"period_end": "2024-12-31"}} if scope_change else {})})]
        if separate_window:
            plans.append(("planner", {"action": "tool", "tool": "read_document",
                                      "arguments": {**first, "offset": 4500, "limit_chars": 6000}}))
        claim_text = f"The Microsoft earnings release is available at {self.SOURCE}"
        claim = {"id": "release", "text": claim_text, "evidence_ids": [self.FIRST_ID], "calculation_ids": [],
                 "requirement_ids": ["source"], "constraints": {}}
        draft = {"claims": [claim], "summary": claim_text, "answered_requirement_ids": ["source"], "unanswered_requirement_ids": []}
        review = {"issues": [], "reviewed_claim_ids": ["release"], "reviewed_requirement_ids": ["source"]}

        class Model(ScriptedModel):
            def evidence_view(self, role, ctx):
                if not preview:
                    return ctx
                rows = [{**row, "text": row["text"][:100]} if row.get("kind") == "document" else row for row in ctx["evidence"]]
                return {**ctx, "evidence": rows}

        with tempfile.TemporaryDirectory() as folder, SessionStore(Path(folder) / "case.db") as store:
            sid = store.create_session()["id"]
            store.update_scope(sid, {"entities": ["MSFT"], "requirements": [{"id": "source", "text": self.QUESTION}]})
            registry = ToolRegistry(store, DATA, allow_network=live, cache_dir=Path(folder) / "cache")
            model = Model([*plans, ("researcher", draft), ("critic", review)])
            with patch("verifin.data.build_opener", side_effect=AssertionError("unexpected network request")), \
                    patch.object(registry, "execute", wraps=registry.execute) as dispatched:
                report = ResearchEngine(store, model, registry, auto_skills=auto_skills).ask(
                    sid, self.QUESTION, as_of="2025-02-01", budget=Budget(max_model_calls=len(plans) + 2))["report"]
            events = store.events(sid)
            tool_events = [e for e in events if e["type"] == "tool_call"]
            self.assertEqual(len(tool_events), len(dispatched.call_args_list))
            self.assertEqual(len(tool_events), report["usage"]["tool_calls"])
            return events, tool_events, model.calls, report

    def test_reuse_original_read_then_dispatch_a_different_window(self):
        events, tools, calls, _ = self.run_reads()
        self.assertEqual([c["arguments"].get("offset", 0) for c in tools], [0, 4500])
        skip, = (e for e in events if e["type"] == "tool_dispatch_skipped")
        self.assertEqual(skip["prior_observation_sha256"], tools[0]["observation_sha256"])
        self.assertEqual(skip["record_versions"], tools[0]["record_versions"])
        self.assertEqual(skip["next_offset"], 4500)
        recovery = calls[2][1]["episode"]["tool_recovery"]
        self.assertEqual(recovery["prior_observation_sha256"], tools[0]["observation_sha256"])
        self.assertIn("offset=4500", recovery["instruction"])
        checkpoint = next(e["episode"] for e in events if e["type"] == "episode_checkpoint"
                          and e["event_sequence"] == skip["event_sequence"] + 1)
        self.assertEqual(checkpoint["tool_calls"], 1)
        self.assertEqual(checkpoint["tool_recovery"], recovery)

    def test_reuse_preserves_last_two_model_calls_for_draft_and_review(self):
        _, tools, calls, report = self.run_reads(separate_window=False)
        self.assertEqual(len(tools), 1)
        self.assertEqual([role for role, _ in calls], ["planner", "planner", "researcher", "critic"])
        self.assertTrue(report["verification_available"])

    def test_scope_live_preview_and_evaluation_keep_dispatching_the_read(self):
        for options in ({"scope_change": True}, {"live": True}, {"preview": True}, {"auto_skills": False}):
            with self.subTest(options=options):
                events, tools, _, _ = self.run_reads(separate_window=False, **options)
                self.assertEqual(len(tools), 2)
                self.assertFalse(any(e["type"] == "tool_dispatch_skipped" for e in events))

    def test_frozen_source_verification_checks_provenance_and_yields_to_a_cached_revision(self):
        with tempfile.TemporaryDirectory() as folder, SessionStore(Path(folder) / "case.db") as store:
            registry = ToolRegistry(store, DATA, cache_dir=Path(folder) / "cache")
            sid = store.create_session()["id"]
            result = registry.execute("read_document", {"document_id": self.DOCUMENT, "entity": "MSFT"},
                                      sid, {"cutoff": "2025-02-01"})
            passage, = result["evidence"]
            self.assertTrue(registry.replay_document_unchanged(passage))
            with patch.object(registry.data, "cached_urls", return_value=[passage["source_url"]]):
                self.assertFalse(registry.replay_document_unchanged(passage))
            for path in (passage["snapshot_path"], passage["text_snapshot_path"]):
                with self.subTest(path=path), patch.dict(registry.data.manifest["files"][path], {"sha256": "0" * 64}):
                    self.assertFalse(registry.replay_document_unchanged(passage))
