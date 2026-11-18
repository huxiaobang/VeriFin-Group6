"""Returned observations survive cancellation, invalid sibling records and reopening."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_engine import FACT, FixtureTools, ScriptedModel, full_script
from verifin.engine import ResearchEngine
from verifin.storage import SessionStore


class SimulatedCrash(BaseException):
    pass


class StopHandoffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "session.sqlite"

    def test_in_flight_review_cannot_approve_another_draft_with_the_same_claim_id(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            def replace(role, ctx):
                if role != "critic":
                    return
                with SessionStore(self.path) as other:
                    fact = other.put_evidence(sid, dict(FACT, value=70_000_000_000, snapshot_hash="b"*64))
                    old = ctx["claims"][0]
                    new = {k:old[k] for k in ("id","requirement_ids","constraints","asserted_unit")}
                    other.put_claim(sid, {**new, "text":"Quarterly revenue was USD 70 billion.",
                                          "evidence_ids":[fact["id"]], "asserted_value":fact["value"],
                                          "status":"Pending Verification"})
            model = ScriptedModel(full_script(), callback=replace)
            report = ResearchEngine(store, model, FixtureTools()).ask(sid, "Revenue?")["report"]
            self.assertEqual("role_inputs_changed", report["stop_reason"])
            self.assertFalse(report["verification_available"])
            self.assertEqual("Pending Verification", report["claims"][0]["status"])
            self.assertEqual("critic", model.calls[-1][0])
            self.assertIsNone(store.latest_event(sid,"verification"))
            self.assertEqual("critic",store.latest_event(sid,"role_result_superseded")["role"])

    def test_reopening_identical_source_keeps_its_review_and_can_finish(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            repeat = ("planner", {"action":"tool", "tool":"financial_facts", "arguments":{"entity":"MSFT"}})
            model = ScriptedModel([*full_script()[:-1], repeat, full_script()[-1]])
            report = ResearchEngine(store, model, FixtureTools()).ask(sid, "Revenue?")["report"]
            self.assertEqual("complete", report["status"])
            self.assertEqual("Supported", report["claims"][0]["status"])
            self.assertEqual(1, sum(role == "critic" for role, _ in model.calls))

    def test_cancelled_after_first_researcher_return_keeps_draft_without_starting_critic(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            def cancel_on_draft(role, _context):
                if role == "researcher":
                    store.request_cancel(sid)
            model = ScriptedModel(full_script(), callback=cancel_on_draft)
            report = ResearchEngine(store, model, FixtureTools()).ask(sid, "Revenue?")["report"]
            self.assertEqual(["planner", "planner", "researcher"], [role for role, _ in model.calls])
            self.assertEqual("cancelled", report["stop_reason"])
            self.assertFalse(report["verification_available"])
            self.assertEqual("Pending Verification", report["claims"][0]["status"])
            self.assertIn("69.632", report["claims"][0]["text"])
            self.assertIsNone(store.latest_event(sid, "verification"))

    def test_rejected_batch_preserves_source_change_without_publishing_any_attached_record(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            source = {"source_document_id":"filing:test", "source_revision":"a"*64,
                      "source_observed_at":"2026-10-13T01:00:00Z",
                      "source_revision_authoritative":True, "source_url":FACT["source_url"]}
            old = dict(FACT, id="old", kind="document", text="Original disclosure", **source)
            store.put_evidence(sid, old)
            revised = dict(source, source_revision="b"*64, source_observed_at="2026-10-13T02:00:00Z")
            event = {"type":"tool_call", "episode_id":"test", "tool":"read_document", "ok":True,
                     "observation":{"data":{"source_observation":revised},
                                    "evidence":[dict(FACT, id="new"), {"id":"invalid"}], "calculations":[]}}
            with self.assertRaises(ValueError):
                store.record_tool_return(sid, event)
        with SessionStore(self.path) as store:
            remaining = store.get_context(sid, report_mode="none")["evidence"]
            self.assertEqual(["old"], [item["id"] for item in remaining])
            self.assertTrue(remaining[0]["stale"])
            self.assertEqual(event["observation"], store.latest_event(sid, "tool_call")["observation"])
            self.assertEqual("ValueError", store.latest_event(sid, "tool_ingestion_error")["error_type"])

    def test_crash_after_critic_commit_then_reopening_twice_keeps_one_review_and_report(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            def crash_after_review(session_id):
                if store.latest_event(session_id, "verification"):
                    raise SimulatedCrash()
                return False
            model = ScriptedModel(full_script())
            with patch.object(store, "is_cancelled", side_effect=crash_after_review), self.assertRaises(SimulatedCrash):
                ResearchEngine(store, model, FixtureTools()).ask(sid, "Revenue?")
            self.assertEqual(["planner", "planner", "researcher", "critic"], [role for role, _ in model.calls])
            original_episode = store.latest_event(sid, "episode_checkpoint")["episode"]
            self.assertTrue(original_episode["reviewed"])
            self.assertEqual("running", original_episode["status"])
            self.assertEqual([], store.list_reports(sid))

        for question in ("What about the source?", "Use the issuer filing"):
            with SessionStore(self.path) as store:
                next_model = ScriptedModel([("planner", {"action":"clarify", "question":"Which period?"})])
                ResearchEngine(store, next_model, FixtureTools()).ask(sid, question)
                report, = store.list_reports(sid)
                self.assertEqual(original_episode["id"], report["episode_id"])
                self.assertEqual("interrupted", report["stop_reason"])
                self.assertTrue(report["verification_available"])
                self.assertEqual("Supported", report["claims"][0]["status"])
                self.assertEqual(1, sum(e["type"] == "verification" for e in store.events(sid)))
                self.assertEqual(["planner"], [role for role, _ in next_model.calls])

    def test_delivery_checkpoint_failure_rolls_back_report_and_assistant_message_together(self):
        with SessionStore(self.path) as store:
            sid = store.create_session()["id"]
            engine = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools())
            checkpoint = engine._checkpoint
            def interrupt_delivery(session_id, episode):
                if episode["status"] == "complete":
                    raise SimulatedCrash()
                return checkpoint(session_id, episode)
            with patch.object(engine, "_checkpoint", side_effect=interrupt_delivery), self.assertRaises(SimulatedCrash):
                engine.ask(sid, "Revenue?")
            self.assertEqual([], store.list_reports(sid))
            self.assertEqual(["user"], [m["role"] for m in store.get_context(sid)["messages"]])
            self.assertTrue(engine.latest_episode(sid)["reviewed"])
        with SessionStore(self.path) as store:
            model = ScriptedModel([("planner", {"action":"clarify", "question":"Which period?"})])
            ResearchEngine(store, model, FixtureTools()).ask(sid, "Source?")
            report, = store.list_reports(sid)
            messages = store.get_context(sid)["messages"]
            self.assertEqual(["user", "assistant", "user", "assistant"], [m["role"] for m in messages])
            self.assertIn("interrupted", messages[1]["content"])
            self.assertEqual("Supported", report["claims"][0]["status"])

    def test_legacy_report_written_before_crash_restores_only_missing_assistant_reply(self):
        for already_replied in (False, True):
            with self.subTest(already_replied=already_replied), SessionStore(self.path) as store:
                sid = store.create_session()["id"]
                engine = ResearchEngine(store, ScriptedModel(full_script()), FixtureTools())
                def legacy_interruption(session_id, episode):
                    store.save_report(session_id, engine._report(session_id, episode))
                    if already_replied:
                        store.append_message(session_id, "assistant", "Legacy delivery")
                    raise SimulatedCrash()
                with patch.object(engine, "_save_delivery", side_effect=legacy_interruption), self.assertRaises(SimulatedCrash):
                    engine.ask(sid, "Revenue?")
            with self.subTest(reopening=already_replied), SessionStore(self.path) as store:
                model = ScriptedModel([("planner", {"action":"clarify", "question":"Which period?"})])
                ResearchEngine(store, model, FixtureTools()).ask(sid, "Source?")
                report, = store.list_reports(sid)
                messages = store.get_context(sid)["messages"]
                self.assertEqual(["user", "assistant", "user", "assistant"], [m["role"] for m in messages])
                self.assertEqual("complete", report["status"])
                self.assertIn("Legacy delivery" if already_replied else "Research complete", messages[1]["content"])
