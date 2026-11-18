"""Storage acceptance checks: isolation, correction, reopen and concurrent writes."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from verifin.storage import SessionStore


def fact(identifier="revenue", value=100, entity="MSFT"):
    return {
        "id": identifier, "kind": "financial_fact", "entity": entity,
        "metric": "revenue", "value": value, "unit": "USD",
        "period_start": "2025-01-01", "period_end": "2025-03-31",
        "published_at": "2025-04-25", "source_url": "https://example.com/filing",
        "locator": "income statement, row 1", "snapshot_hash": f"snapshot-{value}",
    }


def calculation(identifier="double", operands=None, value=200):
    return {
        "id": identifier, "operation": "sum", "operand_ids": operands or ["revenue"],
        "value": value, "unit": "USD", "formula": "sum(operands)", "entity": "MSFT",
        "metric": "revenue", "period_start": "2025-01-01", "period_end": "2025-03-31",
    }


def claim(identifier="claim-1", evidence=None, calculations=None):
    return {
        "id": identifier, "text": "Revenue is 100 USD.",
        "evidence_ids": evidence if evidence is not None else ["revenue"],
        "calculation_ids": calculations or [], "requirement_ids": ["r1"],
        "constraints": {"entity": "MSFT", "period_end": "2025-03-31", "unit": "USD"},
    }


def document_observation(revision="a", observed="2026-10-13T10:00:00Z", authoritative=True):
    return {"source_document_id": "filing-2025-q1", "source_url": "https://www.sec.gov/Archives/edgar/data/1/filing.htm",
            "source_revision": revision * 64, "source_observed_at": observed,
            "source_revision_authoritative": authoritative}


def document(identifier="span-1", revision="a", observed="2026-10-13T10:00:00Z", authoritative=True):
    return {"id": identifier, "kind": "document", "entity": "MSFT", "text": "Original filing paragraph.",
            "published_at": "2025-04-25", "locator": "text characters [0:100]", "snapshot_hash": revision * 64,
            **document_observation(revision, observed, authoritative)}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "session.sqlite3"
        self.store = SessionStore(self.path)
        self.sid = self.store.create_session("Quarterly research")["id"]

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_reopen_keeps_conversation_reports_events_and_cancellation(self):
        self.store.append_message(self.sid, "user", "How was revenue?")
        self.store.append_message(self.sid, "assistant", "Which period?")
        self.store.append_message(self.sid, "user", "Q1 2025")
        self.store.put_evidence(self.sid, fact())
        saved = self.store.save_report(self.sid, {"text": "100 USD", "status": "partial"})
        event = {"type": "episode_checkpoint", "episode": {"model_calls": 4, "status": "clarify"}}
        self.store.add_event(self.sid, event)
        self.store.request_cancel(self.sid)
        self.store.close()
        self.store = SessionStore(self.path)
        context = self.store.get_context(self.sid)
        self.assertEqual([m["sequence"] for m in context["messages"]], [1, 2, 3])
        self.assertEqual(context["evidence"][0]["value"], 100)
        self.assertEqual(context["reports"][0], saved)
        self.assertEqual(self.store.events(self.sid)[0]["episode"], event["episode"])
        self.assertTrue(self.store.is_cancelled(self.sid))
        self.store.clear_cancel(self.sid)
        self.assertFalse(self.store.is_cancelled(self.sid))

    def test_sessions_isolate_identical_record_ids(self):
        other = self.store.create_session()["id"]
        self.store.put_evidence(self.sid, fact(value=100))
        self.store.put_evidence(other, fact(value=900))
        self.store.append_message(self.sid, "user", "Private to first session")
        self.store.add_event(self.sid, {"type": "first"})
        self.store.save_report(self.sid, {"text": "first"})
        self.store.request_cancel(self.sid)
        ctx = self.store.get_context(other)
        self.assertEqual(ctx["evidence"][0]["value"], 900)
        self.assertEqual(ctx["messages"], [])
        self.assertEqual(ctx["reports"], [])
        self.assertEqual(self.store.events(other), [])
        self.assertFalse(self.store.is_cancelled(other))

    def test_evidence_correction_invalidates_transitive_dependents_only(self):
        original = self.store.put_evidence(self.sid, fact())
        self.store.put_evidence(self.sid, fact("unrelated", 55, "NVDA"))
        self.store.put_calculation(self.sid, calculation("first"))
        self.store.put_calculation(self.sid, calculation("second", ["first"]))
        self.store.put_claim(self.sid, claim(calculations=["second"]))
        self.store.put_claim(self.sid, claim("unrelated-claim", ["unrelated"]))
        old_report = self.store.save_report(self.sid, {"text": "Previous conclusion"})
        replacement = self.store.put_evidence(self.sid, fact(value=101))
        self.assertEqual(replacement["version"], 2)
        ctx = self.store.get_context(self.sid)
        self.assertTrue(all(r["stale"] for r in ctx["calculations"]))
        claims = {r["id"]: r for r in ctx["claims"]}
        self.assertTrue(claims["claim-1"]["stale"])
        self.assertFalse(claims["unrelated-claim"]["stale"])
        self.assertEqual(self.store.history(self.sid, "evidence", "revenue")[0], original)
        self.assertEqual(self.store.list_reports(self.sid), [old_report])
        self.assertEqual(old_report["record_versions"]["evidence"]["revenue"], 1)

    def test_recalculation_does_not_implicitly_revalidate_claim(self):
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation())
        self.store.put_claim(self.sid, claim(calculations=["double"]))
        self.store.put_evidence(self.sid, fact(value=101))
        self.store.put_calculation(self.sid, calculation(value=202))
        ctx = self.store.get_context(self.sid)
        self.assertFalse(ctx["calculations"][0]["stale"])
        self.assertTrue(ctx["claims"][0]["stale"])
        revised = self.store.put_claim(self.sid, claim(calculations=["double"]))
        self.assertFalse(revised["stale"])
        self.assertEqual(revised["version"], 2)
        self.assertEqual(revised["calculation_versions"], {"double": 2})

    def test_idempotent_writes_do_not_create_versions(self):
        first = self.store.put_evidence(self.sid, fact())
        second = self.store.put_evidence(self.sid, fact())
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.history(self.sid, "evidence", "revenue")), 1)

    def test_identical_source_reuse_after_scope_edit_is_idempotent(self):
        first = self.store.put_evidence(self.sid, fact())
        self.store.update_scope(self.sid, {"entities": ["MSFT", "NVDA"]})
        second = self.store.put_evidence(self.sid, fact())
        self.assertEqual(first, second)

    def test_retrieval_metadata_does_not_invalidate_unchanged_source(self):
        first = self.store.put_evidence(self.sid, dict(fact(), retrieval_score=0.2, retrieved_at="2026-10-12T01:00:00Z"))
        self.store.put_claim(self.sid, claim())
        second = self.store.put_evidence(self.sid, dict(fact(), retrieval_score=0.8, retrieved_at="2026-10-13T01:00:00Z"))
        self.assertEqual(first, second)
        self.assertFalse(self.store.get_context(self.sid)["claims"][0]["stale"])
        changed = self.store.put_evidence(self.sid, dict(fact(), published_at="2025-04-26"))
        self.assertEqual(changed["version"], 2)
        self.assertTrue(self.store.get_context(self.sid)["claims"][0]["stale"])

    def test_status_update_does_not_clear_scope_staleness(self):
        self.store.update_scope(self.sid, {"entities": ["MSFT"]})
        self.store.put_evidence(self.sid, fact())
        self.store.put_claim(self.sid, claim())
        self.store.update_scope(self.sid, {"entities": ["NVDA"]})
        stale = self.store.get_context(self.sid)["claims"][0]
        stale["status"] = "Pending Verification"
        saved = self.store.put_claim(self.sid, stale)
        self.assertTrue(saved["stale"])

    def test_fresh_review_status_change_retains_validity(self):
        self.store.put_evidence(self.sid, fact())
        pending = self.store.put_claim(self.sid, dict(claim(), status="Pending Verification"))
        pending["status"] = "Supported"
        verified = self.store.put_claim(self.sid, pending)
        self.assertFalse(verified["stale"])
        self.assertEqual(verified["status"], "Supported")
        self.assertEqual(verified["version"], 2)

    def test_scope_change_keeps_evidence_history_and_reports(self):
        scope = {"entities": ["MSFT"], "period_start": "2025-01-01", "period_end": "2025-03-31",
                 "requirements": [{"id": "r1", "text": "Explain revenue"}], "metrics": ["revenue"]}
        self.store.update_scope(self.sid, scope)
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation())
        self.store.put_claim(self.sid, claim())
        old_report = self.store.save_report(self.sid, {"text": "Q1"})
        scope["period_start"], scope["period_end"] = "2024-01-01", "2024-03-31"
        updated = self.store.update_scope(self.sid, scope)
        ctx = self.store.get_context(self.sid)
        self.assertEqual(updated["scope_version"], 3)
        self.assertFalse(ctx["evidence"][0]["stale"])
        self.assertTrue(ctx["calculations"][0]["stale"])
        self.assertTrue(ctx["claims"][0]["stale"])
        self.assertEqual(ctx["reports"], [old_report])
        self.assertEqual(old_report["scope"]["period_end"], "2025-03-31")
        self.assertEqual(self.store.update_scope(self.sid, scope)["scope_version"], 3)

    def test_company_expansion_reuses_matching_calculations(self):
        self.store.update_scope(self.sid, {"entities": ["MSFT"]})
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation())
        self.store.put_claim(self.sid, claim())
        self.store.update_scope(self.sid, {"entities": ["MSFT", "NVDA"]})
        ctx = self.store.get_context(self.sid)
        self.assertFalse(ctx["calculations"][0]["stale"])
        self.assertTrue(ctx["claims"][0]["stale"])

    def test_validation_rolls_back_without_corrupting_previous_state(self):
        self.store.put_evidence(self.sid, fact())
        invalids = [dict(fact(), value=float("nan")), dict(fact(), value=True),
                    dict(fact(), period_end="2024-01-01"), dict(fact(), published_at="yesterday")]
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.put_evidence(self.sid, invalid)
        with self.assertRaises(ValueError):
            self.store.put_calculation(self.sid, calculation(operands=["missing"]))
        with self.assertRaises(ValueError):
            self.store.put_calculation(self.sid, dict(calculation(), operand_versions={"revenue": 99}))
        self.assertEqual(len(self.store.history(self.sid, "evidence", "revenue")), 1)
        self.assertEqual(self.store.get_context(self.sid)["calculations"], [])
        self.store.append_message(self.sid, "user", "Still operational")

    def test_claim_bad_reference_preserved_for_verifier(self):
        saved = self.store.put_claim(self.sid, claim(evidence=["missing"]))
        self.assertEqual(saved["evidence_ids"], ["missing"])
        self.assertTrue(saved["stale"])

    def test_cycles_rejected_and_previous_calculation_preserved(self):
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation("a"))
        self.store.put_calculation(self.sid, calculation("b", ["a"]))
        with self.assertRaises(ValueError):
            self.store.put_calculation(self.sid, calculation("a", ["b"]))
        self.assertEqual(self.store.history(self.sid, "calculation", "a")[0]["operand_ids"], ["revenue"])

    def test_input_and_return_values_cannot_mutate_stored_records(self):
        original = fact()
        saved = self.store.put_evidence(self.sid, original)
        original["value"] = 111
        saved["value"] = 222
        self.assertEqual(self.store.get_context(self.sid)["evidence"][0]["value"], 100)
        scope = {"entities": ["MSFT"]}
        self.store.update_scope(self.sid, scope)
        scope["entities"].append("NVDA")
        self.assertEqual(self.store.get_session(self.sid)["scope"]["entities"], ["MSFT"])

    def test_concurrent_connections_do_not_lose_messages_versions_or_events(self):
        other = SessionStore(self.path)
        try:
            def write_batch(store, prefix):
                for i in range(15):
                    store.append_message(self.sid, "user", f"{prefix}-{i}")
                    store.add_event(self.sid, {"type": "progress", "writer": prefix, "step": i})
                    store.put_evidence(self.sid, fact(value=100 + i + (100 if prefix == "b" else 0)))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write_batch, self.store, "a"), pool.submit(write_batch, other, "b")]
                for future in futures:
                    future.result()
            context = self.store.get_context(self.sid)
            self.assertEqual([m["sequence"] for m in context["messages"]], list(range(1, 31)))
            self.assertEqual([e["event_sequence"] for e in self.store.events(self.sid)], list(range(1, 31)))
            self.assertEqual([r["version"] for r in self.store.history(self.sid, "evidence", "revenue")], list(range(1, 31)))
        finally:
            other.close()

    def test_concurrent_report_saves_have_unique_versions(self):
        other = SessionStore(self.path)
        try:
            def save_batch(store, prefix):
                return [store.save_report(self.sid, {"id": "research-report", "text": f"{prefix}-{i}"})
                        for i in range(10)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(save_batch, self.store, "a"), pool.submit(save_batch, other, "b")]
                saved = [r for future in futures for r in future.result()]
            reports = self.store.list_reports(self.sid)
            self.assertEqual([r["version"] for r in reports], list(range(1, 21)))
            self.assertEqual({r["text"] for r in reports}, {r["text"] for r in saved})
        finally:
            other.close()

    def test_failure_after_insert_rolls_back_version_and_invalidation(self):
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation())
        original_cascade = self.store._cascade_stale
        def fail_after_invalidation(session_id):
            original_cascade(session_id)
            raise RuntimeError("simulated failure after writes")
        with patch.object(self.store, "_cascade_stale", fail_after_invalidation):
            with self.assertRaises(RuntimeError):
                self.store.put_evidence(self.sid, fact(value=101))
        ctx = self.store.get_context(self.sid)
        self.assertEqual(ctx["evidence"][0]["version"], 1)
        self.assertFalse(ctx["calculations"][0]["stale"])
        self.assertEqual(len(self.store.history(self.sid, "evidence", "revenue")), 1)
        self.assertEqual(self.store.put_evidence(self.sid, fact(value=101))["version"], 2)

    def test_reader_never_sees_fresh_result_against_changed_source(self):
        self.store.put_evidence(self.sid, fact())
        self.store.put_calculation(self.sid, calculation())
        other = SessionStore(self.path)
        try:
            def revise():
                for value in range(101, 121):
                    other.put_evidence(self.sid, fact(value=value))
                    other.put_calculation(self.sid, calculation(value=value * 2))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(revise)
                for _ in range(100):
                    context = self.store.get_context(self.sid)
                    source, result = context["evidence"][0], context["calculations"][0]
                    if not result["stale"]:
                        self.assertEqual(result["operand_versions"][source["id"]], source["version"])
                        self.assertEqual(result["value"], source["value"] * 2)
                future.result()
        finally:
            other.close()

    def test_future_schema_is_rejected_without_resetting_database(self):
        self.store.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            SessionStore(self.path)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(connection.execute("SELECT id FROM sessions").fetchone()[0], self.sid)

    def test_latest_event_is_scoped_and_matches_requested_type(self):
        other = self.store.create_session()["id"]
        self.assertIsNone(self.store.latest_event(self.sid, "episode_checkpoint"))
        self.store.add_event(self.sid, {"type": "episode_checkpoint", "episode": {"model_calls": 1}})
        checkpoint = self.store.add_event(self.sid, {"type": "episode_checkpoint", "episode": {"model_calls": 4}})
        self.store.add_event(self.sid, {"type": "model_call", "role": "critic"})
        self.store.add_event(other, {"type": "episode_checkpoint", "episode": {"model_calls": 99}})
        self.assertEqual(self.store.latest_event(self.sid, "episode_checkpoint"), checkpoint)
        with self.assertRaises(KeyError):
            self.store.latest_event("missing", "episode_checkpoint")
        with self.assertRaises(ValueError):
            self.store.latest_event(self.sid, "")

    def test_recent_events_filter_before_limit_and_remain_chronological(self):
        for index in range(5):
            self.store.add_event(self.sid, {"type": "tool_call", "index": index})
            self.store.add_event(self.sid, {"type": "episode_checkpoint", "episode": {"calls": index}})
        self.store.add_event(self.sid, {"note": "untyped event is retained"})
        recent = self.store.events(self.sid, limit=3, exclude_types=["episode_checkpoint"])
        self.assertEqual([r["event_sequence"] for r in recent], [7, 9, 11])
        self.assertEqual(len(self.store.events(self.sid)), 11)
        self.assertEqual(self.store.events(self.sid, limit=0), [])
        self.assertEqual(len(self.store.events(self.sid, exclude_types={"episode_checkpoint"})), 6)
        for kwargs in ({"limit": -1}, {"limit": True}, {"exclude_types": "episode_checkpoint"}, {"exclude_types": [None]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.events(self.sid, **kwargs)

    def test_simultaneous_initial_connections_migrate_and_write_safely(self):
        fresh = Path(self.tmp.name) / "simultaneous.sqlite3"
        barrier = threading.Barrier(6)
        def initialize(index):
            barrier.wait(timeout=5)
            with SessionStore(fresh) as store:
                return store.create_session(f"writer-{index}")["id"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(initialize, index) for index in range(6)]
            identifiers = {future.result() for future in futures}
        with SessionStore(fresh) as store:
            self.assertEqual({s["id"] for s in store.list_sessions()}, identifiers)
            self.assertEqual(store._db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(store._db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_wal_startup_retries_real_busy_after_reader_releases_lock(self):
        path = Path(self.tmp.name) / "reader-lock.sqlite3"
        blocker = sqlite3.connect(path, isolation_level=None)
        try:
            blocker.execute("CREATE TABLE original(value TEXT)")
            blocker.execute("INSERT INTO original VALUES ('preserved')")
            blocker.execute("BEGIN")
            blocker.execute("SELECT * FROM original").fetchall()
            # A real rollback-journal reader prevents switching to WAL. Release
            # it only after the new initializer has observed and retried BUSY.
            def release_reader(delay):
                self.assertGreater(delay, 0)
                self.assertLessEqual(delay, 0.1)
                blocker.execute("COMMIT")
            with patch("verifin.storage.time.sleep", side_effect=release_reader) as wait:
                with SessionStore(path) as store:
                    self.assertEqual(wait.call_count, 1)
                    self.assertEqual(store._db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    self.assertEqual(store._db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                    self.assertEqual(store._db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                    self.assertEqual(store._db.execute("SELECT value FROM original").fetchone()[0], "preserved")
                    self.assertTrue(store.create_session("After lock release")["id"])
        finally:
            blocker.close()

    def test_wal_startup_persistent_lock_expires_and_closes_connection(self):
        path = Path(self.tmp.name) / "persistent-lock.sqlite3"
        blocker = sqlite3.connect(path, isolation_level=None)
        opened = []
        connect = sqlite3.connect
        def record_connection(*args, **kwargs):
            connection = connect(*args, **kwargs)
            opened.append(connection)
            return connection
        try:
            blocker.execute("CREATE TABLE original(value TEXT)")
            blocker.execute("BEGIN")
            blocker.execute("SELECT * FROM original").fetchall()
            with patch("verifin.storage.sqlite3.connect", side_effect=record_connection), \
                 patch("verifin.storage.time.monotonic", side_effect=[0.0, 29.99, 30.0]), \
                 patch("verifin.storage.time.sleep") as wait:
                with self.assertRaises(sqlite3.OperationalError) as raised:
                    SessionStore(path)
                self.assertEqual(raised.exception.sqlite_errorcode & 0xff, sqlite3.SQLITE_BUSY)
                self.assertEqual(wait.call_count, 1)
                self.assertAlmostEqual(wait.call_args.args[0], 0.01)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("SELECT 1")
            blocker.execute("COMMIT")
            with SessionStore(path) as store:
                self.assertTrue(store.create_session("Recovery after expired attempt")["id"])
        finally:
            blocker.close()

    def test_wal_startup_never_retries_non_lock_errors(self):
        failure = sqlite3.OperationalError("deliberate I/O failure")
        failure.sqlite_errorcode = sqlite3.SQLITE_IOERR
        opened = []
        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "PRAGMA journal_mode=WAL":
                    raise failure
                return super().execute(sql, *args)
        connect = sqlite3.connect
        def failing_connection(*args, **kwargs):
            connection = connect(*args, factory=FailingConnection, **kwargs)
            opened.append(connection)
            return connection
        with patch("verifin.storage.sqlite3.connect", side_effect=failing_connection), \
             patch("verifin.storage.time.sleep") as wait:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                SessionStore(Path(self.tmp.name) / "io-error.sqlite3")
            self.assertIs(raised.exception, failure)
            wait.assert_not_called()
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")

    def test_version_one_database_migration_keeps_checkpoints(self):
        old_path = Path(self.tmp.name) / "version-one.sqlite3"
        event = {"type": "episode_checkpoint", "episode": {"model_calls": 4}, "event_sequence": 1}
        with sqlite3.connect(old_path) as connection:
            connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY,title TEXT NOT NULL,scope TEXT NOT NULL,scope_version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,cancelled INTEGER NOT NULL)")
            connection.execute("CREATE TABLE events (session_id TEXT NOT NULL REFERENCES sessions(id),sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session_id,sequence))")
            connection.execute("INSERT INTO sessions VALUES ('old','Saved session','{}',1,'2026-10-13','2026-10-13',0)")
            connection.execute("INSERT INTO events VALUES ('old',1,?)", (json.dumps(event),))
            connection.execute("PRAGMA user_version=1")
        with SessionStore(old_path) as upgraded:
            self.assertEqual(upgraded.latest_event("old", "episode_checkpoint"), event)
            next_event = upgraded.add_event("old", {"type": "episode_checkpoint", "episode": {"model_calls": 5}})
            self.assertEqual(next_event["event_sequence"], 2)
            self.assertEqual(upgraded.events("old")[0], event)
        with sqlite3.connect(old_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_document_refresh_without_new_passage_invalidates_old_source_dependents(self):
        self.store.put_evidence(self.sid, document())
        self.store.put_claim(self.sid, claim(evidence=["span-1"]))
        report = self.store.save_report(self.sid, {"text": "Old documented finding"})
        result = self.store.observe_document_source(self.sid, document_observation("b", "2026-10-13T11:00:00Z"))
        self.assertTrue(result["changed"])
        self.assertEqual(result["invalidated_evidence_ids"], ["span-1"])
        context = self.store.get_context(self.sid)
        self.assertTrue(context["evidence"][0]["stale"])
        self.assertTrue(context["claims"][0]["stale"])
        fresh = self.store.put_evidence(self.sid, document("span-2", "b", "2026-10-13T11:00:00Z"))
        self.assertFalse(fresh["stale"])
        self.assertEqual(self.store.list_reports(self.sid), [report])

    def test_document_lineage_preserves_other_sources_financial_facts_and_historical_replay(self):
        self.store.put_evidence(self.sid, document())
        historical = document("historical", "h", "2026-10-12T10:00:00Z", authoritative=False)
        self.store.put_evidence(self.sid, historical)
        ordinary = document("ordinary-replay")
        for field in ("source_document_id", "source_revision", "source_observed_at", "source_revision_authoritative"):
            ordinary.pop(field)
        self.store.put_evidence(self.sid, ordinary)
        self.store.put_evidence(self.sid, dict(fact(), source_url=document_observation()["source_url"]))
        other = document("other-source")
        other.update(source_document_id="different-filing", source_url="https://www.sec.gov/Archives/edgar/data/1/other.htm")
        self.store.put_evidence(self.sid, other)
        self.store.observe_document_source(self.sid, document_observation("b", "2026-10-13T11:00:00Z"))
        records = {r["id"]: r for r in self.store.get_context(self.sid)["evidence"]}
        self.assertTrue(records.pop("span-1")["stale"])
        self.assertTrue(all(not r["stale"] for r in records.values()))

    def test_cached_older_document_observation_cannot_downgrade_current_revision(self):
        self.store.put_evidence(self.sid, document("current", "b", "2026-10-13T11:00:00Z"))
        observed = self.store.observe_document_source(self.sid, document_observation("a", "2026-10-13T10:00:00Z"))
        self.assertFalse(observed["changed"])
        self.assertEqual(observed["reason"], "older_observation")
        old = self.store.put_evidence(self.sid, document("cached-old", "a", "2026-10-13T10:00:00Z"))
        self.assertTrue(old["stale"])
        fresh = next(r for r in self.store.get_context(self.sid)["evidence"] if r["id"] == "current")
        self.assertFalse(fresh["stale"])
        self.assertEqual(self.store.history(self.sid, "document_source", "filing-2025-q1")[-1]["source_revision"], "b" * 64)

    def test_same_document_revision_new_observation_time_does_not_invalidate(self):
        self.store.put_evidence(self.sid, document())
        self.store.put_claim(self.sid, claim(evidence=["span-1"]))
        observed = self.store.observe_document_source(self.sid, document_observation("a", "2026-10-13T11:00:00Z"))
        self.assertFalse(observed["changed"])
        self.assertEqual(observed["invalidated_evidence_ids"], [])
        self.assertFalse(self.store.get_context(self.sid)["claims"][0]["stale"])
        same_span = self.store.put_evidence(self.sid, dict(document(observed="2026-10-13T11:00:00Z"), mode="cached_live"))
        self.assertEqual(same_span["version"], 1)
        self.assertFalse(self.store.get_context(self.sid)["claims"][0]["stale"])
        # The fresh observation time matters even though the bytes did not change.
        stale = self.store.observe_document_source(self.sid, document_observation("b", "2026-10-13T10:30:00Z"))
        self.assertEqual(stale["reason"], "older_observation")

    def test_document_observation_validates_identity_ordering_and_snapshot_binding(self):
        self.store.put_evidence(self.sid, document())
        with self.assertRaises(ValueError):
            self.store.observe_document_source(self.sid, document_observation("b"))
        with self.assertRaises(ValueError):
            self.store.observe_document_source(self.sid, dict(document_observation(), source_url="https://example.com/other"))
        with self.assertRaises(ValueError):
            self.store.observe_document_source(self.sid, document_observation(observed="2026-10-13T10:00:00"))
        with self.assertRaises(ValueError):
            self.store.put_evidence(self.sid, dict(document(), snapshot_hash="mismatched"))
        self.assertEqual(len(self.store.history(self.sid, "document_source", "filing-2025-q1")), 1)

    def test_document_lineage_survives_reopen_without_history_reset(self):
        self.store.put_evidence(self.sid, fact())
        self.store.close()
        # Source observations use an additional record kind in the existing
        # append-only table, so reopening must not reset any source history.
        self.store = SessionStore(self.path)
        self.store.put_evidence(self.sid, document())
        self.store.close()
        self.store = SessionStore(self.path)
        self.store.observe_document_source(self.sid, document_observation("b", "2026-10-13T11:00:00Z"))
        records = {r["id"]: r for r in self.store.get_context(self.sid)["evidence"]}
        self.assertFalse(records["revenue"]["stale"])
        self.assertTrue(records["span-1"]["stale"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_report_lookup_and_summary_history_preserve_exact_saved_versions(self):
        first = self.store.save_report(self.sid, {
            "id": "research-report", "request": "Earlier quarter", "status": "complete",
            "claims": [{"id": "claim-1", "text": "Old finding"}], "evidence": [{"text": "Old original passage"}],
            "unanswered": [], "usage": {"model_calls": 5},
        })
        second = self.store.save_report(self.sid, {
            "id": "research-report", "request": "Corrected quarter", "status": "partial",
            "claims": [], "evidence": [{"text": "New source passage"}], "unanswered": [{"id": "gap-1"}],
        })
        third = self.store.save_report(self.sid, {"id": "separate-report", "request": "Follow-up"})
        self.assertEqual(self.store.get_report(self.sid, 1), first)
        self.assertEqual(self.store.get_report(self.sid, "research-report"), second)
        self.assertEqual(self.store.get_report(self.sid, "separate-report"), third)
        self.assertEqual(self.store.list_reports(self.sid), [first, second, third])
        summaries = self.store.list_report_summaries(self.sid, limit=2)
        self.assertEqual([r["version"] for r in summaries], [2, 3])
        self.assertEqual(summaries[0]["claim_count"], 0)
        self.assertEqual(summaries[0]["unanswered_count"], 1)
        self.assertTrue(all("evidence" not in r and "claims" not in r and "calculations" not in r for r in summaries))
        self.store.close()
        self.store = SessionStore(self.path)
        self.assertEqual(self.store.get_report(self.sid, 1), first)
        self.assertEqual(self.store.list_report_summaries(self.sid, limit=2), summaries)

    def test_report_list_does_not_promote_a_rejected_draft_or_rewrite_legacy_snapshots(self):
        report = self.store.save_report(self.sid, {
            "request":"Sales?", "verification_available":True, "summary":"Sales reached USD 79.632 billion.",
            "summary_kind":"unverified_researcher_draft",
            "claims":[{"id":"wrong","status":"Insufficient Evidence","text":"Sales reached USD 79.632 billion."},
                      {"id":"right","status":"Supported","text":"Sales reached USD 69.632 billion."}]})
        compact, = self.store.list_report_summaries(self.sid)
        self.assertEqual((compact["summary_claim_id"],compact["summary_kind"],compact["summary"]),
                         ("right","supported_claim_excerpt","Sales reached USD 69.632 billion."))
        old = dict(compact, summary=report["summary"])
        del old["summary_kind"], old["summary_claim_id"]
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reports SET summary=? WHERE session_id=?", (json.dumps(old), self.sid))
        with SessionStore(self.path) as reader:
            legacy, = reader.get_context(self.sid,report_mode="summaries")["reports"]
            self.assertNotIn("summary",legacy)
            self.assertEqual(legacy["summary_kind"],"see_report_findings")
            self.assertEqual(reader.get_report(self.sid,1),report)
        with sqlite3.connect(self.path) as db:
            saved, = db.execute("SELECT summary FROM reports WHERE session_id=?",(self.sid,)).fetchone()
            self.assertEqual(json.loads(saved), old)

    def test_compact_report_prefers_an_answer_to_the_current_question_over_supported_background(self):
        report = {"verification_available":True, "status":"partial",
                  "request_parts":{"part-1":"增长率是多少？","part-2":"需求是否变化？"},
                  "claims":[{"id":"prior","text":"上年销售额为42亿美元。","status":"Supported"},
                            {"id":"answer","text":"增长率为3.7%。营业天数也增加了。","status":"Supported"}],
                  "request_coverage":[{"part":"part-1","status":"answered","claim_id":"answer","answer_sentence":"增长率为3.7%。"},
                                      {"part":"part-2","status":"gap","missing_support":"需求证据不足。"}]}
        self.store.save_report(self.sid,report)
        row, = self.store.list_report_summaries(self.sid)
        self.assertEqual((row["summary"],row["summary_claim_id"]),("增长率为3.7%。","answer"))
        self.store.save_report(self.sid,dict(report,request_coverage=[]))
        gap, = self.store.list_report_summaries(self.sid,limit=1)
        self.assertNotIn("summary", gap)

    def test_context_report_modes_and_limit_keep_true_history_count(self):
        reports = [self.store.save_report(self.sid, {"request": f"request-{i}", "claims": [], "evidence": [{"text": "source"}]})
                   for i in range(4)]
        self.assertEqual(self.store.get_context(self.sid)["reports"], reports)
        limited = self.store.get_context(self.sid, report_limit=2)
        self.assertEqual(limited["reports"], reports[-2:])
        self.assertEqual(limited["report_count"], 4)
        summaries = self.store.get_context(self.sid, report_mode="summaries", report_limit=1)
        self.assertEqual([r["version"] for r in summaries["reports"]], [4])
        self.assertNotIn("evidence", summaries["reports"][0])
        none = self.store.get_context(self.sid, report_mode="none")
        self.assertEqual(none["reports"], [])
        self.assertEqual(none["report_count"], 4)
        self.assertEqual(self.store.get_context(self.sid, report_limit=0)["reports"], [])
        self.assertEqual(self.store.list_report_summaries(self.sid, limit=0), [])

    def test_report_summaries_and_none_context_never_read_full_report_payload_column(self):
        saved = self.store.save_report(self.sid, {
            "request": "Inspect metadata without loading source copies", "status": "partial",
            "evidence": [{"text": "large source passage " * 5000}],
            "calculations": [{"formula": "large formula " * 1000}],
        })
        def deny_full_report_reads(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "reports" and column == "payload":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store._db.set_authorizer(deny_full_report_reads)
        try:
            self.assertEqual(self.store.list_report_summaries(self.sid)[0]["id"], saved["id"])
            self.assertEqual(self.store.get_context(self.sid, report_mode="summaries")["report_count"], 1)
            self.assertEqual(self.store.get_context(self.sid, report_mode="none")["reports"], [])
            # Ensure this test actually blocks a full report read, rather than
            # merely relying on an assumption about the SQLite authorizer.
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.get_report(self.sid, saved["id"])
        finally:
            self.store._db.set_authorizer(None)
        self.assertEqual(self.store.get_report(self.sid, saved["id"]), saved)

    def test_report_history_lookup_is_session_isolated(self):
        other = self.store.create_session()["id"]
        first = self.store.save_report(self.sid, {"id": "same-id", "request": "First session"})
        second = self.store.save_report(other, {"id": "same-id", "request": "Second session"})
        only_first = self.store.save_report(self.sid, {"id": "first-only", "request": "Private first report"})
        self.assertEqual(self.store.get_report(self.sid, "same-id"), first)
        self.assertEqual(self.store.get_report(other, "same-id"), second)
        self.assertEqual(self.store.get_report(other, 1), second)
        self.assertEqual(len(self.store.list_report_summaries(other)), 1)
        self.assertEqual(self.store.get_context(other, report_mode="none")["report_count"], 1)
        with self.assertRaises(KeyError):
            self.store.get_report(other, only_first["id"])
        with self.assertRaises(KeyError):
            self.store.get_report(other, 2)

    def test_report_history_empty_missing_and_invalid_inputs(self):
        self.assertEqual(self.store.list_report_summaries(self.sid), [])
        self.assertEqual(self.store.get_context(self.sid, report_mode="summaries")["report_count"], 0)
        for identifier in ("not-found", 1):
            with self.assertRaises(KeyError):
                self.store.get_report(self.sid, identifier)
        for identifier in (None, [], {}, True, False, 0, -1, "", " "):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.store.get_report(self.sid, identifier)
        for value in (-1, True, 1.5, "3"):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.store.list_report_summaries(self.sid, limit=value)
        for kwargs in ({"report_mode": "bad"}, {"report_mode": None}, {"report_limit": -1}, {"report_limit": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.get_context(self.sid, **kwargs)
        with self.assertRaises(KeyError):
            self.store.list_report_summaries("missing")
        with self.assertRaises(KeyError):
            self.store.get_report("missing", 1)

    def test_version_two_report_migration_preserves_payload_bytes_and_builds_summary(self):
        old_path = Path(self.tmp.name) / "version-two.sqlite3"
        report = {"id": "old-report", "version": 1, "request": "Old report", "status": "complete",
                  "created_at": "2026-10-13T10:00:00Z", "claims": [{"id": "c1"}],
                  "evidence": [{"text": "Original snapshot; preserve exactly"}], "unanswered": []}
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        with sqlite3.connect(old_path) as connection:
            connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY,title TEXT NOT NULL,scope TEXT NOT NULL,scope_version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,cancelled INTEGER NOT NULL)")
            connection.execute("CREATE TABLE reports (session_id TEXT NOT NULL REFERENCES sessions(id),version INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session_id,version))")
            connection.execute("INSERT INTO sessions VALUES ('old','Saved session','{}',1,'2026-10-13','2026-10-13',0)")
            connection.execute("INSERT INTO reports VALUES ('old',1,?)", (encoded,))
            connection.execute("PRAGMA user_version=2")
        with SessionStore(old_path) as upgraded:
            self.assertEqual(upgraded.get_report("old", "old-report"), report)
            summary = upgraded.list_report_summaries("old")[0]
            self.assertEqual(summary["claim_count"], 1)
            self.assertNotIn("evidence", summary)
            self.assertEqual(upgraded.save_report("old", {"request": "New report"})["version"], 2)
        with sqlite3.connect(old_path) as connection:
            self.assertEqual(connection.execute("SELECT payload FROM reports WHERE version=1").fetchone()[0], encoded)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_missing_sessions_consistently_raise_keyerror(self):
        actions = [lambda: self.store.get_session("missing"), lambda: self.store.get_context("missing"),
                   lambda: self.store.events("missing"), lambda: self.store.list_reports("missing"),
                   lambda: self.store.update_scope("missing", {}),
                   lambda: self.store.append_message("missing", "user", "x"),
                   lambda: self.store.request_cancel("missing"),
                   lambda: self.store.put_evidence("missing", fact())]
        for action in actions:
            with self.assertRaises(KeyError):
                action()

    def test_scope_validation_and_nullable_metadata(self):
        for scope in (["not an object"], {"entities": "MSFT"}, {"entities": ["MSFT", "MSFT"]},
                      {"requirements": [{"id": "r1", "text": "x"}, {"id": "r1", "text": "y"}]},
                      {"period_start": "2025-03-31", "period_end": "2025-01-01"}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.store.update_scope(self.sid, scope)
        unknown = fact()
        for key in ("published_at", "source_url", "locator", "snapshot_hash"):
            unknown[key] = None
        self.assertIsNone(self.store.put_evidence(self.sid, unknown)["published_at"])


    def test_reopen_rechecks_preexisting_compact_verdict_without_touching_archive(self):
        question = "What was operating cash flow?"
        sales = {"id":"sales", "text":"Revenue was USD 10 million.", "status":"Supported",
                 "constraints":{"metric":"revenue"}}
        report = {"id":"old", "version":1, "request":question, "status":"complete",
                  "request_parts":{"part-1":question}, "verification_available":True, "claims":[sales],
                  "request_coverage":[{"part":"part-1", "status":"answered", "claim_id":"sales",
                                       "answer_sentence":sales["text"]}]}
        raw = json.dumps(report, indent=2)
        old_summary = {"id":"old", "version":1, "status":"complete", "summary":sales["text"],
                       "summary_kind":"supported_claim_excerpt", "summary_claim_id":"sales"}
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO reports(session_id,version,payload,report_id,summary) VALUES (?,?,?,?,?)",
                       (self.sid, 1, raw, "old", json.dumps(old_summary)))
        self.store.close()
        self.store = SessionStore(self.path)
        summary, = self.store.list_report_summaries(self.sid)
        self.assertEqual("partial", summary["status"])
        self.assertEqual("complete", summary["archived_status"])
        self.assertEqual(1, summary["question_counts"]["gap"])
        self.assertNotIn("summary_claim_id", summary)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(raw, db.execute("SELECT payload FROM reports WHERE session_id=?", (self.sid,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()

