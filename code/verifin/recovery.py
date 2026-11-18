"""Offline step recovery for a pinned report/candidate and an exact operation.

The journal writer brackets an external call. Neither the reader nor the archive
importer can dispatch a call. Missing legacy history is never proof of no call.
"""

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .engine import digest


class RecoveryError(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _checked(raw, expected, label):
    if _sha(raw) != expected:
        raise RecoveryError(f"{label} SHA-256 mismatch")
    return json.loads(raw)


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def step_identity(anchor, operation, arguments):
    """Bind all semantic inputs, including tool/role settings and input snapshot hashes."""
    if not isinstance(anchor, dict) or not anchor or not isinstance(arguments, dict):
        raise RecoveryError("A pinned report or candidate and exact operation arguments are required")
    if set(anchor) - {"candidate_sha256", "parent_payload_sha256"} or not all(_valid_hash(h) for h in anchor.values()):
        raise RecoveryError("Invalid anchor hash or field")
    if not isinstance(operation, str) or not operation.strip():
        raise RecoveryError("An operation name is required")
    return {"anchor": anchor, "operation": operation, "arguments": arguments}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovery_steps (
    step TEXT PRIMARY KEY NOT NULL,
    identity TEXT UNIQUE NOT NULL,
    owner TEXT,
    receipt TEXT,
    receipt_sha256 TEXT,
    CHECK ((receipt IS NULL AND receipt_sha256 IS NULL) OR
           (receipt IS NOT NULL AND receipt_sha256 IS NOT NULL AND owner IS NOT NULL))
)
"""


class StepJournal:
    """Single SQLite journal for one run. Retain it across restarts and branches."""

    def __init__(self, path):
        self.path = Path(path)

    def _writer(self, *, create=False):
        uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _file_identity(self):
        st = self.path.stat()
        return _json([str(self.path.resolve()), st.st_dev, st.st_ino])

    def _attached(self, db):
        row = db.execute("SELECT file_identity FROM recovery_authority WHERE singleton=1").fetchone()
        return row is not None and row[0] == self._file_identity()

    def _require_writer(self, db):
        if not self._attached(db):
            raise RecoveryError("Detached journal; dispatch and writes require the original journal")

    def plan(self, step, identity):
        """Register a new logical step before dispatch. Repeating the plan never resets it."""
        if not isinstance(step, str) or not step.strip():
            raise RecoveryError("A stable step ID is required")
        encoded = _json(step_identity(**identity))
        with closing(self._writer(create=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS recovery_authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), file_identity TEXT NOT NULL)")
            db.execute(_SCHEMA)
            db.execute("INSERT INTO recovery_authority VALUES (1, ?) ON CONFLICT DO NOTHING", (self._file_identity(),))
            self._require_writer(db)
            try:
                db.execute("INSERT INTO recovery_steps(step, identity) VALUES (?, ?) ON CONFLICT(step) DO NOTHING", (step, encoded))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("This exact operation is already registered under another step") from exc
            self._require(db, step, encoded)

    @staticmethod
    def _require(db, step, encoded):
        row = db.execute("SELECT identity, owner, receipt, receipt_sha256 FROM recovery_steps WHERE step=?", (step,)).fetchone()
        if row is None:
            raise RecoveryError("Untracked step; dispatch history is unknown")
        if row[0] != encoded:
            raise RecoveryError("Step belongs to different inputs or anchor")
        return row[1:]

    def start(self, step, identity):
        """Grant one dispatch after its start marker commits; a second caller must stop."""
        encoded = _json(step_identity(**identity))
        owner = uuid.uuid4().hex
        with closing(self._writer()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._require_writer(db)
            updated = db.execute("UPDATE recovery_steps SET owner=? WHERE step=? AND identity=? AND owner IS NULL",
                                 (owner, step, encoded))
            if updated.rowcount != 1:
                self._require(db, step, encoded)
                raise RecoveryError("Step already started; recover its state")
        return owner

    def commit(self, step, identity, owner, receipt):
        """Store a successful or failed result. Only the original attempt may commit."""
        encoded = _json(step_identity(**identity))
        if not isinstance(receipt, dict) or not receipt:
            raise RecoveryError("A nonempty result envelope is required")
        body = _json(receipt)
        checksum = _sha(body.encode())
        with closing(self._writer()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._require_writer(db)
            expected, previous, previous_sha = self._require(db, step, encoded)
            if not owner or owner != expected:
                raise RecoveryError("Receipt owner differs from the recorded attempt")
            if previous is None:
                db.execute("UPDATE recovery_steps SET receipt=?, receipt_sha256=? WHERE step=?", (body, checksum, step))
            elif previous != body or previous_sha != checksum:
                raise RecoveryError("A different or damaged receipt is already committed")

    def recover(self, step, identity):
        """Read-only lookup. A recorded start with no durable result always remains unknown."""
        encoded = _json(step_identity(**identity))
        with closing(_read_only(self.path)) as db:
            db.execute("BEGIN")
            owner, receipt, checksum = self._require(db, step, encoded)
            if receipt is None and not self._attached(db):
                return {"state": "outcome_unknown", "reason": "detached_journal"}
        if receipt is None:
            return {"state": "outcome_unknown" if owner else "not_started"}
        return {"state": "completed", "receipt_sha256": checksum,
                "receipt": _checked(receipt.encode(), checksum, "Receipt")}


def _event(db, session, seq, sha):
    row = db.execute("SELECT payload FROM events WHERE session_id=? AND sequence=?", (session, seq)).fetchone()
    if row is None:
        raise RecoveryError("Pinned event missing")
    event = _checked(row[0].encode(), sha, "Event")
    if event.get("event_sequence") != seq:
        raise RecoveryError("Event sequence mismatch")
    return event


def _timestamp(value):
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise RecoveryError("Archive time has no explicit timezone")
    return result


def _candidate_contains_result(candidate, result):
    """Older candidates have no event watermark. Require their actual records as witnesses."""
    has_record = False
    for key in ("evidence", "calculations"):
        saved = {record["id"]: record for record in candidate.get(key, [])}
        for item in result[key]:
            record = saved.get(item["id"])
            if record is None or any(k not in record or _json(record[k]) != _json(v) for k, v in item.items()):
                raise RecoveryError("Tool output does not match the pinned candidate records")
            has_record = True
    if not has_record:
        raise RecoveryError("This legacy candidate carries no record from this tool result")
    evidence = {e["id"]: e for e in candidate.get("evidence", [])}
    calculations = {c["id"]: c for c in candidate.get("calculations", [])}
    operands = {**evidence, **calculations}
    for calc in result["calculations"]:
        for operand_id in calc.get("operand_ids", []):
            operand = operands.get(operand_id)
            expected = calc.get("operand_versions", {}).get(operand_id)
            if operand is None or expected is None or operand.get("version") != expected:
                raise RecoveryError("Candidate operand missing or at a different recorded version")


def archive_result(database, reference, *, candidate=None, branch=None):
    """Reopen a tool result in its original episode, without changing the source DB."""
    sid, seq = reference["session_id"], reference["event_sequence"]
    with closing(_read_only(database)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM reports WHERE session_id=? AND report_id=? AND version=?",
                         (sid, reference["parent_report_id"], reference["parent_version"])).fetchone()
        if row is None:
            raise RecoveryError("Pinned parent report missing")
        parent = _checked(row[0].encode(), reference["parent_payload_sha256"], "Parent report")
        if (parent.get("id"), parent.get("version")) != (reference["parent_report_id"], reference["parent_version"]):
            raise RecoveryError("Parent report identity mismatch")
        event = _event(db, sid, seq, reference["event_payload_sha256"])
        if reference["anchor_kind"] == "candidate":
            if candidate is None or branch is None:
                raise RecoveryError("Both frozen candidate and branch manifest are required")
            frozen = _checked(Path(candidate).read_bytes(), reference["candidate_sha256"], "Candidate")
            manifest = _checked(Path(branch).read_bytes(), reference["branch_file_sha256"], "Branch manifest")
            for field in ("session_id", "parent_report_id", "parent_version", "candidate_sha256"):
                if manifest.get(field) != reference[field]:
                    raise RecoveryError(f"Branch and anchor disagree on {field}")
            branch_event = _event(db, sid, reference["branch_sequence"], reference["branch_event_sha256"])
            expected_episode = manifest["episode"]["id"]
            if (branch_event.get("type") != "report_branch" or branch_event.get("episode_id") != expected_episode
                    or branch_event.get("parent_report_id") != parent["id"] or branch_event["event_sequence"] >= seq):
                raise RecoveryError("Result outside the pinned report branch")
        elif reference["anchor_kind"] == "parent":
            expected_episode = parent["episode_id"]
            if candidate is not None or branch is not None:
                raise RecoveryError("Unexpected branch files for parent history")
            if _timestamp(event["created_at"]) > _timestamp(parent["created_at"]):
                raise RecoveryError("Event was recorded after the pinned parent")
        else:
            raise RecoveryError("Unknown archive anchor kind")
    if event.get("episode_id") != expected_episode:
        raise RecoveryError("Result belongs to another episode")
    if event.get("tool") != reference["tool"] or _json(event.get("arguments")) != _json(reference["arguments"]):
        raise RecoveryError("Result belongs to different tool arguments")
    if event["type"] == "tool_result":
        result = event["result"]
    elif event["type"] == "tool_call" and "observation" in event:
        observation = event["observation"]
        if digest(observation) != event.get("observation_sha256"):
            raise RecoveryError("Stored observation checksum mismatch")
        result = {**observation, "ok": event["ok"], "summary": event["summary"]}
    else:
        raise RecoveryError("Event contains no complete durable tool observation")
    if (not isinstance(result, dict) or type(result.get("ok")) is not bool
            or not isinstance(result.get("data"), dict)
            or not all(isinstance(result.get(k), list) for k in ("evidence", "calculations"))):
        raise RecoveryError("Invalid tool result envelope")
    if reference["anchor_kind"] == "candidate":
        _candidate_contains_result(frozen, result)
    return {"state": "completed", "session_id": sid, "episode_id": expected_episode,
            "event_sequence": seq, "event_payload_sha256": reference["event_payload_sha256"], "receipt": result}


def _archived_critic_attempts(database, case_dir, reference, history):
    sid, candidate_sha = reference["session_id"], reference["candidate_sha256"]
    first = _checked((case_dir/"attempt.json").read_bytes(), history["attempt_file_sha256"], "Original attempt")
    retry = _checked((case_dir/"retry.json").read_bytes(), history["retry_file_sha256"], "Later attempt")
    review = _checked((case_dir/"review.json").read_bytes(), history["review_file_sha256"], "Review")
    branch = _checked((case_dir/"branch.json").read_bytes(), reference["branch_file_sha256"], "Branch manifest")
    episode = branch["episode"]["id"]
    if any(x["episode_id"] != episode or x["candidate_sha256"] != candidate_sha for x in (first, retry)):
        raise RecoveryError("Critic attempt belongs to a different candidate or episode")
    with closing(_read_only(database)) as db:
        db.execute("BEGIN")
        events = [_event(db, sid, history[key]["sequence"], history[key]["sha256"])
                  for key in ("lost_attempt", "later_usage", "later_review")]
    lost, usage, verified = events
    if ([row["type"] for row in events] != ["model_attempt_usage_unknown", "model_call", "verification"]
            or any(row["episode_id"] != episode for row in events)
            or lost.get("role") != "critic" or usage.get("role") != "critic" or usage.get("ok") is not True
            or not reference["event_sequence"] < lost["event_sequence"] < usage["event_sequence"] < verified["event_sequence"]):
        raise RecoveryError("Historic attempt events do not match the required ordered lifecycle")
    times = [_timestamp(x) for x in (first["started"], lost["created_at"], retry["started"], usage["created_at"], verified["created_at"])]
    if times != sorted(times) or times[0] >= times[2]:
        raise RecoveryError("Historic attempt timestamps disagree with marker order")
    if set(review) != {"issues", "reviewed_claim_ids", "reviewed_requirement_ids"} or any(review[k] != verified.get(k) for k in review):
        raise RecoveryError("Archived critique differs from the committed verification")
    return [
        {"attempt": 1, "state": "outcome_unknown", "sequence": lost["event_sequence"], "usage": None,
         "action": "保留首笔费用未知与已有预留。等待原回执或人工核对账单。"},
        {"attempt": 2, "state": "completed", "usage_sequence": usage["event_sequence"], "verification_sequence": verified["event_sequence"],
         "usage": usage["usage"], "reviewed_claims": len(review["reviewed_claim_ids"]), "reviewed_requirements": len(review["reviewed_requirement_ids"]),
         "action": "可对这份候选稿读取后来已提交的独立评审。首笔仍单独列账。"},
    ]


def recovery_advice(case_dir, manifest_path):
    """Human-readable read-only advice from pinned local case files. Never infer a retry."""
    case_dir, manifest_path = Path(case_dir), Path(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("format") != "verifin-recovery-advice-v1" or not manifest.get("references"):
        raise RecoveryError("Unsupported or empty recovery manifest")
    database = case_dir / "session.sqlite3"
    summary, candidate_ref, common_parent = [], None, None
    for entry in manifest["references"]:
        path = manifest_path.parent / entry["file"]
        ref = _checked(path.read_bytes(), entry["file_sha256"], "Reference")
        parent = tuple(ref[k] for k in ("session_id", "parent_report_id", "parent_version", "parent_payload_sha256"))
        if common_parent is not None and common_parent != parent:
            raise RecoveryError("The advice references belong to different parents")
        common_parent = parent
        files = {}
        if ref["anchor_kind"] == "candidate":
            if candidate_ref is not None:
                raise RecoveryError("An advice bundle can address only one candidate result")
            candidate_ref = ref
            files = {"candidate": case_dir/"candidate.json", "branch": case_dir/"branch.json"}
        result = archive_result(database, ref, **files)
        body = result["receipt"]
        summary.append({"reference": entry["file"], "state": result["state"], "ok": body["ok"],
                        "tool": ref["tool"], "episode_id": result["episode_id"], "event_sequence": result["event_sequence"],
                        "event_payload_sha256": result["event_payload_sha256"], "evidence_ids": [e["id"] for e in body["evidence"]],
                        "calculations": [{k: c[k] for k in ("id","value","unit","operand_ids","operand_versions") if k in c}
                                         for c in body["calculations"]], "error": body.get("error"),
                        "action": ("从该事件读取已有证据或计算。" if body["ok"] else "保留原来的失败和错误原因。")})
    advice = {"dispatch_performed": False, "retry_scheduled": False, "database": str(database.resolve()), "tool_results": summary}
    if "historic_critic" in manifest:
        if candidate_ref is None:
            raise RecoveryError("A historic critique requires a verified candidate result")
        advice.update(candidate_sha256=candidate_ref["candidate_sha256"], parent_report_id=candidate_ref["parent_report_id"],
                      parent_version=candidate_ref["parent_version"],
                      historic_critic=_archived_critic_attempts(database, case_dir, candidate_ref, manifest["historic_critic"]))
    return advice


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    archive = subs.add_parser("archive", help="Read a pinned historical VeriFin tool event")
    archive.add_argument("--database", required=True, type=Path)
    archive.add_argument("--reference", required=True, type=Path)
    archive.add_argument("--candidate", type=Path)
    archive.add_argument("--branch", type=Path)
    status = subs.add_parser("status", help="Read one planned step from an existing recovery journal")
    status.add_argument("--database", required=True, type=Path)
    status.add_argument("--identity", required=True, type=Path)
    status.add_argument("--step", required=True)
    advice = subs.add_parser("diagnose", help="Build a read-only recovery handoff from a pinned case bundle")
    advice.add_argument("--case", required=True, type=Path)
    advice.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "archive":
            value = archive_result(args.database, json.loads(args.reference.read_bytes()),
                                   candidate=args.candidate, branch=args.branch)
        elif args.command == "diagnose":
            value = recovery_advice(args.case, args.manifest)
        else:
            value = StepJournal(args.database).recover(args.step, json.loads(args.identity.read_bytes()))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, f"Recovery stopped: {exc}\n")
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
