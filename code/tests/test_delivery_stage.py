"""Delivery stages reflect this archived draft and only its own local receipts."""
import copy
import unittest
from unittest.mock import patch

from tests.test_engine import FACT, FixtureTools, ScriptedModel, full_script
from verifin.engine import Budget, ResearchEngine
from verifin.model import ModelError, OutputContractError, ProviderOutputError
from verifin.storage import SessionStore


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
