"""Durable, session-isolated research state using only SQLite and JSON.

Record payloads are append-only.  Invalidation is recorded separately so source
corrections never rewrite the evidence behind an earlier report.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator
from uuid import uuid4


_KINDS = {"evidence", "calculation", "claim", "document_source"}
_RESERVED = {"version", "created_at", "updated_at", "stale", "invalidation_reasons"}
_SOURCE_FIELDS = ("source_document_id", "source_revision", "source_observed_at", "source_revision_authoritative")

_CRITIC_RECEIPT_ERRORS = {"invalid_critic_json", "unknown_reference", "unknown_issue_target", "unknown_issue_category",
                          "review_coverage_mismatch", "invalid_role_output", "missing_or_invalid_reviewed_claim_ids",
                          "missing_or_invalid_reviewed_requirement_ids", "missing_or_invalid_critic_issues"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _string(value: Any, field: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _json_copy(value: Any) -> Any:
    """Reject non-JSON objects/NaN and detach input from caller mutations."""
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("state must contain finite JSON-serializable values") from exc


def _ids(value: Any, field: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    for identifier in value:
        _string(identifier, field)
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicate IDs")


def _timestamp(value: Any, field: str, *, date_only: bool = False) -> None:
    if value is None:
        return
    _string(value, field)
    try:
        if date_only:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date or timestamp") from exc


def _period(record: dict) -> None:
    for name in ("period_start", "period_end"):
        if name in record:
            _timestamp(record[name], name, date_only=True)
    if record.get("period_start") and record.get("period_end"):
        if record["period_start"] > record["period_end"]:
            raise ValueError("period_start must not follow period_end")


def _number(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")


def _source_observation(observation: Any) -> dict:
    if not isinstance(observation, dict):
        raise ValueError("source observation must be an object")
    result = _json_copy(observation)
    for field in ("source_document_id", "source_revision", "source_observed_at", "source_url"):
        _string(result.get(field), field)
    if not isinstance(result.get("source_revision_authoritative"), bool):
        raise ValueError("source_revision_authoritative must be boolean")
    _source_time(result["source_observed_at"])
    return {key: result[key] for key in (*_SOURCE_FIELDS, "source_url")}


def _source_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("source_observed_at requires an ISO timestamp with timezone") from exc


_SUMMARY_REVISION = 1  # Version of the compact view's answer-to-question checks.


def _report_summary(report: dict) -> dict:
    """Small persisted metadata view; never embed claims or source snapshots."""
    fields = ("id", "version", "created_at", "episode_id", "request", "status", "stop_reason",
              "scope", "scope_version", "verification_available", "usage", "model_mode")
    result = {key: report[key] for key in fields if key in report}
    # The full report separately labels its Researcher draft. Its opening text
    # can contradict the Critic's final findings, even in a completed episode.
    # Keep the compact list view bounded and copy only a whole reviewed claim.
    if report.get("request_parts"):
        from .request_review import coverage_status
        from .reporting import question_answers
        answers = question_answers(report)
        result.update(coverage_status(report,answers))
        result["question_counts"] = {status:sum(row["status"] == status for row in answers)
                                     for status in ("answered","gap","pending")}
        candidates = ((r.get("answer_sentence"),r.get("claim_id")) for r in answers
                      if r.get("status") == "answered")
    else:
        gaps = {r["id"] for r in report.get("unanswered",[]) if isinstance(r,dict) and "id" in r}
        candidates = ((c.get("text"),c.get("id")) for c in (report.get("claims") or []) if isinstance(c, dict)
                      and c.get("status") == "Supported" and not c.get("stale")
                      and (not gaps or set(c.get("requirement_ids",[])) - gaps))
    excerpt = next(((text, ref) for text, ref in candidates if isinstance(text, str) and 0 < len(text.strip()) <= 640), None)
    if report.get("verification_available") and excerpt:
        result.update(summary=excerpt[0], summary_kind="supported_claim_excerpt", summary_claim_id=excerpt[1])
    else:
        result["summary_kind"] = "see_report_findings"
    result["claim_count"] = len(report.get("claims", [])) if isinstance(report.get("claims", []), list) else 0
    result["unanswered_count"] = len(report.get("unanswered", [])) if isinstance(report.get("unanswered", []), list) else 0
    return result


def _limit(value: int | None, field: str = "limit") -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{field} must be a non-negative integer or None")


def _validate_scope(scope: Any) -> dict:
    if not isinstance(scope, dict):
        raise ValueError("scope must be an object")
    result = _json_copy(scope)
    for field in ("entities", "metrics"):
        if field in result:
            _ids(result[field], field)
    _period(result)
    if "cutoff" in result:
        _timestamp(result["cutoff"], "cutoff")
    if "requirements" in result:
        if not isinstance(result["requirements"], list):
            raise ValueError("requirements must be a list")
        seen = set()
        for item in result["requirements"]:
            if not isinstance(item, dict):
                raise ValueError("each requirement must be an object")
            _string(item.get("id"), "requirement id")
            _string(item.get("text"), "requirement text")
            if item["id"] in seen:
                raise ValueError("requirement IDs must be unique")
            seen.add(item["id"])
    return result


def _validate_record(kind: str, record: Any) -> dict:
    if not isinstance(record, dict):
        raise ValueError(f"{kind} must be an object")
    result = _json_copy(record)
    _string(result.get("id"), "id")
    _period(result)
    if kind == "evidence":
        if result.get("kind") not in {"financial_fact", "document", "market_price", "source_metadata"}:
            raise ValueError("unknown evidence kind")
        if result["kind"] == "source_metadata" and result.get("value") is not None:
            raise ValueError("source metadata cannot be a numeric operand")
        _string(result.get("entity"), "entity")
        for field in ("published_at", "source_url", "locator", "snapshot_hash"):
            if field not in result:
                raise ValueError(f"evidence requires explicit {field} (null is allowed)")
            _string(result[field], field, nullable=True)
        _timestamp(result["published_at"], "published_at")
        if result.get("value") is not None:
            _number(result["value"], "value")
        for field in ("metric", "unit", "text"):
            if field in result:
                _string(result[field], field, nullable=True)
        if any(field in result for field in _SOURCE_FIELDS):
            if result["kind"] != "document":
                raise ValueError("document source lineage is only valid on document evidence")
            observation = _source_observation(result)
            if observation["source_revision"] != result["snapshot_hash"]:
                raise ValueError("source_revision must equal the raw document snapshot_hash")
    elif kind == "calculation":
        for field in ("operation", "unit", "formula"):
            _string(result.get(field), field)
        _ids(result.get("operand_ids"), "operand_ids")
        if not result["operand_ids"]:
            raise ValueError("a calculation requires at least one operand")
        _number(result.get("value"), "value")
    else:
        _string(result.get("text"), "text")
        for field in ("evidence_ids", "calculation_ids", "requirement_ids"):
            result.setdefault(field, [])
            _ids(result[field], field)
        result.setdefault("constraints", {})
        if not isinstance(result["constraints"], dict):
            raise ValueError("constraints must be an object")
        _period(result["constraints"])
        if result.get("asserted_value") is not None:
            _number(result["asserted_value"], "asserted_value")
    for field in ("operand_versions", "evidence_versions", "calculation_versions"):
        if field in result:
            mapping = result[field]
            if not isinstance(mapping, dict) or any(
                not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, int) or v < 1
                for k, v in mapping.items()
            ):
                raise ValueError(f"{field} must map IDs to positive version integers")
    if "scope_version" in result:
        v = result["scope_version"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ValueError("scope_version must be a positive integer")
    if "stale" in result and not isinstance(result["stale"], bool):
        raise ValueError("stale must be boolean")
    return result


class SessionStore:
    """SQLite persistence. One instance can be shared by multiple threads.

    Separate instances/processes coordinate writes through SQLite transactions.
    The root controller remains responsible for preventing two research runs in
    one session; individual storage operations never hold a long-running lease.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self.path = str(Path(self.path).expanduser())
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._enable_wal()
            self._db.execute("PRAGMA synchronous=FULL")
            with self._transaction():
                version = self._db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2, 3):
                    raise ValueError(f"unsupported database schema version: {version}")
                for statement in _SCHEMA:
                    self._db.execute(statement)
                event_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(events)")}
                if "event_type" not in event_columns:
                    self._db.execute("ALTER TABLE events ADD COLUMN event_type TEXT")
                    # Version 1 stored unwrapped event dictionaries already.
                    # Backfill their type once without relying on JSON1 support.
                    for row in self._db.execute("SELECT session_id,sequence,payload FROM events").fetchall():
                        event_type = json.loads(row["payload"]).get("type")
                        if isinstance(event_type, str):
                            self._db.execute("UPDATE events SET event_type=? WHERE session_id=? AND sequence=?",
                                             (event_type, row["session_id"], row["sequence"]))
                self._db.execute("CREATE INDEX IF NOT EXISTS events_by_type ON events(session_id,event_type,sequence DESC)")
                report_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(reports)")}
                for column in ("report_id", "summary"):
                    if column not in report_columns:
                        self._db.execute(f"ALTER TABLE reports ADD COLUMN {column} TEXT")
                if "summary_revision" not in report_columns:
                    self._db.execute("ALTER TABLE reports ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0")
                # Rebuild only derived views. Read one full report at a time and keep its archived bytes.
                outdated = self._db.execute(
                    "SELECT session_id,version FROM reports WHERE report_id IS NULL OR summary IS NULL OR summary_revision<?",
                    (_SUMMARY_REVISION,),
                ).fetchall()
                for key in outdated:
                    payload = self._db.execute("SELECT payload FROM reports WHERE session_id=? AND version=?", tuple(key)).fetchone()
                    report = json.loads(payload[0])
                    self._db.execute("UPDATE reports SET report_id=?,summary=?,summary_revision=? WHERE session_id=? AND version=?", (
                        report["id"], json.dumps(_report_summary(report), ensure_ascii=False), _SUMMARY_REVISION, *key,
                    ))
                self._db.execute("CREATE INDEX IF NOT EXISTS reports_by_id ON reports(session_id,report_id,version DESC)")
                self._db.execute("PRAGMA user_version=3")
        except BaseException:
            self.close()
            raise

    def _enable_wal(self) -> None:
        """Retry only startup lock contention, with one 30-second total budget.

        Concurrent first connections can contend while switching journal mode.
        SQLite may return BUSY without invoking its busy handler when waiting
        could deadlock, so busy_timeout alone does not cover this transition.
        No transaction has started; retrying this idempotent PRAGMA cannot replay
        application writes. Restore the ordinary write timeout on every exit.
        """
        deadline = time.monotonic() + 30.0
        delay = 0.01
        self._db.execute("PRAGMA busy_timeout=0")
        try:
            while True:
                try:
                    cursor = self._db.execute("PRAGMA journal_mode=WAL")
                    try:
                        cursor.fetchone()
                    finally:
                        cursor.close()
                    return
                except sqlite3.OperationalError as exc:
                    # Extended result codes retain the primary code in the
                    # low byte. Never retry corruption, permissions or I/O.
                    code = getattr(exc, "sqlite_errorcode", None)
                    if code is None or (code & 0xff) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(min(delay, remaining))
                    delay = min(delay * 2, 0.1)
        finally:
            self._db.execute("PRAGMA busy_timeout=30000")

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise RuntimeError("SessionStore is closed")
            # A returned tool result or semantic review groups existing store
            # operations. Inner failures undo only their own work; the caller
            # can still commit the receipt and a known source invalidation.
            if self._db.in_transaction:
                self._db.execute("SAVEPOINT store_operation")
                try:
                    yield
                except BaseException:
                    if self._db.in_transaction:
                        self._db.execute("ROLLBACK TO store_operation")
                    raise
                finally:
                    if self._db.in_transaction:
                        self._db.execute("RELEASE store_operation")
                return
            self._db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield
                self._db.execute("COMMIT")
            except BaseException:
                # SQLite can already roll back on I/O failure. Do not mask the
                # original failure with "no transaction is active".
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def atomic_step(self):
        """Group local writes after I/O; never hold this boundary across an external call."""
        return self._transaction()

    def read_snapshot(self):
        """Read related session data at the same committed SQLite revision."""
        return self._transaction(write=False)

    def matches_role_inputs(self, sid, scope_version, records):
        """Check the exact version and invalidation state originally presented to a role."""
        with self._transaction(write=False):
            if self._session(sid)["scope_version"] != scope_version:
                return False
            for kind, item in records:
                current = self._find(sid, kind, item["id"])
                if (current is None or current["version"] != item.get("version")
                        or bool(current.get("stale")) != bool(item.get("stale"))):
                    return False
        return True

    def record_tool_return(self, session_id: str, event: dict, *, record_versions=None) -> bool:
        """Commit the receipt and return whether an authoritative source or record version changed."""
        payload = event["observation"]
        changed = False
        error = None
        written = {}
        with self.atomic_step():
            try:
                if not isinstance(payload.get("data"), dict):
                    raise ValueError("Tool observation data must be an object")
                source = payload["data"].get("source_observation")
                if source is not None:
                    changed = self.observe_document_source(session_id, source)["changed"]
                if event["ok"]:
                    with self.atomic_step():
                        for kind, items in (("evidence", payload["evidence"]), ("calculation", payload["calculations"])):
                            if not isinstance(items, list):
                                raise ValueError("Tool observation evidence and calculations must be lists")
                            for item in items:
                                identity = item.get("id") if isinstance(item, dict) else None
                                prior = self._find(session_id, kind, identity) if not changed and isinstance(identity, str) else None
                                saved = self._put(session_id, kind, item)
                                written[saved["id"]] = saved["version"]
                                if prior is None or saved["version"] != prior["version"]:
                                    changed = True
            except (ValueError, KeyError, TypeError) as exc:
                # Preserve an authoritative revision even if one of the
                # attached records was invalid. Do not publish a partial batch.
                error = exc
            self.add_event(session_id, {**event, "ingestion_status":"rejected" if error else "committed",
                                        "record_versions":{} if error else written})
            if error is not None:
                self.add_event(session_id, {"type":"tool_ingestion_error", "episode_id":event["episode_id"],
                                            "tool":event["tool"], "error_type":type(error).__name__})
        if error is not None:
            raise error
        if record_versions is not None:
            record_versions.update(written)
        return changed

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _session(self, session_id: str) -> dict:
        _string(session_id, "session_id")
        row = self._db.execute("SELECT s.*, c.max_context_chars FROM sessions s LEFT JOIN session_context_limits c "
                               "ON s.id=c.session_id WHERE s.id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        return {
            "id": row["id"], "title": row["title"], "scope": json.loads(row["scope"]),
            "scope_version": row["scope_version"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "cancelled": bool(row["cancelled"]),
            **({"max_context_chars":row["max_context_chars"]} if row["max_context_chars"] is not None else {}),
        }

    def _touch(self, session_id: str) -> None:
        self._db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), session_id))

    def create_session(self, title: str = "Untitled") -> dict:
        _string(title, "title")
        session_id, now = uuid4().hex, _now()
        with self._transaction():
            self._db.execute(
                "INSERT INTO sessions VALUES (?,?,?,1,?,?,0)",
                (session_id, title, "{}", now, now),
            )
            return self._session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._transaction(write=False):
            return self._session(session_id)

    def set_context_limit(self, session_id: str, characters: int) -> dict:
        if type(characters) is not int or not 1 <= characters <= 2**63 - 1:
            raise ValueError("Context character limit must be a positive SQLite integer.")
        with self._transaction():
            self._db.execute("INSERT INTO session_context_limits(session_id,max_context_chars) VALUES (?,?) "
                             "ON CONFLICT(session_id) DO UPDATE SET max_context_chars=excluded.max_context_chars",
                             (session_id, characters))
            return self._session(session_id)

    def list_sessions(self) -> list[dict]:
        with self._transaction(write=False):
            return [self._session(row["id"]) for row in self._db.execute(
                "SELECT id FROM sessions ORDER BY updated_at DESC,id"
            ).fetchall()]

    def update_scope(self, session_id: str, scope: dict) -> dict:
        """Replace the resolved scope; the controller merges patches beforehand."""
        scope = _validate_scope(scope)
        with self._transaction():
            previous = self._session(session_id)
            if scope == previous["scope"]:
                return previous
            self._db.execute(
                "UPDATE sessions SET scope=?,scope_version=scope_version+1,updated_at=? WHERE id=?",
                (json.dumps(scope, ensure_ascii=False), _now(), session_id),
            )
            changed = {key for key in set(previous["scope"]) | set(scope)
                       if previous["scope"].get(key) != scope.get(key)}
            for kind in ("calculation", "claim"):
                for record in self._latest(session_id, kind):
                    if self._scope_affects(record, kind, previous["scope"], scope, changed):
                        self._invalidate(session_id, kind, record, "scope_changed")
            self._cascade_stale(session_id)
            self._event(session_id, {"type": "scope_changed", "scope": scope,
                                     "scope_version": previous["scope_version"] + 1})
            return self._session(session_id)

    @staticmethod
    def _scope_affects(record: dict, kind: str, old: dict, new: dict, changed: set) -> bool:
        semantic = changed - {"display_text", "display", "description"}
        if not semantic:
            return False
        context = record.get("constraints", {}) if kind == "claim" else record
        if "entities" in semantic:
            entity = context.get("entity")
            # An expansion preserves calculations tied to retained entities; a
            # comparative narrative must be rechecked for its new answer scope.
            if kind == "claim" or not entity or entity not in new.get("entities", []):
                return True
        for field in ("period_start", "period_end"):
            if field in semantic and (not context.get(field) or context.get(field) != new.get(field)):
                return True
        if "cutoff" in semantic:
            return True
        if "metrics" in semantic:
            metric = context.get("metric")
            if kind == "claim" or not metric or metric not in new.get("metrics", []):
                return True
        if "requirements" in semantic and kind == "claim":
            return True
        # Unrecognized scope constraints may change the accounting definition.
        known = {"entities", "period_start", "period_end", "cutoff", "metrics", "requirements"}
        return bool(semantic - known)

    def append_message(self, session_id: str, role: str, content: str) -> dict:
        _string(role, "role")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        with self._transaction():
            session = self._session(session_id)
            seq = self._db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            message = {"id": uuid4().hex, "role": role, "content": content,
                       "sequence": seq, "created_at": _now()}
            if role == "assistant":
                message["research_cutoff"] = session["scope"].get("cutoff")
            self._db.execute("INSERT INTO messages VALUES (?,?,?)",
                             (session_id, seq, json.dumps(message, ensure_ascii=False)))
            self._touch(session_id)
            return message

    def _decode(self, session_id: str, kind: str, row: sqlite3.Row) -> dict:
        record = json.loads(row["payload"])
        reasons = self._db.execute(
            "SELECT reason FROM invalidations WHERE session_id=? AND kind=? AND record_id=? AND version=? ORDER BY created_at,reason",
            (session_id, kind, record["id"], record["version"]),
        ).fetchall()
        record["stale"] = bool(record.get("stale") or reasons)
        if reasons:
            record["invalidation_reasons"] = [r["reason"] for r in reasons]
        return record

    def _latest(self, session_id: str, kind: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT r.payload FROM records r JOIN (SELECT record_id,MAX(version) version "
            "FROM records WHERE session_id=? AND kind=? GROUP BY record_id) latest "
            "ON r.record_id=latest.record_id AND r.version=latest.version "
            "WHERE r.session_id=? AND r.kind=? ORDER BY r.created_at,r.record_id",
            (session_id, kind, session_id, kind),
        ).fetchall()
        return [self._decode(session_id, kind, row) for row in rows]

    def _find(self, session_id: str, kind: str, identifier: str) -> dict | None:
        row = self._db.execute(
            "SELECT payload FROM records WHERE session_id=? AND kind=? AND record_id=? ORDER BY version DESC LIMIT 1",
            (session_id, kind, identifier),
        ).fetchone()
        return self._decode(session_id, kind, row) if row is not None else None

    def _invalidate(self, session_id: str, kind: str, record: dict, reason: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO invalidations VALUES (?,?,?,?,?,?)",
                         (session_id, kind, record["id"], record["version"], reason, _now()))

    def _cascade_stale(self, session_id: str) -> None:
        """Propagate through arbitrary calculation depth before checking claims."""
        evidence = {r["id"]: r for r in self._latest(session_id, "evidence")}
        calculations = {r["id"]: r for r in self._latest(session_id, "calculation")}
        while True:
            changed = False
            for record in calculations.values():
                if record.get("stale"):
                    continue
                for identifier in record["operand_ids"]:
                    operand = evidence.get(identifier) or calculations.get(identifier)
                    expected = record.get("operand_versions", {}).get(identifier)
                    if operand is None or operand.get("stale") or expected != operand["version"]:
                        self._invalidate(session_id, "calculation", record, f"operand_changed:{identifier}")
                        record["stale"] = True
                        changed = True
                        break
            if not changed:
                break
        for record in self._latest(session_id, "claim"):
            for field, versions, records in (
                ("evidence_ids", "evidence_versions", evidence),
                ("calculation_ids", "calculation_versions", calculations),
            ):
                for identifier in record[field]:
                    dependency = records.get(identifier)
                    if dependency is None or dependency.get("stale") or record.get(versions, {}).get(identifier) != dependency["version"]:
                        self._invalidate(session_id, "claim", record, f"dependency_changed:{identifier}")

    def _put(self, session_id: str, kind: str, record: dict) -> dict:
        record = _validate_record(kind, record)
        requested_stale = record.get("stale", False)
        with self._transaction():
            session = self._session(session_id)
            if kind == "evidence" and record.get("kind") == "document" and record.get("source_revision_authoritative"):
                observation = _source_observation(record)
                self._observe_document_source(session_id, observation)
                current = self._find(session_id, "document_source", observation["source_document_id"])
                if current["source_revision"] != observation["source_revision"]:
                    requested_stale = True
            previous = self._find(session_id, kind, record["id"])
            if kind in ("evidence", "calculation"):
                other = "calculation" if kind == "evidence" else "evidence"
                if self._find(session_id, other, record["id"]) is not None:
                    raise ValueError("evidence and calculation IDs must not collide within a session")
            record = {key: value for key, value in record.items() if key not in _RESERVED}
            if kind == "evidence":
                # A source snapshot belongs to its own entity/period, not the
                # currently selected answer scope. Reusing it must be a no-op.
                record.pop("scope_version", None)
            else:
                record.setdefault("scope_version", session["scope_version"])
            if kind == "calculation":
                versions = {}
                for identifier in record["operand_ids"]:
                    if identifier == record["id"]:
                        raise ValueError("calculation cannot depend on itself")
                    operand = self._find(session_id, "evidence", identifier) or self._find(session_id, "calculation", identifier)
                    if operand is None or operand.get("stale"):
                        raise ValueError(f"missing or stale calculation operand: {identifier}")
                    versions[identifier] = operand["version"]
                    if self._depends_on(session_id, identifier, record["id"], set()):
                        raise ValueError("calculation dependencies cannot form a cycle")
                supplied = record.get("operand_versions")
                if supplied is not None and supplied != versions:
                    raise ValueError("operand_versions do not match current source versions")
                record["operand_versions"] = versions
            elif kind == "claim":
                for source_kind, field, versions_field in (
                    ("evidence", "evidence_ids", "evidence_versions"),
                    ("calculation", "calculation_ids", "calculation_versions"),
                ):
                    versions = {}
                    for identifier in record[field]:
                        found = self._find(session_id, source_kind, identifier)
                        if found is not None:
                            versions[identifier] = found["version"]
                    record.setdefault(versions_field, versions)
            if previous:
                prior_content = {k: v for k, v in previous.items() if k not in _RESERVED}
                current_content = record
                if kind == "evidence":
                    # Query ranking and fetch time do not change the source.
                    # Keep the original metadata; new retrieval observations
                    # remain inspectable in the controller's tool event log.
                    retrieval_metadata = {"retrieval_score", "retrieved_at"}
                    if record.get("source_revision_authoritative") is True:
                        retrieval_metadata.update({"source_observed_at", "mode"})
                    prior_content = {k: v for k, v in prior_content.items() if k not in retrieval_metadata}
                    current_content = {k: v for k, v in record.items() if k not in retrieval_metadata}
                if prior_content == current_content and previous.get("stale") == requested_stale:
                    return previous
            record["version"] = previous["version"] + 1 if previous else 1
            record["created_at"] = _now()
            # Updating a status on a fetched stale record cannot erase its
            # invalidation. A newly synthesized/recomputed record omits this
            # flag and is checked against current dependency versions below.
            record["stale"] = requested_stale
            self._db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (
                session_id, kind, record["id"], record["version"],
                json.dumps(record, ensure_ascii=False), record["created_at"],
            ))
            self._cascade_stale(session_id)
            self._touch(session_id)
            return self._find(session_id, kind, record["id"])

    def _depends_on(self, session_id: str, identifier: str, target: str, visited: set) -> bool:
        if identifier == target:
            return True
        if identifier in visited:
            return False
        visited.add(identifier)
        record = self._find(session_id, "calculation", identifier)
        return bool(record and any(self._depends_on(session_id, child, target, visited)
                                   for child in record["operand_ids"]))

    def put_evidence(self, session_id: str, record: dict) -> dict:
        return self._put(session_id, "evidence", record)

    def put_calculation(self, session_id: str, record: dict) -> dict:
        return self._put(session_id, "calculation", record)

    def put_claim(self, session_id: str, record: dict) -> dict:
        return self._put(session_id, "claim", record)

    def history(self, session_id: str, kind: str, record_id: str) -> list[dict]:
        if kind not in _KINDS:
            raise ValueError("kind must be evidence, calculation, claim, or document_source")
        _string(record_id, "record_id")
        with self._transaction(write=False):
            self._session(session_id)
            return [self._decode(session_id, kind, row) for row in self._db.execute(
                "SELECT payload FROM records WHERE session_id=? AND kind=? AND record_id=? ORDER BY version",
                (session_id, kind, record_id),
            ).fetchall()]

    def _observe_document_source(self, session_id: str, observation: dict) -> dict:
        if not observation["source_revision_authoritative"]:
            return {"applied": False, "changed": False, "invalidated_evidence_ids": [], "reason": "historical_snapshot"}
        identifier = observation["source_document_id"]
        previous = self._find(session_id, "document_source", identifier)
        if previous is not None:
            if previous["source_url"] != observation["source_url"]:
                raise ValueError("source_document_id cannot identify different source URLs")
            before = _source_time(previous["source_observed_at"])
            observed = _source_time(observation["source_observed_at"])
            if observed < before:
                return {"applied": False, "changed": False, "invalidated_evidence_ids": [], "reason": "older_observation", "source": previous}
            if observed == before:
                if previous["source_revision"] != observation["source_revision"]:
                    raise ValueError("conflicting document revisions share an observation timestamp")
                return {"applied": False, "changed": False, "invalidated_evidence_ids": [], "reason": "unchanged_observation", "source": previous}
        source = dict(observation, id=identifier, version=previous["version"] + 1 if previous else 1,
                      created_at=_now(), stale=False)
        self._db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (
            session_id, "document_source", identifier, source["version"],
            json.dumps(source, ensure_ascii=False), source["created_at"],
        ))
        changed = previous is None or previous["source_revision"] != source["source_revision"]
        invalidated = []
        if changed:
            for record in self._latest(session_id, "evidence"):
                if (record.get("kind") == "document" and record.get("source_revision_authoritative") is True
                        and record.get("source_document_id") == identifier
                        and record.get("source_url") == source["source_url"]
                        and record.get("source_revision") != source["source_revision"]):
                    self._invalidate(session_id, "evidence", record, f"document_source_changed:{identifier}")
                    invalidated.append(record["id"])
            self._cascade_stale(session_id)
        self._touch(session_id)
        return {"applied": True, "changed": changed, "invalidated_evidence_ids": invalidated, "source": source}

    def observe_document_source(self, session_id: str, observation: dict) -> dict:
        """Observe a trusted adapter's complete document snapshot, even with no hits.

        Only explicitly authoritative document-lineage records participate.
        Financial facts and ordinary/historical replay records are unaffected.
        Older cached observations cannot replace a newer source revision.
        """
        observation = _source_observation(observation)
        with self._transaction():
            self._session(session_id)
            return self._observe_document_source(session_id, observation)

    def _event(self, session_id: str, event: dict) -> dict:
        event = _json_copy(event)
        event.setdefault("id", uuid4().hex)
        event.setdefault("created_at", _now())
        seq = self._db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE session_id=?",
                               (session_id,)).fetchone()[0]
        event["event_sequence"] = seq
        event_type = event.get("type")
        if not isinstance(event_type, str):
            event_type = None
        self._db.execute("INSERT INTO events(session_id,sequence,payload,event_type) VALUES (?,?,?,?)",
                         (session_id, seq, json.dumps(event, ensure_ascii=False), event_type))
        return event

    def add_event(self, session_id: str, event_dict: dict) -> dict:
        if not isinstance(event_dict, dict):
            raise ValueError("event must be an object")
        with self._transaction():
            self._session(session_id)
            result = self._event(session_id, event_dict)
            self._touch(session_id)
            return result

    def events(self, session_id: str, *, limit: int | None = None,
               exclude_types: list[str] | tuple[str, ...] | set[str] | None = None) -> list[dict]:
        """Return chronological events, optionally the last N matching events.

        Filtering and limiting happen in SQLite before loading payloads. The
        default preserves the original complete-log API.
        """
        _limit(limit)
        if exclude_types is None:
            excluded = []
        elif isinstance(exclude_types, (list, tuple, set)):
            for event_type in exclude_types:
                _string(event_type, "excluded event type")
            excluded = sorted(set(exclude_types))
        else:
            raise ValueError("exclude_types must be a collection of event type strings")
        with self._transaction(write=False):
            self._session(session_id)
            where = "session_id=?"
            parameters: list[Any] = [session_id]
            if excluded:
                where += " AND (event_type IS NULL OR event_type NOT IN (" + ",".join("?" for _ in excluded) + "))"
                parameters.extend(excluded)
            query = "SELECT payload FROM events WHERE " + where + " ORDER BY sequence"
            if limit is not None:
                query += " DESC LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, parameters).fetchall()
            if limit is not None:
                rows.reverse()
            return [json.loads(row["payload"]) for row in rows]

    def latest_successful_tool_result(self, session_id: str, episode_id: str, tool: str) -> dict | None:
        """Reopen the latest successful observation of this tool in this turn only."""
        with self._transaction(write=False):
            row = self._db.execute(
                "SELECT sequence, payload FROM events WHERE session_id=? AND event_type='tool_call' "
                "AND json_extract(payload,'$.episode_id')=? AND json_extract(payload,'$.tool')=? "
                "AND json_extract(payload,'$.ok')=1 ORDER BY sequence DESC LIMIT 1",
                (session_id, episode_id, tool),
            ).fetchone()
            if row is None:
                return None
            event = json.loads(row["payload"])
            if event.get("ingestion_status") == "rejected":
                return None
            if "ingestion_status" not in event:
                # Older stores recorded failed ingestion as the next event in the same atomic step.
                error = self._db.execute(
                    "SELECT 1 FROM events WHERE session_id=? AND sequence=? AND event_type='tool_ingestion_error'",
                    (session_id, row["sequence"] + 1),
                ).fetchone()
                if error:
                    return None
            return event

    def latest_event(self, session_id: str, event_type: str) -> dict | None:
        """Read one indexed event, without loading a session's entire audit log.

        Returns the same unwrapped dictionary as ``events()``, or None when the
        session exists but has no event with that exact type.
        """
        _string(event_type, "event_type")
        with self._transaction(write=False):
            self._session(session_id)
            row = self._db.execute(
                "SELECT payload FROM events WHERE session_id=? AND event_type=? ORDER BY sequence DESC LIMIT 1",
                (session_id, event_type),
            ).fetchone()
            return json.loads(row["payload"]) if row is not None else None

    def edited_period_fields(self, session_id: str, *, after_version=0, after_sequence=0, after_episode=None) -> set[str]:
        """Find explicit changes to the issuer or dates, including older edit receipts."""
        fields = ("entities", "period_start", "period_end")
        with self._transaction(write=False):
            if after_episode:
                start = self._db.execute(
                    "SELECT MIN(sequence) FROM events WHERE session_id=? AND event_type='episode_checkpoint' "
                    "AND json_extract(payload,'$.episode.id')=?",
                    (session_id, after_episode),
                ).fetchone()[0]
                if start is None:
                    return set()
                after_sequence = max(after_sequence, start)
            edits = self._db.execute(
                "SELECT payload FROM events WHERE session_id=? AND event_type='scope_edited' AND sequence>? "
                "AND json_extract(payload,'$.scope_version')>?",
                (session_id, after_sequence, after_version),
            )
            result = set()
            for row in edits:
                edit = json.loads(row["payload"])
                previous = self._db.execute(
                    "SELECT payload FROM events WHERE session_id=? AND event_type='scope_changed' AND sequence<? "
                    "AND json_extract(payload,'$.scope_version')=? ORDER BY sequence DESC LIMIT 1",
                    (session_id, edit["event_sequence"], edit["previous_scope_version"]),
                ).fetchone()
                prior_scope = json.loads(previous["payload"])["scope"] if previous else {}
                result.update(field for field in fields if prior_scope.get(field) != edit["scope"].get(field))
            return result

    def save_report(self, session_id: str, report_dict: dict) -> dict:
        if not isinstance(report_dict, dict):
            raise ValueError("report must be an object")
        report = _json_copy(report_dict)
        with self._transaction():
            session = self._session(session_id)
            version = self._db.execute("SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE session_id=?",
                                       (session_id,)).fetchone()[0]
            report.setdefault("id", uuid4().hex)
            _string(report["id"], "report id")
            report["version"] = version
            report["created_at"] = _now()
            report.setdefault("scope", session["scope"])
            report.setdefault("scope_version", session["scope_version"])
            # Capture exact dependency versions even for minimal text reports.
            report.setdefault("record_versions", {
                kind: {r["id"]: r["version"] for r in self._latest(session_id, kind)}
                for kind in ("evidence", "calculation", "claim")
            })
            self._db.execute("INSERT INTO reports(session_id,version,payload,report_id,summary,summary_revision) VALUES (?,?,?,?,?,?)", (
                session_id, version, json.dumps(report, ensure_ascii=False), report["id"],
                json.dumps(_report_summary(report), ensure_ascii=False), _SUMMARY_REVISION,
            ))
            self._touch(session_id)
            return report

    def _reports(self, session_id: str, *, summaries: bool = False, limit: int | None = None) -> list[dict]:
        column = "summary" if summaries else "payload"
        query = f"SELECT {column} FROM reports WHERE session_id=? ORDER BY version"
        parameters: list[Any] = [session_id]
        if limit is not None:
            query += " DESC LIMIT ?"
            parameters.append(limit)
        rows = self._db.execute(query, parameters).fetchall()
        if limit is not None:
            rows.reverse()
        result = [json.loads(row[column]) for row in rows]
        if summaries:
            for item in result:
                # Older compact rows cached the draft without its classification.
                # Read them without loading or rewriting the archived report.
                if "summary" in item and "summary_kind" not in item:
                    item.pop("summary")
                    item["summary_kind"] = "see_report_findings"
        return result

    def list_reports(self, session_id: str) -> list[dict]:
        with self._transaction(write=False):
            self._session(session_id)
            return self._reports(session_id)

    def list_report_summaries(self, session_id: str, *, limit: int | None = None) -> list[dict]:
        """Return chronological metadata for all reports, or the last N reports.

        The SQL query reads only a separately persisted summary column, never
        the complete report payload or its evidence/calculation snapshots.
        """
        _limit(limit)
        with self._transaction(write=False):
            self._session(session_id)
            return self._reports(session_id, summaries=True, limit=limit)

    def list_episode_summaries(self, session_id: str) -> list[dict]:
        """Latest checkpoint per episode, without loading every intermediate payload."""
        fields = ("id", "request", "status", "stop_reason", "created_at", "finished_at",
                  "model_calls", "tool_calls", "planner_steps", "active_seconds", "usage", "budget")
        with self._transaction(write=False):
            self._session(session_id)
            try:
                rows = self._db.execute(
                    "SELECT payload FROM events WHERE session_id=? AND sequence IN ("
                    "SELECT MAX(sequence) FROM events WHERE session_id=? AND event_type='episode_checkpoint' "
                    "AND json_type(payload,'$.episode.id')='text' "
                    "GROUP BY json_extract(payload,'$.episode.id')) ORDER BY sequence",
                    (session_id, session_id),
                ).fetchall()
                episodes = [json.loads(row["payload"])["episode"] for row in rows]
            except sqlite3.OperationalError as exc:
                if "no such function: json_" not in str(exc):
                    raise
                # Older system SQLite builds can lack JSON functions.
                latest = {}
                for row in self._db.execute("SELECT payload FROM events WHERE session_id=? AND event_type='episode_checkpoint' ORDER BY sequence", (session_id,)):
                    episode = json.loads(row["payload"]).get("episode")
                    if isinstance(episode, dict) and isinstance(episode.get("id"), str):
                        latest[episode["id"]] = episode
                episodes = list(latest.values())
            return [{key: episode[key] for key in fields if key in episode} for episode in episodes]

    def _material_receipt_ids(self, sid, sequence, event, records):
        if not event.get("ok") or event.get("ingestion_status") == "rejected":
            return []
        versions = event.get("record_versions")
        if isinstance(versions, dict):
            return [ref for ref, version in versions.items() if type(version) is int and ref in records
                    and version == records[ref][1]["version"]]
        # Early receipts have no version map. A matching first-version snapshot
        # is unambiguous, provided ingestion did not reject the batch.
        rejected = self._db.execute("SELECT 1 FROM events WHERE session_id=? AND sequence=? "
                                    "AND event_type='tool_ingestion_error'", (sid, sequence + 1)).fetchone()
        if rejected:
            return []
        matching = []
        for group in ("evidence", "calculations"):
            for item in (event.get("observation") or {}).get(group, []):
                saved = records.get(item.get("id")) if isinstance(item, dict) else None
                if (saved and saved[0] == group and saved[1]["version"] == 1 and len(item) > 1
                        and all(saved[1].get(k) == v for k, v in item.items())):
                    matching.append(item["id"])
        return matching

    def report_with_stage(self, session_id: str, report: dict) -> dict:
        """Enrich one delivery from its own pre-publication receipts, without changing the archive."""
        view = dict(report)
        check_old_critic_failure = (view.get("delivery_stage") == "draft_unverified"
                                    and view.get("stop_reason") == "execution_error"
                                    and "critic_output_error" not in view)
        if "delivery_stage" in view and "episode_material_ids" in view and not check_old_critic_failure:
            return view
        if report.get("verification_available"):
            view["delivery_stage"] = "reviewed"
        elif report.get("model_mode") == "offline_tools_only":
            view["delivery_stage"] = "offline_tools_only"
        if not all(isinstance(report.get(k), str) and report[k] for k in ("episode_id", "request", "created_at")):
            return view

        claims = {c["id"]: c for c in report.get("claims", [])}
        claim_versions = {key: c.get("version") for key, c in claims.items()}
        records = {}
        for group, kind in (("evidence", "evidence"), ("calculations", "calculation")):
            versions = report.get("record_versions", {}).get(kind)
            for record in report.get(group, []):
                version = record.get("version")
                if (not record.get("stale") and type(version) is int
                        and (versions is None or versions.get(record["id"]) == version)):
                    records[record["id"]] = (group, record)
        obtained = set()
        checkpoint = None
        critic_started_at = None
        error = None
        accepted_draft = False
        # Clarification can reuse an episode ID with an amended request. Checkpoints
        # delimit those requests. Project just the draft fields out of large checkpoints.
        query = """
            SELECT sequence, CASE WHEN event_type='episode_checkpoint' THEN json_object(
                'type',event_type,'request',json_extract(payload,'$.episode.request'),
                'summary',json_extract(payload,'$.episode.summary'),
                'scope_version',json_extract(payload,'$.episode.draft_scope_version'),
                'claim_ids',json_extract(payload,'$.episode.active_claim_ids'))
                ELSE payload END AS receipt
            FROM events WHERE session_id=? AND (
                (event_type='episode_checkpoint' AND json_extract(payload,'$.episode.id')=?) OR
                (event_type IN ('step_started','model_call','tool_call','role_result_superseded')
                    AND json_extract(payload,'$.episode_id')=?))
                AND json_extract(payload,'$.created_at') <= ?
            ORDER BY sequence"""
        with self._transaction(write=False):
            for row in self._db.execute(query, (session_id, report["episode_id"], report["episode_id"], report["created_at"])):
                event = json.loads(row["receipt"])
                kind = event["type"]
                if kind == "episode_checkpoint":
                    if event["request"] != report["request"]:
                        checkpoint, error, critic_started_at, accepted_draft = None, None, None, False
                        obtained.clear()
                    else:
                        checkpoint = event
                    continue
                if checkpoint is None:
                    continue
                if kind == "tool_call":
                    obtained.update(self._material_receipt_ids(session_id, row["sequence"], event, records))
                    continue
                if kind == "model_call" and event.get("role") == "researcher" and event.get("ok"):
                    error, critic_started_at = None, None
                    accepted_draft = not event.get("local_commit_error")
                if kind == "role_result_superseded" and event.get("role") == "researcher":
                    accepted_draft = False
                if event.get("role") != "critic":
                    continue
                if kind == "step_started":
                    critic_started_at = event.get("created_at")
                if kind != "model_call":
                    continue
                error = None
                code = event.get("contract_error")
                truncated = event.get("provider_output_error") == "max_output_tokens"
                if (event.get("ok") is not False or (not truncated and not (isinstance(code, str) and code))
                        or any(c.get("stale") for c in claims.values())):
                    continue
                if "draft_claim_versions" in event:
                    matches = (event["draft_claim_versions"] == claim_versions
                               and event.get("draft_scope_version") == report.get("scope_version"))
                else:
                    # Before explicit claim-version bindings, use the checkpoint from
                    # the call and ensure no delivered claim was written after it began.
                    matches = (accepted_draft and critic_started_at
                               and checkpoint["scope_version"] == report.get("scope_version")
                               and checkpoint["summary"] == report.get("summary")
                               and set(checkpoint["claim_ids"] or []) == set(claims)
                               and all(c.get("created_at") and c["created_at"] <= critic_started_at for c in claims.values()))
                if matches:
                    if truncated:
                        error = {"delivery_stage": "draft_unverified", "critic_output_error": "max_output_tokens"}
                        limit = (event.get("response_metadata") or {}).get("max_output_tokens")
                        if type(limit) is int and limit > 0:
                            error["critic_max_output_tokens"] = limit
                    else:
                        error = {"delivery_stage": "critic_receipt_invalid",
                                 "critic_receipt_error": code if code in _CRITIC_RECEIPT_ERRORS else "invalid_role_output"}
        if view.get("delivery_stage") not in {"offline_tools_only", "reviewed"}:
            if error:
                view.update(error)
            elif claims or (accepted_draft and checkpoint and checkpoint["summary"]
                            and checkpoint["summary"] == report.get("summary")):
                view["delivery_stage"] = "draft_unverified"
            elif checkpoint and checkpoint["claim_ids"] == [] and checkpoint["summary"] == "":
                view.setdefault("delivery_stage", "no_draft")
        if checkpoint is not None:
            view["episode_material_ids"] = {group: [r["id"] for r in report.get(group, []) if r["id"] in obtained]
                                            for group in ("evidence", "calculations")}
        return view

    def get_report(self, session_id: str, report_id: str | int) -> dict:
        """Load a saved report by ID, or by its positive session-wide version.

        A string is an exact ID (including numeric strings); an integer is a
        version. If a caller deliberately reused an ID, that ID resolves to its
        latest saved version. Missing sessions/reports raise KeyError.
        """
        if isinstance(report_id, bool) or not isinstance(report_id, (str, int)):
            raise ValueError("report_id must be a non-empty string or positive version integer")
        if isinstance(report_id, int):
            if report_id < 1:
                raise ValueError("report version must be positive")
            field = "version"
        else:
            _string(report_id, "report_id")
            field = "report_id"
        with self._transaction(write=False):
            self._session(session_id)
            row = self._db.execute(
                f"SELECT payload FROM reports WHERE session_id=? AND {field}=? ORDER BY version DESC LIMIT 1",
                (session_id, report_id),
            ).fetchone()
            if row is None:
                raise KeyError(report_id)
            return json.loads(row["payload"])

    def get_context(self, session_id: str, *, report_mode: str = "full", report_limit: int | None = None) -> dict:
        """Read a consistent session view with bounded optional report history.

        Defaults preserve the complete-history API. ``summaries`` reads only
        small metadata rows; ``none`` loads no reports. ``report_count`` always
        describes the full history, independently of mode or limit.
        """
        if report_mode not in ("full", "summaries", "none"):
            raise ValueError("report_mode must be full, summaries, or none")
        _limit(report_limit, "report_limit")
        with self._transaction(write=False):
            session = self._session(session_id)
            return {
                "session": session,
                "messages": [json.loads(row["payload"]) for row in self._db.execute(
                    "SELECT payload FROM messages WHERE session_id=? ORDER BY sequence", (session_id,)
                ).fetchall()],
                "evidence": self._latest(session_id, "evidence"),
                "calculations": self._latest(session_id, "calculation"),
                "claims": self._latest(session_id, "claim"),
                "reports": [] if report_mode == "none" else self._reports(
                    session_id, summaries=report_mode == "summaries", limit=report_limit),
                "report_count": self._db.execute("SELECT COUNT(*) FROM reports WHERE session_id=?", (session_id,)).fetchone()[0],
            }

    def _cancel(self, session_id: str, cancelled: bool) -> None:
        with self._transaction():
            self._session(session_id)
            self._db.execute("UPDATE sessions SET cancelled=?,updated_at=? WHERE id=?",
                             (int(cancelled), _now(), session_id))

    def request_cancel(self, session_id: str) -> None:
        self._cancel(session_id, True)

    def clear_cancel(self, session_id: str) -> None:
        self._cancel(session_id, False)

    def is_cancelled(self, session_id: str) -> bool:
        return self.get_session(session_id)["cancelled"]


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,title TEXT NOT NULL,scope TEXT NOT NULL,scope_version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,cancelled INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS session_context_limits (session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,max_context_chars INTEGER NOT NULL CHECK(max_context_chars>0))",
    "CREATE TABLE IF NOT EXISTS messages (session_id TEXT NOT NULL REFERENCES sessions(id),sequence INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session_id,sequence))",
    "CREATE TABLE IF NOT EXISTS records (session_id TEXT NOT NULL REFERENCES sessions(id),kind TEXT NOT NULL,record_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(session_id,kind,record_id,version))",
    "CREATE TABLE IF NOT EXISTS invalidations (session_id TEXT NOT NULL,kind TEXT NOT NULL,record_id TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(session_id,kind,record_id,version,reason),FOREIGN KEY(session_id,kind,record_id,version) REFERENCES records(session_id,kind,record_id,version))",
    "CREATE TABLE IF NOT EXISTS events (session_id TEXT NOT NULL REFERENCES sessions(id),sequence INTEGER NOT NULL,payload TEXT NOT NULL,event_type TEXT,PRIMARY KEY(session_id,sequence))",
    "CREATE TABLE IF NOT EXISTS reports (session_id TEXT NOT NULL REFERENCES sessions(id),version INTEGER NOT NULL,payload TEXT NOT NULL,report_id TEXT NOT NULL,summary TEXT NOT NULL,PRIMARY KEY(session_id,version))",
)
