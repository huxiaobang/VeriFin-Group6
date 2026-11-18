import concurrent.futures
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from verifin.engine import digest
from verifin.recovery import archive_result, RecoveryError, StepJournal, step_identity


class JournalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "journal.sqlite3"
        self.journal = StepJournal(self.path)
        self.key = step_identity({"candidate_sha256": "a" * 64}, "critic", {"wire_sha256": "b" * 64})
        self.journal.plan("review", self.key)

    def test_not_dispatched_and_unknown_survive_reopening_and_replanning(self):
        self.assertEqual(self.journal.recover("review", self.key), {"state": "not_started"})
        self.journal.start("review", self.key)
        reopened = StepJournal(self.path)
        reopened.plan("review", self.key)
        for _ in range(3):
            self.assertEqual(reopened.recover("review", self.key), {"state": "outcome_unknown"})
        with self.assertRaisesRegex(RecoveryError, "already started"):
            reopened.start("review", self.key)

    def test_completion_lost_to_caller_can_be_reopened_and_commit_redelivered(self):
        token = self.journal.start("review", self.key)
        receipt = {"ok": False, "error": "output_contract", "usage": {"prompt_tokens": 120}}
        self.journal.commit("review", self.key, token, receipt)
        reopened = StepJournal(self.path)
        self.assertEqual(reopened.recover("review", self.key)["receipt"], receipt)
        reopened.commit("review", self.key, token, receipt)
        with self.assertRaisesRegex(RecoveryError, "different"):
            reopened.commit("review", self.key, token, {"ok": True})
        with self.assertRaisesRegex(RecoveryError, "already started"):
            reopened.start("review", self.key)

    def test_different_candidate_step_arguments_and_wrong_late_owner_refused(self):
        token = self.journal.start("review", self.key)
        for key in (step_identity({"candidate_sha256": "c"*64}, "critic", self.key["arguments"]),
                    step_identity(self.key["anchor"], "critic", {"wire_sha256": "d"*64})):
            for method in (self.journal.plan, self.journal.start, self.journal.recover):
                with self.assertRaisesRegex(RecoveryError, "different"):
                    method("review", key)
        with self.assertRaisesRegex(RecoveryError, "owner"):
            self.journal.commit("review", self.key, token + "wrong", {"ok": True})
        with self.assertRaisesRegex(RecoveryError, "Untracked"):
            self.journal.recover("missing", self.key)
        with self.assertRaisesRegex(RecoveryError, "another step"):
            self.journal.plan("review-renamed", self.key)

    def test_competing_workers_get_one_dispatch_grant(self):
        def compete(_):
            try:
                return StepJournal(self.path).start("review", self.key)
            except RecoveryError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            tokens = [t for t in pool.map(compete, range(12)) if t]
        self.assertEqual(len(tokens), 1)
        with self.assertRaisesRegex(RecoveryError, "already started"):
            self.journal.start("review", self.key)

    def test_damage_is_not_a_reason_to_reissue(self):
        token = self.journal.start("review", self.key)
        self.journal.commit("review", self.key, token, {"ok": True})
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE recovery_steps SET receipt='{}'")
        with self.assertRaisesRegex(RecoveryError, "SHA-256 mismatch"):
            self.journal.recover("review", self.key)
        with self.assertRaisesRegex(RecoveryError, "already started"):
            self.journal.start("review", self.key)

    def test_equal_visible_values_cannot_cross_record_version_or_source_snapshot(self):
        original = {"id":"row1", "value":1.1, "version":1, "snapshot_hash":"b"*64}
        for changed in ({"version":2}, {"snapshot_hash":"c"*64}):
            first = step_identity(self.key["anchor"], "calculate", {"operand":original})
            other = step_identity(self.key["anchor"], "calculate", {"operand":{**original, **changed}})
            step = next(iter(changed))
            # These cases are separate runs which legitimately calculate the same original input.
            journal = StepJournal(self.path.with_name(step + ".sqlite"))
            journal.plan(step, first)
            owner = journal.start(step, first)
            journal.commit(step, first, owner, {"value":1.1})
            with self.assertRaisesRegex(RecoveryError, "different inputs"):
                journal.recover(step, other)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.path, self.candidate, self.branch = [root / f for f in ("session.sqlite3", "candidate.json", "branch.json")]
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE reports(session_id,report_id,version,payload);"
                              "CREATE TABLE events(session_id,sequence,payload);")
        self.parent = {"id": "report", "version": 1, "episode_id": "parent-episode", "created_at": "2020-03-04T00:00:00Z"}
        raw = json.dumps(self.parent)
        self.db.execute("INSERT INTO reports VALUES ('s','report',1,?)", (raw,))
        self.ref = {"anchor_kind": "parent", "session_id": "s", "parent_report_id": "report", "parent_version": 1,
                    "parent_payload_sha256": hashlib.sha256(raw.encode()).hexdigest(), "event_sequence": 2,
                    "tool": "lookup", "arguments": {"id": "source"}}
        self.observation = {"data": {"network_requests": 0}, "evidence": [], "calculations": [], "error": "not_found"}
        self.event = {"type": "tool_call", "episode_id": "parent-episode", "created_at": "2020-03-03T00:00:00Z",
                      "event_sequence": 2, "tool": "lookup", "arguments": {"id": "source"},
                      "ok": False, "summary": "failed", "observation": self.observation,
                      "observation_sha256": digest(self.observation)}
        self.ref["event_payload_sha256"] = self.save_event(self.event)

    def save_event(self, event):
        raw = json.dumps(event)
        self.db.execute("DELETE FROM events WHERE sequence=?", (event["event_sequence"],))
        self.db.execute("INSERT INTO events VALUES ('s',?,?)", (event["event_sequence"], raw))
        self.db.commit()
        return hashlib.sha256(raw.encode()).hexdigest()

    def test_restore_failed_observation_without_promoting_to_success(self):
        recovered = archive_result(self.path, self.ref)
        self.assertEqual(recovered["state"], "completed")
        self.assertEqual(recovered["receipt"], {**self.observation, "ok": False, "summary": "failed"})

    def test_same_candidate_calculation_requires_input_version_witness(self):
        from verifin.recovery import _candidate_contains_result
        calc = {"id":"c", "value":1.1, "operand_ids":["row"], "operand_versions":{"row":1}}
        receipt = {"evidence":[], "calculations":[calc]}
        frozen = {"evidence":[{"id":"row","value":1.1,"version":1}], "calculations":[{**calc, "version":1}]}
        _candidate_contains_result(frozen, receipt)
        frozen["evidence"][0]["version"] = 2
        with self.assertRaisesRegex(RecoveryError, "different recorded version"):
            _candidate_contains_result(frozen, receipt)

    def test_parent_rejects_other_or_later_episode_or_changed_arguments_and_hash(self):
        for changed in ({"arguments": {}}, {"parent_payload_sha256": "0"*64}, {"event_payload_sha256": "0"*64}):
            with self.assertRaises(RecoveryError):
                archive_result(self.path, {**self.ref, **changed})
        for changed in ({"created_at": "2021-01-01T00:00:00Z"}, {"episode_id": "other"}, {"observation_sha256": "0"*64}):
            new_hash = self.save_event({**self.event, **changed})
            with self.assertRaises(RecoveryError):
                archive_result(self.path, {**self.ref, "event_payload_sha256": new_hash})

    def test_candidate_requires_exact_bytes_manifest_and_lineage(self):
        content = b'{"evidence":[{"id":"e","value":2,"version":1}]}'
        self.candidate.write_bytes(content)
        candidate_sha = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        manifest = {k: self.ref[k] for k in ("session_id", "parent_report_id", "parent_version")}
        manifest.update(candidate_sha256=candidate_sha, episode={"id":"child"})
        self.branch.write_text(json.dumps(manifest))
        branch_event = {"type": "report_branch", "episode_id": "child", "event_sequence": 1, "parent_report_id": "report"}
        receipt = {"ok": True, "data": {}, "evidence": [{"id":"e", "value":2}], "calculations": []}
        ref = {**self.ref, "anchor_kind": "candidate", "candidate_sha256": candidate_sha, "branch_sequence": 1,
               "branch_event_sha256": self.save_event(branch_event),
               "branch_file_sha256": hashlib.sha256(self.branch.read_bytes()).hexdigest(),
               "event_payload_sha256": self.save_event({**self.event, "type": "tool_result", "episode_id": "child", "result": receipt})}
        def restore():
            return archive_result(self.path, ref, candidate=self.candidate, branch=self.branch)
        self.assertEqual(restore()["receipt"], receipt)
        self.candidate.write_bytes(b'{ "claims":[]}')
        with self.assertRaisesRegex(RecoveryError, "Candidate SHA-256"):
            restore()
        self.candidate.write_bytes(content)
        ref["event_payload_sha256"] = self.save_event({**self.event, "type": "tool_result", "episode_id": "child",
                                                      "result": {**receipt, "evidence": [{"id":"e","value":3}]}})
        with self.assertRaisesRegex(RecoveryError, "candidate records"):
            restore()
        ref["branch_event_sha256"] = self.save_event({**branch_event, "parent_report_id": "other"})
        with self.assertRaisesRegex(RecoveryError, "outside"):
            restore()


if __name__ == "__main__":
    unittest.main()
