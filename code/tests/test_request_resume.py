"""Request IDs and durable handoffs across actual interpreter exits."""
from contextlib import contextmanager
from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_engine import FixtureTools, ScriptedModel, full_script
from verifin.engine import Budget, ResearchEngine, session_lock
from verifin.storage import SessionStore
from verifin.submissions import model_binding, reserve_submission


QUESTION = "Revenue for MSFT fiscal Q2 2025?"
REQUEST = "restart-fixture"
LIMITS = Budget(max_model_calls=5, max_tool_calls=1, max_planner_steps=3)


def crash_worker(path, sid, stage):
    with SessionStore(path) as store:
        original = store.atomic_step

        @contextmanager
        def stop_after_commit():
            with original():
                yield
            if store._db.in_transaction:
                return
            frame = store.latest_event(sid, "python_checkpoint")
            observation = store.latest_event(sid, "tool_call")
            if frame and frame["stage"] == stage and (stage != "plan" or observation):
                os._exit(73)

        def stop_tool():
            if stage == "in_flight_tool":
                os._exit(73)

        def stop_model(role, _):
            if stage == "in_flight_critic" and role == "critic":
                os._exit(73)

        store.atomic_step = stop_after_commit
        ResearchEngine(store, ScriptedModel(full_script(), callback=stop_model), FixtureTools(callback=stop_tool)).ask(
            sid, QUESTION, budget=LIMITS, request_id=REQUEST)


class RequestResumeTests(unittest.TestCase):
    def new_database(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "session.sqlite"
        with SessionStore(path) as store:
            sid = store.create_session()["id"]
        return path, sid

    def crash(self, stage):
        path, sid = self.new_database()
        child = subprocess.run([sys.executable, "-B", "-m", "tests.test_request_resume",
                                "--crash", str(path), sid, stage], capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 73, child.stderr)
        return path, sid

    def test_handoffs_resume_without_replaying_committed_calls(self):
        for stage, model_calls, tool_calls in (("route", 1, 0), ("tool", 1, 0), ("plan", 1, 1), ("critic", 3, 1),
                                               ("reviewed", 4, 1), ("deliver", 5, 1)):
            with self.subTest(stage=stage):
                path, sid = self.crash(stage)
                with SessionStore(path) as store:
                    checkpoint = store.latest_event(sid, "episode_checkpoint")["episode"]
                    model, tools = ScriptedModel(full_script()[model_calls:]), FixtureTools()
                    engine = ResearchEngine(store, model, tools)
                    result = engine.ask(sid, request_id=REQUEST)
                    self.assertEqual(result["status"], "complete", result["episode"])
                    self.assertEqual(result["episode"]["id"], checkpoint["id"])
                    self.assertEqual(result["report"]["request_id"], REQUEST)
                    self.assertEqual(result["report"]["version"], 1)
                    self.assertTrue(result["report"]["verification_available"])
                    self.assertEqual(result["report"]["claims"][0]["status"], "Supported")
                    self.assertEqual(result["report"]["usage"]["model_calls"], 5)
                    self.assertEqual(result["report"]["usage"]["tool_calls"], 1)
                    self.assertEqual(result["report"]["usage"]["planner_steps"], 3)
                    self.assertEqual([role for role, _ in model.calls], [role for role, _ in full_script()[model_calls:]])
                    self.assertEqual(len(tools.calls), 1 - tool_calls)
                    self.assertEqual(store.latest_event(sid, "python_resume")["stage"], stage)
                    self.assertIsNone(store.get_research_handoff(sid, REQUEST))
                with SessionStore(path) as store:
                    offline = ResearchEngine(store, None, FixtureTools())
                    self.assertEqual(offline.ask(sid, request_id=REQUEST), result)
                    self.assertEqual(len(store.list_reports(sid)), 1)
                    self.assertEqual([m["role"] for m in store.get_context(sid)["messages"]], ["user", "assistant"])

    def test_unknown_tool_and_critic_attempts_finish_without_replaying(self):
        for stage, kind, last_calls in (("in_flight_tool", "tool", (1, 1)), ("in_flight_critic", "model", (4, 1))):
            with self.subTest(stage=stage):
                path, sid = self.crash(stage)
                with SessionStore(path) as store:
                    model, tools = ScriptedModel([]), FixtureTools()
                    result = ResearchEngine(store, model, tools).ask(sid, request_id=REQUEST)
                    self.assertEqual(result["episode"]["stop_reason"], "unknown_external_outcome")
                    self.assertEqual(result["status"], "partial")
                    self.assertEqual(result["report"]["request_id"], REQUEST)
                    self.assertEqual((result["episode"]["model_calls"], result["episode"]["tool_calls"]), last_calls)
                    self.assertEqual(store.latest_event(sid, "python_resume_blocked")["kind"], kind)
                    self.assertFalse(result["report"]["verification_available"])
                    self.assertFalse(model.calls)
                    self.assertFalse(tools.calls)

    def test_resume_keeps_cancellation_and_rejects_scope_changes(self):
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                path, sid = self.crash("critic")
                with SessionStore(path) as store:
                    if cancel:
                        store.request_cancel(sid)
                    else:
                        scope = store.get_session(sid)["scope"]
                        store.update_scope(sid, {**scope, "period_start": "2024-07-01", "period_end": "2024-09-30"})
                    model, tools = ScriptedModel([]), FixtureTools()
                    result = ResearchEngine(store, model, tools).ask(sid, request_id=REQUEST)
                    self.assertEqual(result["episode"]["stop_reason"], "cancelled" if cancel else "role_inputs_changed")
                    self.assertFalse(model.calls)
                    self.assertFalse(tools.calls)

    def test_pending_request_preserves_inputs_and_model_route(self):
        path, sid = self.new_database()
        old = ScriptedModel(full_script())
        old.base_url = "https://original.example/v1"
        old.model = "original"
        old.api_mode = "responses"
        old._api_key = "not-persisted"
        with SessionStore(path) as store, session_lock(store, sid):
            reserve_submission(store, sid, REQUEST, message=QUESTION, as_of="2025-01-31",
                               budget=asdict(LIMITS), max_context_chars=None, execution_backend="python-staged",
                               offline=False, reset_cancel=True, model_identity=model_binding(old))
        with SessionStore(path) as store:
            different = ScriptedModel([])
            different.model, different.api_mode, different.base_url = old.model, old.api_mode, "https://other.example/v1"
            with self.assertRaisesRegex(ValueError, "original model"):
                ResearchEngine(store, different, FixtureTools()).ask(sid, request_id=REQUEST)
            matching = ResearchEngine(store, old, FixtureTools())
            with self.assertRaisesRegex(ValueError, "different cutoff"):
                matching.ask(sid, request_id=REQUEST, as_of="2025-02-01")
            with self.assertRaisesRegex(ValueError, "different question"):
                matching.ask(sid, "Different question", request_id=REQUEST)
            result = matching.ask(sid, request_id=REQUEST, budget=Budget(max_model_calls=1))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["episode"]["budget"], asdict(LIMITS))
            self.assertEqual(result["report"]["scope"]["cutoff"], "2025-01-31")
            payload = str(store.get_submission(sid, REQUEST))
            self.assertNotIn("not-persisted", payload)
            self.assertNotIn(old.base_url, payload)

    def test_new_submission_and_clarification_get_distinct_receipts(self):
        path, sid = self.new_database()
        with SessionStore(path) as store:
            first = ResearchEngine(store, ScriptedModel([("planner", {"action": "clarify", "question": "Which date?"})]),
                                   FixtureTools()).ask(sid, QUESTION)
            first_id = first["episode"]["request_id"]
            self.assertEqual(first["status"], "waiting_for_clarification")
        with SessionStore(path) as store:
            resume = ResearchEngine(store, None, FixtureTools()).ask(sid, request_id=first_id)
            self.assertEqual(first, resume)
            second = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools()).ask(sid, "2024-12-31")
            self.assertNotEqual(first_id, second["episode"]["request_id"])
            self.assertEqual(first["episode"]["id"], second["episode"]["id"])
            self.assertEqual(store.get_submission(sid, first_id)["episode"]["status"], "waiting_for_clarification")
            third = ResearchEngine(store, ScriptedModel([("planner", {"action": "finish"})]), FixtureTools()).ask(
                sid, QUESTION)
            self.assertNotEqual(first_id, third["episode"]["request_id"])
            self.assertNotEqual(first["episode"]["id"], third["episode"]["id"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash":
        crash_worker(*sys.argv[2:])
    else:
        unittest.main()
