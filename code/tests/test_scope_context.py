import copy
from pathlib import Path
import tempfile
import unittest

from verifin.context import select_history
from verifin.engine import Budget, ResearchEngine, session_lock
from verifin.scope import edit_scope, validate_scope
from verifin.storage import SessionStore
from verifin.usage import session_usage
from tests.test_engine import ScriptedModel, FixtureTools, SCOPE, FACT, CLAIM, RESEARCH, REVIEW


class ScopeContextTests(unittest.TestCase):
    def test_scope_edit_during_clarification_invalidates_the_prior_review(self):
        from tests.test_engine import full_script
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.sqlite3"
            store = SessionStore(path)
            sid = store.create_session()["id"]
            script = full_script()[:-1] + [("planner", {"action": "clarify", "question": "Confirm period?"})]
            first = ResearchEngine(store, ScriptedModel(script), FixtureTools()).ask(sid, "Revenue?")
            self.assertTrue(first["episode"]["reviewed"])
            session = store.get_session(sid)
            edit_scope(store, sid, {**SCOPE, "period_start": "2024-07-01", "period_end": "2024-09-30"}, expected_version=session["scope_version"])
            store.close()
            store = SessionStore(path)
            try:
                result = ResearchEngine(store, ScriptedModel([("planner", {"action": "finish"})]), FixtureTools()).ask(sid, "Use the edited period")
                self.assertFalse(result["report"]["verification_available"])
                self.assertEqual("partial", result["status"])
                self.assertTrue(all(c["status"] == "Pending Verification" for c in result["report"]["claims"]))
                self.assertEqual(first["episode"]["id"], result["episode"]["id"])
            finally:
                store.close()

    def test_explicit_scope_edit_invalidates_dependencies_preserves_report_and_rejects_lost_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "session.sqlite3")
            try:
                sid = store.create_session()["id"]
                current = store.update_scope(sid, SCOPE)
                store.put_evidence(sid, FACT)
                store.put_claim(sid, {**CLAIM, "scope_version": current["scope_version"], "status": "Supported"})
                old = store.save_report(sid, {"claims": [CLAIM], "request": "Original"})
                updated = edit_scope(store, sid, {**SCOPE, "period_start": "2024-07-01", "period_end": "2024-09-30"}, expected_version=current["scope_version"])
                self.assertGreater(updated["scope_version"], current["scope_version"])
                self.assertTrue(store.get_context(sid)["claims"][0]["stale"])
                self.assertEqual(old, store.get_report(sid, old["version"]))
                with self.assertRaisesRegex(ValueError, "changed"):
                    edit_scope(store, sid, SCOPE, expected_version=current["scope_version"])
                with session_lock(store, sid), self.assertRaisesRegex(ValueError, "running"):
                    edit_scope(store, sid, SCOPE, expected_version=updated["scope_version"])
            finally:
                store.close()

    def test_invalid_scope_cannot_silently_change_financial_dates_or_requirements(self):
        for patch in ({"period_start": "2025-02-30"}, {"period_start": "2026-01-01"},
                      {"cutoff": "2025-01-01T12:00:00"}, {"entities": "MSFT"},
                      {"requirements": [{"id": "x", "text": "A"}, {"id": "x", "text": "B"}]},
                      {"cutoff": False}, {"extra": float("nan")}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_scope({**SCOPE, **patch})

    def test_retrieves_old_relevant_turn_with_reply_and_keeps_recent_context(self):
        messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Unrelated discussion {i}"} for i in range(80)]
        messages[4]["content"] = "Explain the acquisition assumption for Contoso."
        messages[5]["content"] = "The acquisition assumption requires a source."
        reports = [{"version": i, "request": "Other"} for i in range(1, 10)]
        reports[0]["request"] = "Contoso acquisition assumption"
        original = copy.deepcopy(messages)
        selected = select_history(messages, reports, request="Revisit the Contoso acquisition assumption", scope={})
        self.assertTrue({4, 5, 79} <= set(selected["message_indices"]))
        self.assertLessEqual(len(selected["message_indices"]), 24)
        self.assertEqual([1, 8, 9], selected["report_versions"])
        self.assertEqual(original, messages)
        self.assertFalse(selected["semantic_summary"])

    def test_fixed_planner_uses_real_shared_research_and_review_without_fake_model_calls(self):
        class Fixed:
            def __init__(self):
                self.actions = iter([{"action": "tool", "tool": "financial_facts", "arguments": {}, "scope": SCOPE},
                                     {"action": "research"}, {"action": "finish"}])
            def decide(self, context):
                return next(self.actions)
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "db.sqlite3")
            try:
                sid = store.create_session()["id"]
                model = ScriptedModel([("researcher", RESEARCH), ("critic", REVIEW)])
                result = ResearchEngine(store, model, FixtureTools(), planner=Fixed()).ask(sid, "Revenue?")
                self.assertEqual("complete", result["status"])
                self.assertEqual(2, result["episode"]["model_calls"])
                self.assertEqual(3, result["episode"]["planner_steps"])
                self.assertEqual(2, result["episode"]["usage"]["total_tokens_reported_calls"])
                episodes = store.list_episode_summaries(sid)
                self.assertEqual(1, len(episodes))
                usage = session_usage(episodes)
                self.assertEqual(2, usage["model_calls"])
                self.assertEqual(20, usage["total_tokens"])
                self.assertFalse(usage["token_usage_complete"])
                other = store.create_session()["id"]
                self.assertEqual([], store.list_episode_summaries(other))
            finally:
                store.close()

    def test_planner_steps_are_bounded_even_when_fixed_planner_does_not_call_model(self):
        class Fixed:
            def decide(self, context):
                return {"action": "tool", "tool": "financial_facts", "arguments": {}, "scope": SCOPE}
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "db.sqlite3")
            try:
                sid = store.create_session()["id"]
                result = ResearchEngine(store, ScriptedModel([]), FixtureTools(), planner=Fixed()).ask(sid, "Revenue?", budget=Budget(max_planner_steps=2))
                self.assertEqual("planner_budget_exhausted", result["episode"]["stop_reason"])
                self.assertEqual(0, result["episode"]["model_calls"])
                self.assertEqual(2, result["episode"]["planner_steps"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
