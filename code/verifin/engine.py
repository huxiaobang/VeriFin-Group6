"""Budgeted research episodes over persistent sessions.

The state machine schedules real model and tool adapters. Tests inject a scripted
model explicitly; production never substitutes scripted answers for a model.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from http.client import IncompleteRead
import json
import math
from pathlib import Path
import threading
import time
import uuid

from . import fiscal_followup, planner_evidence, income_followup, issuer_attribution, source_scope, latest_quarter, source_link_followup
from .answer_periods import CHECK as ANSWER_PERIOD_CHECK, remember_latest_quarter
from .verification import Verifier, check_rules
from .model import ContextLimitReached, ModelError, OutputContractError, SpendLimitReached, USAGE_FIELDS
from .request_review import clarify_request, request_parts, coverage_issues, report_coverage, single_question_mismatch, unresolved_checklist
from .reporting_period import explicit_quarter_end
from .submissions import check_model_binding, lookup_submission, model_binding, research_cutoff, reserve_submission, saved_response
from .request_continuation import RequestContinuation, restore_acquisition


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def request_wording(text):
    return " ".join(text.split()).casefold().rstrip("。！？.!?")


def retry_instruction(text):
    return request_wording(text) in {
        "再回答一次", "请再回答一次", "再答一次", "重新回答", "请重新回答", "重答上一问",
        "重新回答上一个问题", "重复上一个问题", "再试一次", "重试", "请重试", "重試", "請重試",
        "再试试", "请再试试", "再試試", "請再試試",
        "repeat the last question", "answer the same question again", "answer again", "try again", "retry"}


def progress_content(value):
    """Retrieval bookkeeping alone is not new research progress."""
    if isinstance(value, dict):
        return {key: progress_content(child) for key, child in value.items()
                if key not in {"retrieved_at", "source_observed_at", "network_requests", "retrieval_score",
                               "ranking_latency_ms", "runtime_encoding"}}
    if isinstance(value, list):
        return [progress_content(child) for child in value]
    return value


def review_sources(context):
    return digest([(r["id"], r.get("version"), r.get("stale", False))
                   for key in ("evidence", "calculations") for r in context[key]])


def record_review(episode, context, instruction):
    """Track recurring objections and progress on the user's questions across rewrites."""
    sources = review_sources(context)
    previous = episode.get("revision_memory", {})
    same_inputs = (sources == previous.get("sources") and
                   context["session"]["scope_version"] == previous.get("scope_version"))
    counts = {(r["target_id"], r["issue_type"]): r["reviews"]
              for r in previous.get("open", [])} if same_inputs else {}
    targets = sorted({(i["target_id"], i["issue_type"]) for i in episode["issues"]})
    # A claim ID, objection label or revised sentence can change on every
    # draft. A user question remains the same. Only count a newly reached
    # coverage level on an unchallenged answer to that question as progress.
    # A requirement-level objection can coexist with a supported amount.
    # This accounting limits retries; it never certifies a claim or question.
    best = dict(previous.get("best_part_coverage", {})) if same_inputs else {}
    objected = {target for target, _ in targets}
    improved = False
    parts = context.get("request_parts", {})
    reviewed = set(episode.get("reviewed_claim_ids", []))
    for row in episode.get("request_coverage", []):
        if not isinstance(row, dict):
            continue
        part, claim = row.get("part"), row.get("claim_id")
        status = row.get("status")
        if status not in ("partial", "answered"):
            continue
        level = 2 if status == "answered" else 1
        if (isinstance(part, str) and part in parts and isinstance(claim, str) and claim in reviewed and claim not in objected
                and (part not in objected or level == 1) and level > best.get(part, 0)):
            best[part] = level
            improved = True
    # Include the review that established this coverage level. The default
    # allowance of three reviews permits two rewrites after that review.
    review_span = previous.get("review_span", 0) + 1 if same_inputs and not improved else 1
    episode["revision_memory"] = {
        "sources": sources, "scope_version": context["session"]["scope_version"],
        "best_part_coverage": best, "review_span": review_span,
        "open": [{"target_id": target, "issue_type": category,
                  "reviews": counts.get((target, category), 0) + 1} for target, category in targets],
        "last_draft_instruction": instruction, "tool_calls_at_review": episode["tool_calls"]}


def repeated_redraft(episode, limit):
    """Let the planner fetch new material; decline an unproductive draft on old inputs."""
    memory = episode.get("revision_memory", {})
    open_issues = memory.get("open", [])
    if not episode.get("reviewed") or not open_issues:
        return False
    if memory.get("tool_calls_at_review") != episode["tool_calls"] and not episode["no_progress"]:
        return False
    return (all(item["reviews"] >= 2 for item in open_issues)
            or memory.get("review_span", 0) >= limit)


@dataclass(frozen=True)
class Budget:
    max_model_calls: int = 16
    max_tool_calls: int = 16
    max_seconds: float = 240
    max_no_progress: int = 3
    max_planner_steps: int = 64

    def __post_init__(self):
        for key in ("max_model_calls", "max_tool_calls", "max_no_progress", "max_planner_steps"):
            value = getattr(self, key)
            if type(value) is not int or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float)) or not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("max_seconds must be finite and positive")


class StopEpisode(Exception):
    pass


class SessionBusy(ValueError):
    pass


_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def session_lock(store, session_id):
    """Fail fast for a second writer; cancellation uses a separate connection.

The OS releases the lock on crashes. No long SQLite transaction blocks readers.
"""
    key = (str(getattr(store, "path", id(store))), session_id)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise SessionBusy("This session is already running; cancel or wait before continuing.")
    handle = None
    try:
        db_path = str(getattr(store, "path", ":memory:"))
        if db_path != ":memory:":
            import fcntl
            directory = Path(db_path).absolute().parent / ".run-locks"
            directory.mkdir(parents=True, exist_ok=True)
            handle = (directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".lock")).open("a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SessionBusy("This session is already running in another process.") from None
        yield
    finally:
        if handle:
            handle.close()
        lock.release()


class ResearchEngine:
    def __init__(self, store, model, tools, *, clock=time.monotonic, candidate_callback=None, planner=None,
                 finish_after_review=False, reference_duplicate_records=False, auto_skills=True,
                 stop_on_tool_fault=False):
        self.store, self.model, self.tools = store, model, tools
        self.clock = clock
        self.verifier = Verifier(model)
        self.candidate_callback = candidate_callback
        self.planner = planner
        self.finish_after_review = finish_after_review
        self.reference_duplicate_records = reference_duplicate_records
        self.auto_skills = auto_skills
        self.stop_on_tool_fault = stop_on_tool_fault

    def _checkpoint(self, sid, episode):
        event = {"type": "episode_checkpoint", "episode": episode.copy()}
        if episode.get("request_id"):
            event["request_id"] = episode["request_id"]
        return self.store.add_event(sid, event)

    def _finish_request(self, sid, episode, checkpoint, report_version=None):
        if request_id := episode.get("request_id"):
            self.store.finish_submission(sid, request_id, checkpoint["event_sequence"], report_version)
            self.store.clear_research_handoff(sid, request_id)

    def _save_delivery(self, sid, episode, existing=None):
        with self.store.atomic_step():
            saved = existing if existing is not None else self.store.save_report(sid, self._report(sid, episode))
            # Earlier versions committed the final report, message and checkpoint
            # separately. Reopening a legacy report may need just its reply.
            messages = self.store.get_context(sid, report_mode="none")["messages"] if existing is not None else []
            if not messages or messages[-1]["role"] != "assistant":
                message = f"Research {episode['status']} ({episode['stop_reason']}). "
                if not saved.get("verification_available"):
                    message += "Verification incomplete. "
                self.store.append_message(sid, "assistant", message + "See the report's findings, sources and unresolved items.")
            checkpoint = self._checkpoint(sid, episode)
            self._finish_request(sid, episode, checkpoint, saved["version"])
        return saved

    def _restore_review(self, sid, episode):
        """Bind delivery to the last committed review of the same question, scope and inputs."""
        episode.update(reviewed=False, request_coverage=[])
        episode.pop("restored_claim_versions", None)
        episode.pop("followup_limits", None)
        if episode.get("draft_reference_issues"):
            # A newer, saved draft isolated these claims. Earlier reviewed
            # versions, including records with the same IDs, cannot replace it.
            return
        event = self.store.latest_event(sid, "verification")
        if not event or event.get("episode_id") != episode["id"] or not event.get("review_snapshot"):
            return
        snapshot = event["review_snapshot"]
        ctx = self._context(sid, episode)
        if snapshot["scope_version"] != ctx["session"]["scope_version"] or snapshot["request"] != episode["request"]:
            if episode.get("draft_scope_version", episode.get("reviewed_scope_version")) != ctx["session"]["scope_version"]:
                episode.update(active_claim_ids=[], issues=[], summary="", draft_saved=False, answered_requirement_ids=[], unanswered_requirement_ids=[])
            return
        if snapshot["sources"] != review_sources(ctx):
            return
        episode.update(snapshot["fields"], reviewed=True, restored_claim_versions=snapshot["claim_versions"])
        self.store.add_event(sid, {"type": "reviewed_draft_restored", "episode_id": episode["id"],
                                   "claim_ids": episode["active_claim_ids"], "claim_versions": snapshot["claim_versions"]})

    def latest_episode(self, sid):
        if hasattr(self.store, "latest_event"):
            event = self.store.latest_event(sid, "episode_checkpoint")
            return event["episode"] if event else None
        for event in reversed(self.store.events(sid)):
            payload = event.get("event", event)
            if payload.get("type") == "episode_checkpoint":
                return payload["episode"]
        return None

    def _seal_interrupted(self, sid, episode, *, reason="interrupted"):
        count = self.store.get_context(sid, report_mode="none")["report_count"]
        saved = self.store.get_report(sid, count) if count else None
        if saved and saved.get("episode_id") == episode["id"] and saved.get("request_id") == episode.get("request_id"):
            # Older writers could commit the report before the final checkpoint.
            episode.update(status=saved["status"], stop_reason=saved["stop_reason"], finished_at=saved["created_at"])
            return self._save_delivery(sid, episode, existing=saved)
        episode.update(status="partial", stop_reason=reason, finished_at=utcnow())
        with self.store.atomic_step():
            # A committed review may include both a rejected draft item and
            # verified siblings. Keep an exact current review intact.
            if not (episode["reviewed"] and self._current_review(sid, episode, self._context(sid, episode))):
                self._restore_review(sid, episode)
            return self._save_delivery(sid, episode)

    def recover_interrupted(self, sid):
        """Seal the current checkpoint without scheduling another research turn."""
        with session_lock(self.store, sid):
            self.store.get_session(sid)
            episode = self.latest_episode(sid)
            if episode and episode["status"] == "running":
                return self._seal_interrupted(sid, episode)
            return None

    def _continuation_settings(self):
        data = getattr(self.tools, "data", None)
        return {
            "fixed_planner": self.planner is not None,
            "live_sources": getattr(data, "allow_network", None),
            "snapshot_manifest": getattr(data, "manifest_hash", None),
            "finish_after_review": self.finish_after_review,
            "reference_duplicate_records": self.reference_duplicate_records,
            "auto_skills": self.auto_skills,
            "stop_on_tool_fault": self.stop_on_tool_fault,
        }

    def ask(self, session_id, message=None, *, budget=None, reset_cancel=True, as_of=None, max_context_chars=None,
            request_id=None):
        """Admit a new request or resume the original submission by its durable ID."""
        submission = lookup_submission(self.store, session_id, request_id, message, as_of)
        if response := saved_response(self.store, session_id, submission):
            return response
        with session_lock(self.store, session_id):
            submission = lookup_submission(self.store, session_id, request_id, message, as_of)
            if response := saved_response(self.store, session_id, submission):
                return response
            self.store.get_session(session_id)
            if submission:
                message, as_of = submission["message"], submission["as_of"]
                budget = Budget(**submission["budget"])
                max_context_chars = submission["max_context_chars"]
                request_id = submission["request_id"]
                check_model_binding(submission, self.model)
            elif message is None:
                raise ValueError("No saved research request has this ID.")
            stated, day_only_prefix = research_cutoff(message, as_of)
            previous = self.latest_episode(session_id)
            if submission and submission["episode_id"]:
                if not previous or previous.get("request_id") != request_id or previous["status"] != "running":
                    raise ValueError("The request has no matching running checkpoint.")
                journal = RequestContinuation(self.store, session_id, request_id)
                frame, interruption = journal.restore(previous, self._continuation_settings())
                if interruption:
                    report = self._seal_interrupted(session_id, previous, reason=interruption)
                    return {"status": previous["status"], "report": report, "episode": previous}
                return self._run_turn(session_id, frame["episode"], frame["bootstrap"], journal, frame["state"], frame["stage"])
            if not submission:
                request_id = request_id or "request_" + uuid.uuid4().hex
                reserve_submission(self.store, session_id, request_id, message=message, as_of=as_of,
                                   budget=asdict(budget or Budget()), max_context_chars=max_context_chars,
                                   execution_backend="python-staged", offline=self.model is None,
                                   reset_cancel=reset_cancel, model_identity=model_binding(self.model))
            journal = RequestContinuation(self.store, session_id, request_id)
            with self.store.atomic_step():
                episode, bootstrap, state = self._prepare_turn(
                    session_id, message, budget, as_of, max_context_chars, stated, day_only_prefix, request_id)
                admitted = self._checkpoint(session_id, episode)
                journal.save(episode, admitted, bootstrap, "plan", state)
            return self._run_turn(session_id, episode, bootstrap, journal, state, "plan")

    def _prepare_turn(self, session_id, message, budget, as_of, max_context_chars, stated, day_only_prefix, request_id):
        from .data import cutoff_time
        turn_cutoff = as_of or stated
        session = self.store.get_session(session_id)
        if max_context_chars is not None:
            session = self.store.set_context_limit(session_id, max_context_chars)
        previous = self.latest_episode(session_id)
        period_fields = ("period_start", "period_end")
        period_edits = set()
        interrupted = previous and previous["status"] == "running"
        if interrupted:
            checkpoint = self.store.latest_event(session_id, "episode_checkpoint")
            period_edits = self.store.edited_period_fields(session_id, after_sequence=checkpoint["event_sequence"])
            self._seal_interrupted(session_id, previous)
        session_scope_at_start = session["scope"]
        old_cutoff = session["scope"].get("cutoff")
        if day_only_prefix and as_of is None and old_cutoff and old_cutoff[:10] == stated and len(old_cutoff) > 10:
            turn_cutoff = old_cutoff
        new_followup = False
        use_current_request = False
        retry_report = None
        prior_report = None
        same_checklist = False
        archived_source = None
        source_lookup = False
        local_source_lookup = self.planner is None and self.auto_skills and not getattr(self.model, "method_pack", None)
        link_only = local_source_lookup and source_link_followup.requested(message)
        relative_only = fiscal_followup.prior_period_only(message)
        if previous and previous["status"] in {"complete","partial"}:
            version = self.store.get_context(session_id,report_mode="none")["report_count"]
            prior_report = self.store.get_report(session_id, version)
            if not interrupted:
                period_edits = self.store.edited_period_fields(session_id, after_version=prior_report["scope_version"])
                if prior_report.get("stop_reason") == "interrupted":
                    # Separate recovery may have saved the old quarter under the scope already edited by the user.
                    period_edits.update(self.store.edited_period_fields(
                        session_id, after_episode=prior_report["episode_id"]))
            same = request_wording(message) == request_wording(prior_report["request"])
            if retry_instruction(message) or (same and not link_only and (not relative_only or period_edits)):
                # Older releases saved the shortcut as the entire report request.
                # Follow consecutive shortcuts back to the substantive user turn.
                retry_report = prior_report
                while version > 1 and retry_instruction(retry_report["request"]):
                    version -= 1
                    retry_report = self.store.get_report(session_id, version)
            new_followup = retry_report is None
            # Explicitly edited requirements remain actionable. Editing only a date
            # or a display field does not turn an old checklist into a new task.
            scope = session["scope"]
            prior_scope = prior_report["scope"]
            assigned_task = scope.get("planning_task") and scope["planning_task"] != prior_scope.get("planning_task")
            use_current_request = (self.planner is None and new_followup and not assigned_task
                and scope.get("requirements", []) == prior_scope.get("requirements", []))
            source_lookup = local_source_lookup and (
                (link_only and use_current_request) or bool(retry_report and retry_report.get("archived_source")))
            if (source_lookup and prior_report["scope_version"] == session["scope_version"]
                    and (not turn_cutoff or cutoff_time(turn_cutoff) == cutoff_time(old_cutoff))):
                source_report = retry_report or prior_report
                if pointer := source_report.get("archived_source"):
                    source_report = self.store.get_report(session_id, pointer["report_version"])
                archived_source = source_link_followup.saved_source(source_report,
                    self.store.get_context(session_id, report_mode="none"), scope)
        if turn_cutoff and cutoff_time(turn_cutoff) != cutoff_time(old_cutoff):
            session = self.store.update_scope(session_id, {**session["scope"], "cutoff":turn_cutoff})
        if previous and previous["status"] == "waiting_for_clarification":
            episode = dict(previous)
            episode.pop("revision_memory", None)
            episode.pop("income_reason_attempted", None)
            episode.pop("tool_recovery", None)
            previous_parts = previous.get("clarified_parts", previous.get("inherited_parts"))
            amended, parts, revised_checklist = clarify_request(previous["request"], message, previous_parts)
            episode["request"] = amended
            episode["clarified_parts"] = parts
            # Preserve an explicitly edited checklist. Otherwise update the default
            # task and allow the planner to decompose the amended request afresh.
            requirements = session["scope"].get("requirements", [])
            default_id = "request:" + episode["id"]
            same_checklist = digest(requirements) == episode.get("clarification_checklist_hash")
            if (self.model is not None and self.planner is None and
                    (not requirements or requirements == [{"id":default_id, "text":previous["request"]}]
                     or (same_checklist and revised_checklist))):
                session = self.store.update_scope(session_id, {
                    **session["scope"], "requirements":[{"id":default_id, "text":amended}]})
            episode.pop("resolve_followup_checklist", None)
            # Keep source observations and the consumed budget. The old draft was
            # reviewed against the previous request, even if its fiscal period is unchanged.
            episode.update(active_claim_ids=[], answered_requirement_ids=[], unanswered_requirement_ids=[],
                           reviewed_claim_ids=[], reviewed_requirement_ids=[], request_coverage=[], issues=[],
                           reviewed=False, draft_attempted=False, draft_saved=False, summary="", no_progress=0, seen_results=[])
            episode.pop("reviewed_scope_version", None)
            episode.pop("restored_claim_versions", None)
            episode.pop("draft_reference_issues", None)
            # A user can also change scope outside ask() while the agent is
            # waiting. Compare to the last context actually shown to it.
            prior_view = previous.get("clarification_cutoff")
            current_view = session["scope"].get("cutoff")
            if current_view and (not prior_view or cutoff_time(current_view) < cutoff_time(prior_view)):
                episode.pop("last_tool_result", None)
                episode.pop("recent_tool_results", None)
            if not turn_cutoff and prior_view != current_view:
                episode.pop("user_cutoff", None)
            # Clarification resumes the original allowance, never a fresh one.
            limits = Budget(**episode["budget"])
            episode.update(status="running", question=None)
        else:
            limits = budget or Budget()
            episode = {
                "id": "episode_" + uuid.uuid4().hex,
                "request": message, "status": "running", "created_at": utcnow(),
                "budget": asdict(limits), "model_calls": 0, "tool_calls": 0, "planner_steps": 0,
                "active_seconds": 0.0, "usage": {}, "no_progress": 0,
                "active_claim_ids": [], "issues": [], "reviewed": False, "draft_saved": False,
                "answered_requirement_ids": [], "unanswered_requirement_ids": [],
                "summary": "", "seen_results": [],
            }
            if new_followup:
                episode["new_followup"] = True
            if retry_report is not None:
                episode["request"] = retry_report["request"]
                episode["retry_of_version"] = retry_report["version"]
                if retry_report.get("request_parts"):
                    episode["inherited_parts"] = retry_report["request_parts"]
                if (retry_report["version"] != prior_report["version"]
                        and session["scope"].get("requirements") == prior_report["scope"].get("requirements")):
                    session = self.store.update_scope(session_id, {**session["scope"],
                        "requirements": retry_report["scope"].get("requirements", [])})
            if use_current_request and not relative_only:
                session = self.store.update_scope(session_id, {
                    **session["scope"], "requirements": [{"id":"request:"+episode["id"], "text":message}]})
        episode["model_mode"] = getattr(self.model, "mode", "configured_model") if self.model is not None else "offline_tools_only"
        episode["request_id"] = request_id
        self.store.admit_submission(session_id, request_id, episode["id"])
        self.store.append_message(session_id, "user", message)
        task_message = episode["request"]
        for field in ("skill_calculations", "skill_gap", "research_skills", "skill_selection_source", "draft_skill_selection", "statement_notes", "skill_assessments", "skill_fiscal_quarter", "requested_fiscal_year", "offline_part_findings", "margin_formula_target"):
            episode.pop(field, None)
        if turn_cutoff:
            episode["user_cutoff"] = turn_cutoff
        from . import skill_routing, skill_calculations, operating_margin
        auto = self.auto_skills and self.planner is None and not getattr(self.model, "method_pack", None)
        scope = session["scope"]
        catalog = getattr(getattr(self.tools, "data", None), "manifest", {}).get("companies", [])
        companies = catalog or [{"name":ticker, "ticker":ticker} for ticker in scope.get("entities", [])]
        resumed = previous and previous["status"] == "waiting_for_clarification"
        scope_message = message if resumed else task_message
        amendment = skill_routing.amended_issuers(scope_message, scope, companies)
        declared = skill_routing.named_issuers(skill_routing.focus_of_switch(scope_message), companies)
        # A user may return to a different issuer instead of answering the
        # outstanding clarification. The Planner decides whether this is a
        # replacement. Its issuer must be explicitly named in the latest turn.
        replacement_offer = None
        if (resumed and same_checklist
                and (not requirements or requirements == [{"id":default_id, "text":previous["request"]}])
                and self.model is not None and self.planner is None
                and len(declared) == 1 and declared[0] not in scope.get("entities", [])
                and not amendment and not skill_routing.preserves_prior_participants(message)):
            replacement_offer = {"latest_message": message, "named_issuer": declared[0]}
        prior_boundary = (previous or {}).get("user_issuer_boundary")
        retain_boundary = bool(prior_boundary and
            set(prior_boundary["entities"]).issubset(scope.get("entities", [])) and
            ((resumed and not declared and not amendment and skill_routing.focus_of_switch(message) == message)
             or episode.get("retry_of_version")))
        if retain_boundary:
            scope_message, declared, amendment = message, [], None
        if amendment:
            declared = amendment[0]
        boundary = None
        period_followup = new_followup and relative_only
        original_parts = (retry_report or {}).get("quarter_followup_parts")
        replaying_quarter = bool(retry_report and original_parts)
        edited_retry = bool(retry_report and period_edits)
        edited_issuer = edited_retry and "entities" in period_edits
        quarter_dates = {}
        time_message = message if resumed else task_message
        annual = skill_calculations.requested_fiscal_year(time_message)
        anchored_predecessor = skill_calculations.latest_reported_target(time_message) == "previous"
        prior_target = anchored_predecessor or skill_calculations.targets_prior_quarter(time_message)
        quarter_labels = skill_calculations.target_labels(time_message)
        return_reference = (fiscal_followup.quarter_reference(time_message)
                            if (new_followup or retry_report) and not (prior_target or annual or quarter_labels) else None)
        historical = None
        return_bounds = {}
        stated_end = None
        inherited_quarter = False
        issuer, target_quarter, switch = None, None, False
        if auto or declared or amendment or retain_boundary or period_followup or return_reference or edited_retry:
            stored = self.store.get_context(session_id, report_mode="none")
            history = stored["messages"]
            earlier = [m["content"] for m in history[:-1] if m["role"] == "user"]
            if edited_issuer:
                entities = scope.get("entities", [])
                issuer = entities[0] if len(entities) == 1 else None
                if entities:
                    boundary = {"entities": entities, "open_peers": False}
            else:
                entities, issuer = skill_routing.resolve_issuer_scope(scope_message, earlier, scope, companies, amendment=amendment)
                if retain_boundary and entities == scope.get("entities", []):
                    boundary = prior_boundary
                else:
                    boundary = skill_routing.user_issuer_boundary(scope_message, declared, entities, scope.get("entities", []))
                if amendment and amendment[1]:
                    boundary = {**(boundary or {"entities":[],"open_peers":True}), "excluded":amendment[1]}
            issuer_history = skill_routing.same_issuer_history(earlier, issuer, companies) if issuer else []
            prior_entities = scope.get("entities", [])
            switch = bool(prior_entities and entities != prior_entities)
            if switch:
                # Fiscal dates of the old issuer cannot resolve the new issuer's "this quarter".
                issuer_history = []
            saved_quarter = (previous or {}).get("skill_fiscal_quarter", {})
            if issuer and not switch and (new_followup or retry_report) and fiscal_followup.report_issuer(prior_report) == issuer:
                current = {r["id"]: r for r in stored["evidence"] + stored["calculations"]}
                recovered = fiscal_followup.reviewed_quarter(prior_report, issuer, current, scope.get("cutoff"))
                if recovered:
                    saved_quarter = recovered
                elif saved_quarter.get("evidence_ids"):
                    # The reviewed inputs may have been superseded or moved beyond the cutoff.
                    saved_quarter = {}
            anchor = saved_quarter.get("quarter") if saved_quarter.get("entity") == issuer and not switch else None
            if not anchor and episode.get("retry_of_version"):
                # Legacy checkpoints did not save the fiscal target. Reconstruct the history
                # preceding the original request, ignoring its contiguous replay instructions.
                while issuer_history and (retry_instruction(issuer_history[-1]) or
                        request_wording(issuer_history[-1]) == request_wording(task_message)):
                    issuer_history = issuer_history[:-1]
            prior_quarter = anchor
            if not prior_quarter and not (previous or {}).get("retry_period_bounds"):
                prior_quarter = skill_calculations.requested_quarter(issuer_history)
            target_quarter = (anchor if anchor and episode.get("retry_of_version") else
                              skill_calculations.requested_quarter([time_message], prior_quarter))
            if anchored_predecessor:
                target_quarter = None  # This request names a fresh disclosure as its anchor.
            stated_end = explicit_quarter_end(time_message) if issuer and target_quarter else None
            if stated_end:
                quarter_dates["period_end"] = stated_end
            pending = episode.get("retry_period_bounds", {})
            if (resumed and pending.get("requested_quarter")
                    and (switch or annual or prior_target
                         or quarter_labels and pending["requested_quarter"] not in quarter_labels
                         or stated_end and stated_end != pending.get("period_end"))):
                episode.pop("retry_period_bounds")
            inherited_quarter = bool(new_followup and not switch and not prior_target and not annual
                                     and target_quarter == prior_quarter and not quarter_labels)
            if return_reference:
                edited_dates = any(scope.get(k) != prior_scope.get(k) for k in ("period_start", "period_end"))
                if isinstance(return_reference, int):
                    historical = self._historical_quarter(session_id, issuer, scope, version=return_reference, match_dates=edited_dates)
                target_quarter = historical["quarter"] if historical else None
                if historical:
                    quarter_dates = {k: historical[k] for k in ("period_start", "period_end")}
                return_bounds = ({k:scope[k] for k in ("period_start", "period_end") if scope.get(k)}
                                 if edited_dates else quarter_dates)
            if replaying_quarter:
                archived = retry_report.get("skill_fiscal_quarter", {})
                archived_scope = retry_report["scope"]
                same_window = not edited_retry and issuer and archived.get("entity") == issuer and all(
                    scope.get(field) == archived_scope.get(field) for field in period_fields)
                if same_window:
                    target_quarter = archived.get("quarter")
                    quarter_dates = {field: archived[field] for field in period_fields if archived.get(field)}
                else:
                    # An edited window supersedes the relative question's old anchor.
                    target_quarter = None
                    quarter_dates = {field: scope[field] for field in period_fields if scope.get(field)}
                    if quarter_dates:
                        episode["retry_period_bounds"] = {"entity": issuer, **quarter_dates}
                    original_parts, parts = fiscal_followup.inherited_questions(
                        retry_report, retry_report, archived.get("quarter"), "指定期间")
                    episode["inherited_parts"] = parts
                    original_questions = retry_report.get("request_parts", {})
                    replacement = {original_questions[key]: text for key, text in parts.items()
                                   if key in original_questions}
                    archived_requirements = {row["id"]: row["text"]
                                             for row in archived_scope.get("requirements", [])}
                    scope = {**scope, "requirements": [
                        {**row, "text": replacement[row["text"]]}
                        if archived_requirements.get(row["id"]) == row["text"] and row["text"] in replacement
                        else row for row in scope.get("requirements", [])]}
                    session = self.store.update_scope(session_id, scope)
                episode["quarter_followup_parts"] = original_parts
            shift = (not edited_dates if return_reference else
                     not replaying_quarter and (prior_target or (prior_quarter and target_quarter != prior_quarter)))
            if (auto and (new_followup or retry_report) and issuer and not (assigned_task or switch or shift)
                    and saved_quarter.get("entity") == issuer and target_quarter == anchor and anchor
                    and fiscal_followup.report_issuer(prior_report) == issuer
                    and all(not scope.get(k) or scope[k] == saved_quarter.get(k) for k in ("period_start","period_end"))
                    and all(session_scope_at_start.get(k) == prior_scope.get(k)
                            for k in ("requirements", "metrics", "period_start", "period_end"))
                    and not annual):
                focus = fiscal_followup.same_quarter_focus(task_message, retry_report or prior_report,
                                                          retry=retry_report is not None)
                if focus:
                    quarter_dates = {k:saved_quarter[k] for k in ("period_start","period_end") if saved_quarter.get(k)}
                    episode["followup_focus"] = {**focus, "entity":issuer, "quarter":target_quarter, **quarter_dates}
            if (new_followup and not prior_quarter and target_quarter
                    and any(scope.get(k) for k in ("period_start", "period_end"))
                    and all(scope.get(k) == prior_scope.get(k) for k in ("period_start", "period_end"))):
                # Earlier questions may omit the fiscal label even when their report has dates.
                # Resolve the explicitly named quarter before reusing those dates as tool defaults.
                shift = True
            edited_window = (new_followup and len(quarter_labels) == 1 and not switch
                             and period_edits.intersection(period_fields)
                             and (not stated_end or stated_end == scope.get("period_end")))
            if edited_retry or edited_window:
                quarter_dates = {key: scope[key] for key in period_fields if scope.get(key)}
                bounds = {"entity": issuer, **quarter_dates}
                shift = False
                if edited_retry:
                    target_quarter = stated_end = None
                else:
                    bounds["requested_quarter"] = next(iter(quarter_labels))
                    matched = self._historical_quarter(session_id, issuer, scope) if issuer and quarter_dates else None
                    if matched and matched["quarter"] == bounds["requested_quarter"]:
                        target_quarter = matched["quarter"]
                    else:
                        if matched:
                            bounds["conflicting_quarter"] = matched["quarter"]
                        target_quarter = None
                if quarter_dates or edited_window or replaying_quarter:
                    episode["retry_period_bounds"] = bounds
            if (period_followup and issuer and target_quarter and not switch
                    and fiscal_followup.report_issuer(prior_report) == issuer and not assigned_task
                    and all(session_scope_at_start.get(k) == prior_scope.get(k)
                            for k in ("requirements", "period_start", "period_end"))):
                original_parts, parts = fiscal_followup.inherited_questions(
                    prior_report, previous, prior_quarter, target_quarter)
                episode.update(inherited_parts=parts, quarter_followup_parts=original_parts)
                if anchor and saved_quarter.get("period_start"):
                    documents = getattr(getattr(self.tools, "data", None), "manifest", {}).get("documents", [])
                    quarter_dates = fiscal_followup.adjacent_dates(saved_quarter, documents, scope.get("cutoff"))
                boundary = {"entities": [issuer], "open_peers": False}
            if entities != prior_entities or (shift and issuer) or (stated_end and stated_end != scope.get("period_end")):
                scope = dict(scope)
                if switch or shift or (stated_end and stated_end != scope.get("period_end")):
                    scope.pop("period_start", None)
                    scope.pop("period_end", None)
                    if return_reference or stated_end:
                        scope.update(quarter_dates)
                if switch:
                    scope.pop("metrics", None)
                scope["entities"] = entities
                if original_parts and period_followup:
                    scope.update(quarter_dates)
                    scope["requirements"] = [{"id": "request:" + episode["id"] + ":" + key, "text": value}
                                             for key, value in parts.items()]
                session = self.store.update_scope(session_id, scope)
                if resumed and (shift or set(prior_entities).difference(entities)):
                    # Discovery responses can contain source metadata with no evidence IDs to filter.
                    episode.pop("last_tool_result", None)
                    episode.pop("recent_tool_results", None)
                if resumed and set(prior_entities).difference(entities) and entities and (declared or amendment):
                    notice = ("当前研究对象限定为 " + "、".join(entities) + "。沿用前问中仍适用的指标、期间和证据要求。"
                              "已移出的发行人及其公司间比较不再属于本轮待答项。")
                    episode["issuer_amendment"] = {"entities":entities, "user_reply":message, "instruction":notice}
                    episode["clarified_parts"] = {key:notice+" "+value
                        for key,value in (episode.get("clarified_parts") or {"part-1":message}).items()}
                    if self.planner is None and same_checklist:
                        current = [{"id":"request:"+episode["id"], "text":notice+" 本次更正："+message}]
                        session = self.store.update_scope(session_id, {**scope,"requirements":current})
                        scope = session["scope"]
        if use_current_request and relative_only and not original_parts:
            scope = {**scope, "requirements": [{"id": "request:" + episode["id"], "text": message}]}
            session = self.store.update_scope(session_id, scope)
        if auto and self.model is not None and (use_current_request or retry_report) and not switch:
            from .margin_formula import formula_followup, reviewed_followup_target

            if formula_followup(task_message):
                reference = retry_report or prior_report
                if version := reference.get("margin_formula_target", {}).get("report_version"):
                    reference = self.store.get_report(session_id, version)
                current = {r["id"]: r for r in stored["evidence"] + stored["calculations"]}
                if target := reviewed_followup_target(task_message, reference, scope, current):
                    episode["margin_formula_target"] = {**target, "report_version": reference["version"]}
                    quarter_dates = {k: target[k] for k in ("period_start", "period_end")}
                    session = self.store.update_scope(session_id, {**scope, **quarter_dates})
        if boundary:
            episode["user_issuer_boundary"] = boundary
        else:
            episode.pop("user_issuer_boundary", None)
        if (auto or period_followup or return_reference) and target_quarter and issuer:
            episode["skill_fiscal_quarter"] = historical or {"entity": issuer, "quarter": target_quarter, **quarter_dates}
        if auto:
            skills = skill_routing.select_skills(task_message, issuer_history) if self.model is None else []
            episode["research_skills"] = skills
            work = (skill_calculations.prepare(task_message, issuer_history, skills, scope,
                        quarter_anchor=prior_quarter, target_quarter=target_quarter)
                    if self.model is None and issuer and scope.get("entities") == [issuer] else None)
            if annual and issuer:
                episode["requested_fiscal_year"] = {"entity":issuer, "fiscal_year":annual}
            if self.model is None and hasattr(self.tools, "data"):
                from .statement_analysis import ANALYZERS
                expense_followup = (episode.get("followup_focus") or {}).get("aspect") == "expense_contribution"
                unresolved_issuer = ("已识别比较范围 " + "、".join(entities) + "，需要分别核对各公司的可比期间和证据。"
                                     if len(entities) > 1 else "需要先明确本项分析所属的发行人。")
                source_task = skill_calculations.source_request(task_message, issuer_history)
                formula_query = skill_calculations.FORMULA_QUERY.fullmatch(task_message.strip())
                from .sales_comparison import supports_parts
                if original_parts and target_quarter and supports_parts(original_parts):
                    work = {"method": "quarter_followup", "entity": issuer, "quarter": target_quarter,
                            "parts": original_parts, "previous_period_start": saved_quarter.get("period_start"),
                            "steps": [], "operands": [], "calculations": [], **quarter_dates}
                elif formula_query:
                    work = None
                    episode["statement_notes"], gap = skill_calculations.saved_formula(self.store, session_id, formula_query)
                    if gap:
                        episode["skill_gap"] = gap
                    self.store.add_event(session_id, {"type":"saved_calculation_lookup", "episode_id":episode["id"],
                        "calculation_ids":[i for n in episode["statement_notes"] for i in n["calculation_ids"]], "gap":gap})
                elif (operating_margin.requested(task_message) and "incremental-profit-margin" not in skills) or expense_followup:
                    dates = {k: saved_quarter[k] for k in ("period_start", "period_end") if saved_quarter.get(k)} if (
                        target_quarter and saved_quarter.get("entity") == issuer and saved_quarter.get("quarter") == target_quarter) else {}
                    work, gap = operating_margin.prepare(task_message, issuer, target_quarter, {**dates, **scope}, issuer_history)
                    if work and expense_followup:
                        work.update(explain=True, expense_followup=True)
                    episode["statement_notes"] = []
                    if gap:
                        episode["skill_gap"] = gap
                elif any(s in ANALYZERS for s in skills) or source_task:
                    work = None
                    if not issuer:
                        episode["skill_gap"] = unresolved_issuer
                    elif not session["scope"].get("cutoff"):
                        episode["skill_gap"] = "需要明确可见资料的研究截止时点。"
                    elif prior_target:
                        episode["skill_gap"] = "需先核实该发行人所指财季的起止时间，再选择该期间的披露。"
                    else:
                        work = {"method":source_task or "statement", "entity":issuer, "steps":[], "operands":[], "calculations":[]}
                elif not work and skills:
                    episode["skill_gap"] = (unresolved_issuer if not issuer else
                        f"现有资料还未确定 {issuer} 本次问题的可比期间和对应数值。")
            if self.model is None:
                episode["skill_selection_source"] = "offline_rules"
                self.store.add_event(session_id, {"type":"skill_selection", "episode_id":episode["id"],
                                                  "skills":skills, "selection":"offline_rules"})
        else:
            work = None
        if source_lookup:
            episode["model_mode"] = "offline_tools_only"
            quarter = episode.get("skill_fiscal_quarter", {}).get("quarter")
            if archived_source:
                episode["archived_source"] = archived_source
                finding = {"status": "source_excerpt", "answer_sentence": archived_source["url"],
                           "evidence_ids": archived_source["evidence_ids"]}
            else:
                finding = {"status": "gap", "missing_support": source_link_followup.ARCHIVE_GAP}
            episode["offline_part_findings"] = {"part-1": {**finding, "quarter": quarter}}
        acquisition_start = None
        latest_request = (self.planner is None and self.model is not None
                          and getattr(getattr(self.tools, "data", None), "allow_network", False)
                          and latest_quarter.requested(task_message) and len(scope.get("entities", [])) <= 1)
        resolving_latest = latest_request and not scope.get("entities")
        if latest_request:
            target_previous = skill_calculations.latest_reported_target(task_message) == "previous"
            episode["latest_sec_gap"] = ("尚未核实最新披露财季的上一季及其 SEC 原表。" if target_previous else
                                         "尚未取得足以核实最新已公布财季的 SEC 申报。")
            if not resolving_latest:
                acquisition_start = [scope["entities"][0], scope.get("cutoff"), task_message]
        income_plan = None
        if auto and hasattr(self.tools,"data") and issuer and income_followup.parts_for(task_message,request_parts(task_message)):
            parts=episode.get("clarified_parts") or episode.get("inherited_parts") or request_parts(task_message) or {"part-1":task_message}
            referring=bool((new_followup or retry_report) and not switch)
            window=income_followup.prior_source_window(prior_report, {
                r["id"]:r for r in stored["evidence"]},issuer,scope.get("cutoff")) if referring else {}
            income_plan=income_followup.plan(task_message,parts,scope,window,referring=referring,
                resolved_quarter=quarter_dates if target_quarter and not switch else None)
            if income_plan:
                episode.setdefault("inherited_parts",parts)
                if self.model is None:
                    work=None
                if income_plan.get("gap") and self.model is None:
                    episode["skill_gap"]=income_plan["gap"]
                    episode["offline_part_findings"]={p:{"status":"gap","missing_support":income_plan["gap"]}
                                                      for p in income_plan["part_ids"]}
        # A new user turn can change the task; internal replanning cannot
        # remove already resolved obligations to manufacture completeness.
        requirements_locked = bool(original_parts) or bool(retry_report and any(
            not item["id"].startswith("request:") or item["text"] != episode["request"]
            for item in session["scope"].get("requirements", [])))
        bootstrap = {
            "engine_settings": self._continuation_settings(),
            "message": message, "auto": auto, "new_followup": new_followup,
            "source_lookup": source_lookup, "declared": declared, "companies": companies,
            "resumed": bool(resumed), "same_checklist": same_checklist,
            "clarification_checklist_hash": (previous or {}).get("clarification_checklist_hash"),
            "retrying": retry_report is not None or bool(original_parts),
            "prior_report_version": prior_report["version"] if prior_report else None,
            "inherited_quarter": inherited_quarter, "return_reference": return_reference,
            "return_bounds": return_bounds, "stated_end": stated_end, "issuer": issuer,
            "target_quarter": target_quarter, "quarter_dates": quarter_dates,
            "latest_request": latest_request, "income_plan": income_plan,
        }
        state = {
            "work": work, "requirements_locked": requirements_locked, "boundary": boundary,
            "replacement_offer": replacement_offer, "task_message": task_message,
            "resolving_latest": resolving_latest, "acquisition_start": acquisition_start,
            "acquisition_receipts": [], "acquisition_scope": None,
            "income_attempted": False, "pending_income_draft": None,
            "pending_recovery_calculation": None, "role_seconds": {},
        }
        return episode, bootstrap, state

    def _run_turn(self, session_id, episode, bootstrap, journal, state, stage):
        from . import skill_routing, skill_calculations
        from .data import cutoff_time

        limits = Budget(**episode["budget"])
        message, auto = bootstrap["message"], bootstrap["auto"]
        source_lookup, new_followup = bootstrap["source_lookup"], bootstrap["new_followup"]
        declared, companies = bootstrap["declared"], bootstrap["companies"]
        resumed, same_checklist = bootstrap["resumed"], bootstrap["same_checklist"]
        retrying = bootstrap["retrying"]
        inherited_quarter, return_reference = bootstrap["inherited_quarter"], bootstrap["return_reference"]
        return_bounds, stated_end = bootstrap["return_bounds"], bootstrap["stated_end"]
        issuer, target_quarter, quarter_dates = bootstrap["issuer"], bootstrap["target_quarter"], bootstrap["quarter_dates"]
        latest_request, income_plan = bootstrap["latest_request"], bootstrap["income_plan"]
        prior_report = (self.store.get_report(session_id, bootstrap["prior_report_version"])
                        if bootstrap["prior_report_version"] else None)
        work, requirements_locked = state["work"], state["requirements_locked"]
        boundary, replacement_offer, task_message = state["boundary"], state["replacement_offer"], state["task_message"]
        resolving_latest, acquisition_start = state["resolving_latest"], state["acquisition_start"]
        acquisition_receipts, acquisition_scope = state["acquisition_receipts"], state["acquisition_scope"]
        acquisition, sec_step = restore_acquisition(acquisition_start, acquisition_receipts, acquisition_scope)
        income_attempted, pending_income_draft = state["income_attempted"], state["pending_income_draft"]
        pending_recovery_calculation, role_seconds = state["pending_recovery_calculation"], state["role_seconds"]
        began = self.clock()
        elapsed_before = episode["active_seconds"]

        def elapsed():
            return elapsed_before + max(0.0, self.clock() - began)

        def checkpoint(next_stage=None, **handoff):
            with self.store.atomic_step():
                episode["active_seconds"] = elapsed()
                event = self._checkpoint(session_id, episode)
                if next_stage:
                    journal.save(episode, event, bootstrap, next_stage, {
                        "work": work, "requirements_locked": requirements_locked, "boundary": boundary,
                        "replacement_offer": replacement_offer, "task_message": task_message,
                        "resolving_latest": resolving_latest, "acquisition_start": acquisition_start,
                        "acquisition_receipts": acquisition_receipts, "acquisition_scope": acquisition_scope,
                        "income_attempted": income_attempted, "pending_income_draft": pending_income_draft,
                        "pending_recovery_calculation": pending_recovery_calculation, "role_seconds": role_seconds,
                        **handoff,
                    })
                return event

        def reserve_attempt(kind, operation, *, started_event=None):
            with self.store.atomic_step():
                guard(kind)
                if not journal.inputs_current():
                    raise StopEpisode("role_inputs_changed")
                if kind in {"model", "tool"}:
                    episode[kind + "_calls"] += 1
                if started_event:
                    self.store.add_event(session_id, started_event)
                journal.attempt(episode, kind, operation)
                checkpoint()

        @contextmanager
        def local_handoff():
            # A stop after a tool return must retain the receipt, including
            # when there is no time or allowance for another external action.
            stopped = None
            with self.store.atomic_step():
                try:
                    yield
                except StopEpisode as exc:
                    stopped = exc
                    checkpoint("deliver", reason=str(exc))
            if stopped is not None:
                raise stopped

        def guard(kind=None):
            if self.store.is_cancelled(session_id):
                raise StopEpisode("cancelled")
            if elapsed() >= limits.max_seconds:
                raise StopEpisode("time_budget_exhausted")
            if kind == "model" and episode["model_calls"] >= limits.max_model_calls:
                raise StopEpisode("model_budget_exhausted")
            if kind == "tool" and episode["tool_calls"] >= limits.max_tool_calls:
                raise StopEpisode("tool_budget_exhausted")
            if kind == "planner" and episode.get("planner_steps", 0) >= limits.max_planner_steps:
                raise StopEpisode("planner_budget_exhausted")

        def model_call(role, context, *, commit_result=None, on_return=None, reserve_seconds=0):
            reserve_attempt("model", role, started_event={"type": "step_started", "episode_id": episode["id"], "role": role})
            original_timeout = getattr(self.model, "timeout", None)
            if original_timeout is not None:
                self.model.timeout = max(0.01, min(original_timeout, limits.max_seconds - elapsed() - reserve_seconds))
            original_cancel_check = getattr(self.model, "cancel_check", None)
            if hasattr(self.model, "cancel_check"):
                self.model.cancel_check = lambda: self.store.is_cancelled(session_id)
            started = self.clock()
            # A failed call must not reuse a previous call's token counts.
            if hasattr(self.model, "last_usage"):
                self.model.last_usage = {}
            if hasattr(self.model, "last_response_metadata"):
                self.model.last_response_metadata = {}
            event = {"type": "model_call", "episode_id": episode["id"], "role": role,
                     "mode": getattr(self.model, "mode", "configured_model"),
                     "model": getattr(self.model, "model", None),
                     "base_url": getattr(self.model, "base_url", None),
                     "api_mode": getattr(self.model, "api_mode", None),
                     "service_tier": getattr(self.model, "service_tier", None)}
            if role == "planner" and episode.get("new_followup") and hasattr(self.tools, "current_document_catalog"):
                access = dict(context.get("evidence_access", {}))
                access["current_local_manifest"] = self.tools.current_document_catalog(context["scope"])
                context = {**context, "evidence_access": access}
            if role == "critic":
                event["draft_scope_version"] = context["session"]["scope_version"]
                event["draft_claim_versions"] = {c["id"]: c["version"] for c in context["claims"]}
            if getattr(self.model, "method_pack", None):
                event.update(method_pack=self.model.method_pack, method_pack_sha256=self.model.method_pack_sha256)
            elif episode.get("research_skills"):
                event["research_skills"] = episode["research_skills"]
            try:
                # Compact Planner may receive previews that omit source fields.
                # Its latest observation must remain available on the compact path.
                if self.reference_duplicate_records and (role != "planner" or getattr(self.model, "planner_view", "full") == "full"):
                    from .context import reference_tool_records
                    context = reference_tool_records(context)
                response = self.verifier.review(context) if role == "critic" else self.model.complete(role, context)
                event["ok"] = True
            except SpendLimitReached:
                episode["model_calls"] -= 1
                event.update(type="spend_reservation_declined", ok=False)
                raise StopEpisode("spend_budget_exhausted") from None
            except ContextLimitReached as exc:
                episode["model_calls"] -= 1
                episode["context_limit"] = {"role":role, "characters":exc.size, "limit":exc.limit}
                event.update(type="context_limit_before_send", ok=False, **episode["context_limit"])
                raise StopEpisode("local_context_limit") from None
            except Exception as exc:
                # Never echo raw provider responses or credentials to the report.
                event.update(ok=False, error_type=type(exc).__name__)
                if getattr(self.model, "provider", None) == "codex" and isinstance(getattr(exc, "code", None), str):
                    event["diagnostic_code"] = "codex_" + exc.code
                    from .codex_provider import CodexTransportError
                    if isinstance(exc, CodexTransportError):
                        episode["codex_failure"] = {"role": role, "hint": exc.user_hint}
                if getattr(exc, "codex_stop_reason", None) == "cancelled":
                    raise StopEpisode("cancelled") from None
                from .model import ProviderOutputError
                if isinstance(exc, OutputContractError):
                    event["contract_error"] = exc.code
                if isinstance(exc, ProviderOutputError):
                    event["provider_output_error"] = exc.code
                if role == "researcher" and isinstance(exc, (ProviderOutputError, OutputContractError)):
                    episode["researcher_output_error"] = exc.code
                if role == "planner" and isinstance(exc, ModelError):
                    event["diagnostic_code"] = event.get("contract_error") or event.get("provider_output_error") or event.get("diagnostic_code") or "model_error_unclassified"
                raise
            finally:
                event.update(seconds=self.clock() - started,
                    response_metadata=getattr(self.model, "last_response_metadata", {}) or {},
                    usage={k: v for k, v in (getattr(self.model, "last_usage", {}) or {}).items()
                           if k in USAGE_FIELDS and type(v) is int and v >= 0})
                if event.get("ok") and role in ("critic", "researcher"):
                    role_seconds[role] = event["seconds"]
                if not event.get("ok") or commit_result is None and on_return is None:
                    saved_event = self.store.add_event(session_id, event)
                    if role == "planner" and "diagnostic_code" in event:
                        episode["planner_error"] = {k: saved_event[k] for k in
                                                    ("error_type", "diagnostic_code", "event_sequence")}
                if original_timeout is not None:
                    self.model.timeout = original_timeout
                if hasattr(self.model, "cancel_check"):
                    self.model.cancel_check = original_cancel_check
                for key, value in (getattr(self.model, "last_usage", {}) or {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                        episode["usage"][key] = episode["usage"].get(key, 0) + value
                        if key in USAGE_FIELDS and type(value) is int:
                            counter = key + "_reported_calls"
                            episode["usage"][counter] = episode["usage"].get(counter, 0) + 1
            if commit_result is not None or on_return is not None:
                # A return already received can be committed locally after a
                # stop. No subsequent model/tool call may start. Bind the
                # receipt, last reviewed version and restart checkpoint.
                prior = episode.copy()
                try:
                    with self.store.atomic_step():
                        self.store.add_event(session_id, event)
                        current = (self._role_inputs_current(session_id, role, context, response)
                                   if role != "planner" else self.store.matches_role_inputs(
                                       session_id, context["session"]["scope_version"],
                                       ((kind, row) for kind, name in (("evidence", "evidence"), ("calculation", "calculations"),
                                                                      ("claim", "claims")) for row in context[name])))
                        if current:
                            if commit_result is not None:
                                commit_result(response)
                            if on_return is not None:
                                on_return(response)
                        else:
                            episode.update(reviewed=False, request_coverage=[])
                            self.store.add_event(session_id, {"type":"role_result_superseded", "role":role,
                                "episode_id":episode["id"], "reason":"scope_or_cited_record_changed_during_call"})
                        if on_return is None or not current:
                            checkpoint()
                except Exception as exc:
                    # Match the rolled-back transaction. A failed checkpoint
                    # must not publish a draft that never reached storage.
                    episode.clear()
                    episode.update(prior, reviewed=False)
                    self.store.add_event(session_id, {**event, "local_commit_error":type(exc).__name__})
                    raise
            guard()
            if (commit_result is not None or on_return is not None) and not current:
                raise StopEpisode("role_inputs_changed")
            return response

        try:
            while True:
                if stage == "deliver":
                    raise StopEpisode(state["reason"])
                guard()
                if source_lookup:
                    raise StopEpisode("offline_tools_only")
                if stage in {"plan", "route"}:
                    planner_result = False
                    if stage == "plan":
                        if episode["no_progress"] >= limits.max_no_progress:
                            raise StopEpisode("no_progress")
                        guard("planner")
                        episode["planner_steps"] = episode.get("planner_steps", 0) + 1
                        ctx = self._context(session_id, episode)
                        recovery = episode.get("tool_recovery")
                        finalize_recovery = pending_recovery_calculation is not None
                        step = sec_step or (skill_calculations.next_action(work, ctx) if work is not None else None)
                        if (self.model is None and self.planner is None and auto and new_followup
                                and work is None and not income_plan and not step):
                            from . import offline_attribution
                            args = None
                            if episode["tool_calls"] == 0:
                                args = offline_attribution.initial_search(ctx, prior_report)
                                if args:
                                    episode["offline_attribution_search"] = True
                            elif episode.get("offline_attribution_search"):
                                if episode["tool_calls"] < limits.max_tool_calls:
                                    args = issuer_attribution.next_search(ctx, {"action":"research"})
                                if args:
                                    episode["attribution_search_expanded"] = True
                                else:
                                    episode["offline_part_findings"] = offline_attribution.findings(ctx)
                            if args:
                                step = {"action":"tool", "tool":"search_documents", "arguments":args}
                        income_action={"action":"tool","tool":"reconcile_net_income","arguments":income_plan["arguments"]} if (
                            income_plan and "arguments" in income_plan and not income_attempted and
                            any(t["name"]=="reconcile_net_income" for t in ctx["tools"])) else None
                        if income_action and self.model is None:
                            step=income_action
                        reason_step = income_followup.reason_action(ctx) if income_plan else None
                        if not sec_step and reason_step and episode["tool_calls"] < limits.max_tool_calls and not episode.get("income_reason_attempted"):
                            step = reason_step
                        if pending_recovery_calculation is not None:
                            step = {"action":"tool", "tool":"calculate", "arguments":pending_recovery_calculation[0]}
                        cash_handoff = None
                        income_handoff = False
                        if step is not None:
                            decision = step
                        elif self.planner is None:
                            if work is not None:
                                episode["skill_calculations"] = list(work["calculations"])
                                if work["method"] == "operating_margin":
                                    episode["statement_notes"] = work["notes"]
                                    episode["offline_part_findings"] = work["part_findings"]
                                    if selected := work.get("selected"):
                                        cell = selected[0, "revenue"]
                                        if work.get("quarter"):
                                            episode["skill_fiscal_quarter"] = {"entity": work["entity"], "quarter": work["quarter"],
                                                "period_start": cell["period_start"], "period_end": cell["period_end"]}
                                if work.get("gap"):
                                    episode["skill_gap"] = work["gap"]
                                    ctx["episode"]["skill_gap"] = work["gap"]
                                work = None
                            if self.model is None:
                                raise StopEpisode("offline_tools_only")
                            if auto:
                                ctx["skill_catalog"] = dict(skill_routing.catalog())
                            cash_handoff = planner_evidence.cash_draft_instruction(ctx) if auto else None
                            if (income_attempted and not episode.get("draft_attempted") and income_plan
                                    and set(income_plan["part_ids"])==set(ctx.get("request_parts",{}))):
                                income_handoff = True
                                decision={"action":"research","reason":"说明已核对的同比贡献，区分会计调节与披露的经营原因；若缺项则保留可核实金额，明确哪一行哪一期缺资料。"}
                                self.store.add_event(session_id,{"type":"controller_route","episode_id":episode["id"],
                                    "from":"reconcile_net_income","to":"research","reason":"quarterly_income_statement_examined"})
                            elif cash_handoff:
                                decision = {"action": "research", "reason": cash_handoff}
                                self.store.add_event(session_id, {"type": "controller_route", "episode_id": episode["id"],
                                    "from": "analyze_disclosures", "to": "research", "reason": "reconciled_cash_flow"})
                            else:
                                if replacement_offer:
                                    ctx["episode"]["clarification_reply"] = replacement_offer
                                planner_result = True
                                decision = model_call("planner", ctx, on_return=lambda response:
                                                      checkpoint("route", decision=response, planner_result=True))
                        else:
                            from .model import validate_response
                            reserve_attempt("fixed_planner", "planner")
                            started = self.clock()
                            try:
                                decision = validate_response("planner", self.planner.decide(ctx), ctx)
                            except Exception as exc:
                                self.store.add_event(session_id, {"type": "fixed_planner_step", "episode_id": episode["id"], "ok": False,
                                                                   "error_type": type(exc).__name__, "seconds": self.clock() - started})
                                raise
                            with self.store.atomic_step():
                                self.store.add_event(session_id, {"type": "fixed_planner_step", "episode_id": episode["id"], "ok": True,
                                                                   "seconds": self.clock() - started})
                                checkpoint("route", decision=decision, planner_result=False)
                            guard()
                    else:
                        ctx = self._context(session_id, episode)
                        decision, planner_result = state["decision"], state["planner_result"]
                        if replacement_offer and planner_result:
                            ctx["episode"]["clarification_reply"] = replacement_offer
                        recovery = episode.get("tool_recovery")
                        finalize_recovery = pending_recovery_calculation is not None
                        step = reason_step = cash_handoff = income_handoff = None
                        income_action = {"action":"tool","tool":"reconcile_net_income","arguments":income_plan["arguments"]} if (
                            income_plan and "arguments" in income_plan and not income_attempted and
                            any(t["name"]=="reconcile_net_income" for t in ctx["tools"])) else None
                    with local_handoff():
                        if not journal.inputs_current():
                            raise StopEpisode("role_inputs_changed")
                        if planner_result:
                            if "clarification_relation" in decision:
                                from .model import validate_response
                                validate_response("planner", decision, ctx)
                                # Keep the consumed budget and immutable history. Use the
                                # member's actual words as the new coverage obligation.
                                task_message = episode["request"] = message
                                episode["clarified_parts"] = request_parts(message)
                                episode.pop("inherited_parts", None)
                                boundary = {"entities": list(declared), "open_peers": False}
                                episode["user_issuer_boundary"] = boundary
                                decision = {**decision, "scope": {**decision["scope"],
                                    "requirements": [{"id":"request:" + episode["id"], "text":message}]}}
                                decision.pop("requirements", None)
                                self.store.add_event(session_id, {"type":"clarification_replaced",
                                    "episode_id":episode["id"], "entities":list(declared)})
                            replacement_offer = None
                        if not isinstance(decision, dict):
                            raise ValueError("Planner must return an object")
                        if resolving_latest and decision.get("action") != "clarify":
                            args = decision.get("arguments", {})
                            entities = decision.get("scope", {}).get("entities", [])
                            query = args.get("query") if decision.get("tool") == "discover_company" else args.get("entity")
                            query = query or (entities[0] if len(entities) == 1 else None)
                            if not query:
                                decision = {"action": "clarify", "question": "请提供要查询的发行人名称或股票代码。"}
                            else:
                                decision = {"action": "tool", "tool": "discover_company",
                                            "arguments": {"query": query, "view": "identity"}}
                        if recovery and decision.get("action") == "tool" and (
                                decision.get("tool") == recovery["tool"]
                                and decision.get("arguments", {}) == recovery["arguments"]
                                and all(ctx["scope"].get(k) == v for k, v in decision.get("scope", {}).items())):
                            # Nothing has changed since the rejected action. Skip the
                            # identical call and reserve the remaining work for review.
                            finalize_recovery = True
                            decision = {"action":"research", "reason":recovery["instruction"]}
                            self.store.add_event(session_id, {"type":"controller_route", "episode_id":episode["id"],
                                "from":"tool", "to":"research", "reason":"unchanged_recovery_action"})
                        if latest_request and decision.get("tool") in {"discover_filings", "financial_facts"}:
                            decision = {**decision, "arguments": {**decision["arguments"], "source": "live"}}
                        if episode.get("user_cutoff") and "cutoff" in decision.get("scope", {}):
                            decision = {**decision, "scope":{**decision["scope"], "cutoff":episode["user_cutoff"]}}
                        self.store.add_event(session_id, {"type": "planner_decision", "episode_id": episode["id"], "decision": decision,
                            **({"decision_source": "controller"} if sec_step or cash_handoff or income_handoff or
                               (step is not None and (step is income_action or step is reason_step)) else {}),
                            "summary": str(decision.get("reason", decision.get("action", "")))})
                        conflict = skill_routing.issuer_decision_conflict(boundary, decision, companies)
                        if conflict:
                            episode["issuer_conflict"] = conflict
                            self.store.add_event(session_id, {"type":"issuer_scope_conflict","episode_id":episode["id"], **conflict})
                            raise StopEpisode("planner_issuer_conflicts_with_user")
                        if auto and self.model is not None and step is None:
                            if "research_skills" in decision:
                                skills = skill_routing.checked_selection(decision["research_skills"])
                                if skills != episode["research_skills"] or episode.get("skill_selection_source") != "planner":
                                    self.store.add_event(session_id, {"type":"skill_selection", "episode_id":episode["id"],
                                        "skills":skills, "selection":"planner", "reason":decision.get("reason", "")})
                                episode["research_skills"], episode["skill_selection_source"] = skills, "planner"
                            elif "skill_selection_source" not in episode:
                                # Older adapters may omit the field; do not silently invoke keyword routing.
                                episode["skill_selection_source"] = "not_specified"
                                self.store.add_event(session_id, {"type":"skill_selection", "episode_id":episode["id"],
                                    "skills":[], "selection":"not_specified"})
                        if boundary and not boundary["open_peers"] and "entities" in decision.get("scope", {}):
                            # An equivalent alias is a single issuer identity, not a new scope version.
                            decision = {**decision,"scope":{**decision["scope"],"entities":boundary["entities"]}}
                        action = decision.get("action")
                        # A planner's prose reason is never a reviewed report. If
                        # it proposes finishing after acquiring original evidence
                        # but before writing any candidate this turn, route it
                        # through the existing researcher/critic once. Both calls
                        # remain budgeted and may return a partial result.
                        if (self.planner is None and action == "finish" and not episode.get("draft_attempted")
                                and not episode.get("reviewed") and ctx.get("evidence")):
                            action = "research"
                            decision = {**decision, "action": action}
                            self.store.add_event(session_id, {"type":"controller_route","episode_id":episode["id"],
                                                 "from":"finish","to":"research","reason":"source_evidence_without_a_draft"})
                        if self.planner is None:
                            # An explicitly empty array is also an omitted plan. It cannot
                            # erase the user's current obligation or lock discovery to [].
                            if decision.get("requirements") == []:
                                decision = {key:value for key,value in decision.items() if key != "requirements"}
                            if decision.get("scope", {}).get("requirements") == []:
                                decision = {**decision,"scope":{key:value for key,value in decision["scope"].items() if key != "requirements"}}
                        if (self.planner is None and action == "research" and not ctx["requirements"]
                                and not decision.get("requirements") and not decision.get("scope",{}).get("requirements")):
                            # Preserve the new user's exact request as the minimum
                            # coverage obligation, never a model-generated answer.
                            decision = {**decision,"requirements":[{"id":"request:"+episode["id"],"text":episode["request"]}]}
                        if ctx.get("fiscal_target"):
                            decision = fiscal_followup.bound_plan(decision, ctx["fiscal_target"], ctx)
                        elif (return_bounds or stated_end) and {"period_start", "period_end"}.intersection(decision.get("scope", {})):
                            bounds = return_bounds or {"period_end": stated_end}
                            decision = {**decision, "scope": {**decision["scope"], **bounds}}
                        if (target := episode.get("margin_formula_target")) and "scope" in decision:
                            proposed = decision["scope"]
                            bounds = {"entities": [target["entity"]], "period_start": target["period_start"],
                                      "period_end": target["period_end"], "cutoff": ctx["scope"].get("cutoff")}
                            decision = {**decision, "scope": {**proposed, **{k: v for k, v in bounds.items() if k in proposed}}}
                        previous_cutoff = ctx.get("scope", {}).get("cutoff")
                        if resumed and bootstrap["clarification_checklist_hash"] and not same_checklist:
                            # The user edited the checklist while the question was waiting.
                            # Preserve that edit while allowing the proposed research to continue.
                            decision.pop("requirements", None)
                            if "scope" in decision:
                                decision = {**decision, "scope": {k:v for k,v in decision["scope"].items()
                                                                   if k != "requirements"}}
                        changed_scope = self._apply_scope(session_id, decision, preserve_requirements=requirements_locked,
                                                          retrying=retrying)
                        if "requirements" in decision or "requirements" in decision.get("scope", {}) or action == "research":
                            # Pre-upgrade clarification checkpoints may still carry this marker.
                            episode.pop("resolve_followup_checklist", None)
                        # Discovery may precede defining a new user turn's task.
                        # Freeze its checklist when explicitly supplied or first
                        # used for synthesis, not after an unrelated lookup.
                        requirements_locked = requirements_locked or "requirements" in decision or "requirements" in decision.get("scope", {}) or action == "research"
                        if changed_scope:
                            if self.planner is None:
                                existing = {item["id"] for item in ctx["requirements"]}
                                proposed = decision.get("requirements", decision.get("scope", {}).get("requirements", []))
                                additions = {item["id"]: item["text"] for item in proposed if item["id"] not in existing}
                                if additions:
                                    episode.setdefault("planner_added_requirements", {}).update(additions)
                            if ((inherited_quarter or return_reference)
                                    and {"period_start", "period_end"}.intersection(decision.get("scope", {}))):
                                planned_scope = self.store.get_session(session_id)["scope"]
                                current_target = episode.get("skill_fiscal_quarter", {})
                                bounds = {k:v for k in ("period_start", "period_end") if (v := planned_scope.get(k))}
                                if bounds and not all(current_target.get(k) == v for k, v in bounds.items()):
                                    matched = self._historical_quarter(session_id, issuer, planned_scope)
                                    if matched:
                                        episode["skill_fiscal_quarter"] = matched
                                    else:
                                        episode.pop("skill_fiscal_quarter", None)
                                    episode.pop("followup_focus", None)
                            episode.update(reviewed=False, active_claim_ids=[], issues=[], summary="", draft_saved=False,
                                           request_coverage=[], answered_requirement_ids=[], unanswered_requirement_ids=[])
                            for field in ("reviewed_claim_ids", "reviewed_requirement_ids", "reviewed_scope_version", "restored_claim_versions", "draft_reference_issues", "followup_limits"):
                                episode.pop(field, None)
                            episode.pop("revision_memory", None)
                            current_cutoff = self.store.get_session(session_id)["scope"].get("cutoff")
                            if current_cutoff and (not previous_cutoff or cutoff_time(current_cutoff) < cutoff_time(previous_cutoff)):
                                # Tool payloads may include unreferenced future titles or figures.
                                # The immutable tool events remain available for the audit.
                                episode.pop("last_tool_result", None)
                                episode.pop("recent_tool_results", None)
                                episode["summary"] = ""
                        draft_after_tool = pending_income_draft if step is not None and step is reason_step else None
                        if pending_recovery_calculation is not None:
                            _, draft_after_tool = pending_recovery_calculation
                            pending_recovery_calculation = None
                        if action == "research" and not sec_step and not finalize_recovery and auto and self.model is not None and episode["tool_calls"] < limits.max_tool_calls:
                            draft_context = self._context(session_id, episode) if changed_scope else ctx
                            active_scope=draft_context["scope"]
                            if (income_action and active_scope.get("entities")==[income_action["arguments"]["entity"]]
                                    and all(not active_scope.get(k) or not income_action["arguments"].get(k) or
                                            active_scope[k]==income_action["arguments"][k] for k in ("period_start","period_end"))):
                                draft_after_tool=decision
                                decision=income_action
                                action="tool"
                                self.store.add_event(session_id,{"type":"controller_route","episode_id":episode["id"],
                                    "from":"research","to":"reconcile_net_income","reason":"reconcile_comparative_income_statement_before_draft"})
                            else:
                                from .margin_formula import draft_calculation
                                if arguments := draft_calculation(draft_context):
                                    draft_after_tool = decision
                                    action = "tool"
                                    decision = {"action": "tool", "tool": "calculate", "arguments": arguments}
                                    self.store.add_event(session_id, {"type":"controller_route", "episode_id":episode["id"],
                                        "from":"research", "to":"calculate", "reason":"requested_margin_calculation_before_draft"})
                        # Source restrictions come from the user. A document_ids choice by
                        # the Planner alone need not constrain a later search for an explanation.
                        source_ctx = self._context(session_id, episode) if changed_scope else ctx
                        bounded = source_scope.bound_decision(source_ctx, decision)
                        if bounded is not decision:
                            self.store.add_event(session_id, {"type":"controller_route", "episode_id":episode["id"],
                                "from":action, "to":bounded.get("tool", bounded["action"]), "reason":"user_document_scope"})
                            decision, action = bounded, bounded["action"]
                        if (self.model is not None and self.planner is None and step is None and not recovery
                                and episode["tool_calls"] < limits.max_tool_calls and not draft_after_tool):
                            expanded = issuer_attribution.next_search(source_ctx, decision)
                            if expanded:
                                draft_after_tool = decision if action == "research" else None
                                self.store.add_event(session_id, {"type":"controller_route", "episode_id":episode["id"],
                                    "from":action, "to":"search_documents", "reason":"same_period_attribution_search"})
                                episode["attribution_search_expanded"] = True
                                decision = {"action":"tool", "tool":"search_documents", "arguments":expanded}
                                action = "tool"
                        if action == "clarify":
                            question = decision.get("question")
                            if not isinstance(question, str) or not question.strip():
                                raise ValueError("Clarification requires a question")
                            waiting_scope = self.store.get_session(session_id)["scope"]
                            episode.update(status="waiting_for_clarification", question=question,
                                           clarification_cutoff=waiting_scope.get("cutoff"),
                                           clarification_checklist_hash=digest(waiting_scope.get("requirements", [])))
                            with self.store.atomic_step():
                                self.store.append_message(session_id, "assistant", question)
                                completion = checkpoint()
                                self._finish_request(session_id, episode, completion)
                            return {"status": episode["status"], "question": question, "episode": episode}
                        if (action == "tool" and decision.get("tool") == "read_document" and auto
                                and self.model is not None and step is None and not draft_after_tool and not changed_scope
                                and not episode.get("draft_attempted") and not source_ctx["scope"].get("planning_task")):
                            instruction = self._reuse_document_window(session_id, episode, source_ctx, decision.get("arguments", {}), elapsed)
                            if instruction:
                                if (recovery or episode["tool_calls"] >= limits.max_tool_calls
                                        or episode["model_calls"] + 3 > limits.max_model_calls
                                        or episode["planner_steps"] >= limits.max_planner_steps
                                        or episode["no_progress"] >= limits.max_no_progress):
                                    finalize_recovery = True
                                    action = "research"
                                    decision = {"action": action, "reason": instruction + "按原问交付可核实的部分，并标明缺项。"}
                                else:
                                    stage = "plan"
                                    checkpoint(stage)
                                    continue
                        stage = action
                        if stage == "tool":
                            source_limits = {}
                            if lock := source_ctx.get("source_document_scope"):
                                source_limits = {"_source_document_ids": lock["document_ids"],
                                                 "_source_publisher": lock.get("publisher"),
                                                 "_source_record_ids": [row["id"] for row in
                                                                        source_ctx["evidence"] + source_ctx["calculations"]
                                                                        if not row.get("stale")]}
                            skill_step = step is not None
                            tool_action = {
                                "decision": decision, "context": ctx, "source_limits": source_limits,
                                "draft_after_tool": draft_after_tool, "skill_step": skill_step,
                                "finalize_recovery": finalize_recovery,
                            }
                            checkpoint(stage, tool_action=tool_action)
                        elif stage == "research":
                            checkpoint(stage, decision=decision, draft_after_tool=draft_after_tool,
                                       finalize_recovery=finalize_recovery)
                        elif stage == "finish":
                            raise StopEpisode("completed" if self._complete(session_id, episode) else "unresolved_items")
                        else:
                            raise ValueError("Unsupported planner action")
                elif stage == "tool":
                    tool_action = state["tool_action"]
                    decision, ctx, source_limits = (tool_action[key] for key in ("decision", "context", "source_limits"))
                    draft_after_tool, skill_step, finalize_recovery = (
                        tool_action[key] for key in ("draft_after_tool", "skill_step", "finalize_recovery"))
                    recovery = episode.get("tool_recovery")
                elif stage == "research":
                    decision, draft_after_tool, finalize_recovery = (
                        state[key] for key in ("decision", "draft_after_tool", "finalize_recovery"))

                if stage == "tool":
                    guard("tool")
                    name, arguments = decision.get("tool"), decision.get("arguments", {})
                    if name == income_followup.EXPLANATION_TOOL:
                        if episode.get("income_reason_attempted"):
                            raise StopEpisode("income_explanation_already_examined")
                        episode["income_reason_attempted"] = True
                    if name=="reconcile_net_income":
                        if income_attempted:
                            raise StopEpisode("income_statement_already_examined")
                        if income_plan and "arguments" in income_plan and any(
                                arguments.get(k) and arguments[k]!=v for k,v in income_plan["arguments"].items()):
                            raise StopEpisode("income_statement_scope_mismatch")
                        if income_plan and "arguments" in income_plan:
                            arguments={**income_plan["arguments"],**arguments}
                        income_attempted=True
                    if not isinstance(name, str) or not isinstance(arguments, dict):
                        raise ValueError("Tool action requires name and object arguments")
                    if sec_step and arguments.get("source") == "live":
                        # The automatic next read may start sooner than a model-planned one.
                        delay = self.tools.data.sec_request_delay()
                        if delay:
                            time.sleep(min(delay, max(0, limits.max_seconds - elapsed())))
                            guard("tool")
                    reserve_attempt("tool", name)
                    original_timeout = getattr(self.tools, "timeout", None)
                    if original_timeout is not None:
                        self.tools.timeout = max(0.01, min(original_timeout, limits.max_seconds - elapsed()))
                    tool_session = self.store.get_session(session_id)
                    try:
                        result = self.tools.execute(name, arguments, session_id, {**tool_session["scope"], **source_limits})
                    except Exception as exc:
                        result = {"ok": False, "summary": "Tool failed", "error": type(exc).__name__, "evidence": [], "calculations": [], "data": {}}
                    finally:
                        if original_timeout is not None:
                            self.tools.timeout = original_timeout
                    if not isinstance(result, dict):
                        result = {"ok": False, "summary": "Tool returned a non-object result", "error": "InvalidToolResult",
                                  "evidence": [], "calculations": [], "data": {"returned_type": type(result).__name__}}
                    with local_handoff():
                        read_gap = None
                        read_context = ctx
                        if name == "read_document" and self.model is not None and self.planner is None:
                            # An indexed passage may be in storage while its full text is
                            # still new to a planner that received only a preview.
                            read_context = planner_evidence.planner_read_context(self.model, ctx)
                            if planner_evidence.covered_document_read(read_context, result):
                                read_gap = "原文已在当前可见证据中；本次未新增原文、数字记录或计算。"
                                result["data"]["covered_read_recovery"] = True
                        if tool_session["scope_version"] == ctx["session"]["scope_version"]:
                            if name == "read_document":
                                read_gap = planner_evidence.covered_statement_read_gap(read_context, result) or read_gap
                            elif name == "analyze_disclosures":
                                read_gap = planner_evidence.repeated_statement_gap(ctx, arguments, result)
                        if read_gap:
                            result["data"]["acquisition_gap"] = read_gap
                        # Preserve the public observation at call time, including
                        # failures and results returned just as a budget expires.
                        # Adapters already exclude credentials/raw HTTP errors.
                        observation_record = {key: result.get(key, [] if key in {"evidence", "calculations"} else {} if key == "data" else None)
                                              for key in ("data", "evidence", "calculations", "error")}
                        receipt = {"type":"tool_call", "episode_id":episode["id"], "tool":name, "arguments":arguments,
                                   "ok":bool(result.get("ok")), "summary":result.get("summary", ""),
                                   "network_requests":observation_record["data"].get("network_requests", 0) if isinstance(observation_record["data"], dict) else 0,
                                   "observation":observation_record, "observation_sha256":digest(observation_record)}
                        versions = {}
                        try:
                            changed = self.store.record_tool_return(session_id, receipt, record_versions=versions)
                        except (ValueError, KeyError, TypeError) as exc:
                            # Ingestion can reject the records while still committing an
                            # authoritative source change and the rejected receipt.
                            episode.update(reviewed=False, error_type=type(exc).__name__)
                            self.store.add_event(session_id, {"type": "episode_error", "episode_id": episode["id"],
                                                              "error_type": type(exc).__name__})
                            raise StopEpisode("execution_error") from exc
                        if changed:
                            episode["reviewed"] = False
                        episode["last_tool_result"] = {**result, "record_versions":versions} if versions else result
                        if name == "read_document" and result.get("ok"):
                            # The compact history keeps only eight receipts. Keep an explicit
                            # read available for the rest of *this* turn even after it rolls off.
                            opened = episode.setdefault("opened_document_ids", [])
                            for row in result.get("evidence", []):
                                if row.get("kind") == "document" and row.get("id") and row["id"] not in opened:
                                    opened.append(row["id"])
                        if name == "analyze_disclosures":
                            details = result.get("data", {})
                            episode["statement_notes"] = details.get("skill_notes", [])
                            episode["skill_assessments"] = details.get("skill_assessments", [])
                            gaps = details.get("skill_gaps", [])
                            if self.model is None and result.get("ok"):
                                from .statement_analysis import answer_statement_parts
                                parts = ctx.get("request_parts") or {"part-1": episode["request"]}
                                fiscal_target = {**{k:ctx.get("scope", {}).get(k) for k in ("period_start","period_end")},
                                                 **episode.get("skill_fiscal_quarter", {})}
                                answers = answer_statement_parts(parts, details, fiscal_target)
                                if answers:
                                    episode.setdefault("offline_part_findings", {}).update(answers)
                            if gaps or not result.get("ok"):
                                episode["skill_gap"] = " ".join(gaps) or result.get("error")
                            else:
                                episode.pop("skill_gap", None)
                        if work is not None and skill_step:
                            if work["method"] == "quarter_followup" and name == "inspect_sales_comparison" and result.get("ok"):
                                details = result["data"]
                                episode["offline_part_findings"] = details.get("part_findings", {})
                                period = details.get("fiscal_period", {})
                                if (period.get("entity") == issuer and period.get("quarter") == target_quarter
                                        and fiscal_followup.confirmed_dates(period, quarter_dates)):
                                    dates = {k: period[k] for k in ("period_start", "period_end")}
                                    episode["skill_fiscal_quarter"].update(dates)
                                    scope = self.store.get_session(session_id)["scope"]
                                    self.store.update_scope(session_id, {**scope, **dates})
                            if work["method"] == "overview" and name in ("read_document","search_documents") and result.get("ok"):
                                episode["statement_notes"] = skill_calculations.overview_notes(result.get("evidence", []))
                            work["steps"].append({"tool": name, "ok": bool(result.get("ok"))})
                            work["operands"].extend(result.get("data", {}).get("numeric_operand_ids", []))
                            work["calculations"].extend(c["id"] for c in result.get("calculations", []))
                            episode["skill_calculations"] = list(work["calculations"])
                        recent = episode.setdefault("recent_tool_results", [])
                        recent.append({"tool": name, "arguments": arguments, "ok": bool(result.get("ok")),
                                       "scope_version": tool_session["scope_version"],
                                       "summary": result.get("summary", ""), "data": result.get("data", {}),
                                       "error": result.get("error"),
                                       "evidence_ids": [r["id"] for r in result.get("evidence", [])],
                                       "calculation_ids": [r["id"] for r in result.get("calculations", [])]})
                        if (auto and name == "read_document" and result.get("ok")
                                and result["data"].get("numeric_operand_status") == "text_only"):
                            recent[-1]["observation_sha256"] = receipt["observation_sha256"]
                        if name == "search_documents":
                            recent[-1]["search_scope"] = {k:v for k,v in tool_session["scope"].items() if k != "requirements"}
                        if name == "analyze_disclosures":
                            recent[-1]["planner_instruction"] = decision.get("reason", "")
                        if name in ("reconcile_net_income", income_followup.EXPLANATION_TOOL):
                            recent[-1]["income_request"]=episode["request"]
                        if versions:
                            recent[-1]["record_versions"] = versions
                        episode["recent_tool_results"] = recent[-8:]
                        if name == "market_prices":
                            scope_version = tool_session["scope_version"]
                            price_history = episode.get("market_window_history", {})
                            if price_history.get("scope_version") != scope_version:
                                price_history = {"scope_version": scope_version, "receipts": []}
                                episode["market_window_history"] = price_history
                            price_history["receipts"].append({
                                "tool": name, "arguments": arguments, "ok": result["ok"],
                                "error": result.get("error"), "summary": "" if result["ok"] else result.get("summary", ""),
                                "data": {"mode": result["data"].get("mode")}})
                        remember_latest_quarter(episode, recent[-1])
                        if resolving_latest and name == "discover_company":
                            matches = result.get("data", {}).get("companies", [])
                            if result.get("ok") and len(matches) == 1:
                                acquisition_start = [matches[0]["entity"], tool_session["scope"].get("cutoff"), task_message]
                                acquisition_scope = {"entities": [matches[0]["entity"]]}
                                acquisition, sec_step = restore_acquisition(acquisition_start, acquisition_receipts, acquisition_scope)
                                resolving_latest = False
                        elif sec_step:
                            try:
                                sec_step = acquisition.send(result)
                                acquisition_receipts.append(result)
                                if target := sec_step.get("relative_quarter"):
                                    episode["skill_fiscal_quarter"] = {k: v for k, v in target.items() if k != "accession"}
                                if sec_step.get("arguments", {}).get("quarter_operands"):
                                    remember_latest_quarter(episode, recent[-1], accepted_filing=True)
                                if sec_step.get("tool") == "calculate":
                                    remember_latest_quarter(episode, recent[-1], accepted_filing=True)
                                    episode.pop("latest_sec_gap", None)
                            except StopIteration as complete:
                                sec_step = acquisition_start = acquisition_scope = None
                                acquisition_receipts = []
                                if complete.value:
                                    episode["latest_sec_gap"] = complete.value
                                    raise StopEpisode("latest_filing_unavailable")
                                remember_latest_quarter(episode, recent[-1], accepted_filing=True)
                                episode.pop("latest_sec_gap", None)
                        guard()
                        fingerprint = digest(progress_content({"tool": name, "arguments": arguments, "result": result}))
                        progress = bool(result.get("ok")) and not read_gap and fingerprint not in episode["seen_results"]
                        if progress:
                            episode["seen_results"].append(fingerprint)
                        episode["no_progress"] = 0 if progress else episode["no_progress"] + 1
                        recoverable = read_gap or (name == "calculate" and not result.get("ok")
                            and result.get("data", {}).get("recoverable_input_error"))
                        if (self.model is not None and self.planner is None and not episode.get("draft_attempted")
                                and (recoverable or (recovery and result.get("ok") and not progress))):
                            instruction = ("已有原文未增加新证据。可从已读表格提取金额并保留期间和出处。"
                                if read_gap else "根据工具回执核对输入的指标、单位、符号及会计含义。")
                            instruction += ("只有能指出具体修正时才换一种操作；否则直接按原问起草，引用已有证据，"
                                            "标明未核实的部分。被拒绝的计算不能作为依据。")
                            episode["tool_recovery"] = {"tool":name, "arguments":arguments, "instruction":instruction,
                                "detail":read_gap or result.get("error") or result.get("summary")}
                            if (recovery or episode["tool_calls"] >= limits.max_tool_calls
                                    or episode["model_calls"] + 3 > limits.max_model_calls
                                    or episode["planner_steps"] >= limits.max_planner_steps
                                    or episode["no_progress"] >= limits.max_no_progress):
                                finalize_recovery = True
                                draft_after_tool = {"action":"research", "reason":instruction}
                            self.store.add_event(session_id, {"type":"controller_route", "episode_id":episode["id"],
                                "from":name, "to":"research" if finalize_recovery else "planner",
                                "reason":"bounded_tool_recovery"})
                        elif self.stop_on_tool_fault and (read_gap or
                            (not result.get("ok") and name in {"calculate", "analyze_disclosures"})
                            or (not progress and result.get("ok") and name in {"read_document", "search_documents"})
                        ):
                            raise StopEpisode("tool_fault_requires_inspection")
                        elif progress:
                            episode.pop("tool_recovery", None)
                            if recovery and (episode["model_calls"] + 2 >= limits.max_model_calls
                                    or episode["planner_steps"] >= limits.max_planner_steps):
                                finalize_recovery = True
                                draft_after_tool = {"action":"research", "reason":
                                    "修正后的工具回执已返回；按原问起草并引用有效记录，标明仍缺的证据。"}
                        if draft_after_tool is not None:
                            if (finalize_recovery and auto and self.model is not None and result.get("ok")
                                    and name != "calculate" and result.get("evidence")
                                    and episode["planner_steps"] < limits.max_planner_steps
                                    and episode["tool_calls"] < limits.max_tool_calls
                                    and episode["model_calls"] + 2 <= limits.max_model_calls):
                                from .margin_formula import draft_calculation
                                if operands := draft_calculation(self._context(session_id, episode)):
                                    # Spend the available tool slot while preserving both model slots for the review.
                                    pending_recovery_calculation = operands, draft_after_tool
                                    self.store.add_event(session_id, {"type":"controller_route","episode_id":episode["id"],
                                        "from":name,"to":"calculate","reason":"requested_margin_calculation_before_draft"})
                                    stage = "plan"
                                    checkpoint(stage)
                                    continue
                            if (name == "reconcile_net_income" and not episode.get("income_reason_attempted")
                                    and episode["tool_calls"] < limits.max_tool_calls
                                    and income_followup.reason_action(self._context(session_id, episode))):
                                # Carry the planner's draft instruction across the single targeted read.
                                pending_income_draft = draft_after_tool
                                stage = "plan"
                                checkpoint(stage)
                                continue
                            pending_income_draft = None
                            decision = draft_after_tool
                            stage = "research"
                            checkpoint(stage, decision=decision, draft_after_tool=draft_after_tool,
                                       finalize_recovery=finalize_recovery)
                        else:
                            stage = "plan"
                            checkpoint(stage)
                            continue
                if stage == "research":
                    if sec_step and sec_step["action"] == "research":
                        sec_step = acquisition_start = acquisition_scope = None
                        acquisition_receipts = []
                    if finalize_recovery and not self.store.get_session(session_id)["scope"].get("requirements"):
                        with self.store.atomic_step():
                            self._apply_scope(session_id, {"requirements":[
                                {"id":"request:" + episode["id"], "text":episode["request"]}]})
                            checkpoint("research", decision=decision, draft_after_tool=draft_after_tool,
                                       finalize_recovery=finalize_recovery)
                    if episode.get("reviewed") and limits.max_model_calls - episode["model_calls"] < 2:
                        # Preserve the last review when the next revision cannot be checked.
                        raise StopEpisode("model_budget_exhausted")
                    reserve = 0
                    if "critic" in role_seconds and self._current_review(session_id,episode,self._context(session_id,episode)):
                        reserve = role_seconds["critic"]
                        # Leave a full recent review and at least the shorter
                        # of the last two role calls for the drafting attempt.
                        remaining = limits.max_seconds - elapsed()
                        if remaining <= reserve + min(role_seconds.get("researcher",reserve),reserve):
                            self.store.add_event(session_id, {"type":"revision_skipped", "episode_id":episode["id"],
                                "remaining_seconds":remaining, "last_critic_seconds":reserve,
                                "last_researcher_seconds":role_seconds.get("researcher")})
                            raise StopEpisode("insufficient_time_for_reviewed_revision")
                    if repeated_redraft(episode, limits.max_no_progress):
                        raise StopEpisode("repeated_review_without_progress")
                    episode["draft_attempted"] = True
                    ctx = self._context(session_id, episode)
                    if not ctx.get("request_parts"):
                        ctx["request_parts"] = {"part-1":episode["request"]}
                        episode["inherited_parts"] = ctx["request_parts"]
                    instruction = decision.get("reason", "")
                    if ctx.get("issuer_attribution", {}).get("candidates"):
                        instruction = (instruction + "\nCurrent retrieval returned the issuer_attribution candidate "
                            "passages. Check the named metric, period, and causal language against the cited "
                            "original. Use applicable company explanations with their evidence IDs; a prior "
                            "search of one document cannot establish their absence. Separate inference.").strip()
                    if draft_after_tool is not None and ctx.get("income_bridge"):
                        # A real Planner may correctly say no arithmetic exists *at decision time*.
                        # The receipt just committed supersedes that premise for both roles.
                        instruction = (instruction + "\n原表核算回执更新：在上述决定之后，本地工具已返回本轮 income_bridge。"
                            "rows 中带 contribution_id 的变动已有正式计算记录；请交付能核对的各项贡献与净变动。"
                            "原先关于只有文本或没有计算的判断不适用于这些新记录。缺少的行仍须指出，不倒推填数。").strip()
                    if ctx.get("income_explanation"):
                        instruction = (instruction + "\n请按本轮 income_explanation 里的原文交付管理层解释；"
                            "区分该项目的直接解释、组成费用的背景和仍缺的原因。各行同比金额引用计算回执，"
                            "分配到具体原因的金额需要另外的量化披露。").strip()
                    if instruction:
                        ctx["draft_instruction"] = instruction
                    if ctx.get("repair_feedback"):
                        ctx["repair_feedback"]["current_draft_instruction"] = instruction
                    result = model_call("researcher", ctx, reserve_seconds=reserve,
                                        commit_result=lambda draft: self._save_research(session_id, episode, draft, ctx),
                                        on_return=lambda draft: checkpoint("critic", claims=draft.get("claims"),
                                            instruction=instruction, finalize_recovery=finalize_recovery))
                    claims = result.get("claims")
                    stage = "critic"
                elif stage == "critic":
                    claims, instruction, finalize_recovery = (
                        state[key] for key in ("claims", "instruction", "finalize_recovery"))

                if stage == "critic":
                    if episode.get("draft_reference_issues") and not episode["active_claim_ids"]:
                        raise StopEpisode("researcher_no_valid_references")
                    ctx = self._context(session_id, episode)
                    if self.candidate_callback and not episode.get("candidate_captured"):
                        # Capture only the initial candidate. Later drafts
                        # have already benefited from this episode's review.
                        reserve_attempt("capture", "candidate")
                        self.candidate_callback({**ctx, "capture_metadata": {
                            "model_mode": getattr(self.model, "mode", "configured_model"),
                            "model": getattr(self.model, "model", None)}})
                        episode["candidate_captured"] = True
                        checkpoint("critic", claims=claims, instruction=instruction, finalize_recovery=finalize_recovery)
                    display_notes = []
                    context_notes = []
                    rules = self._check_rules(ctx, display_notes=display_notes, context_notes=context_notes)
                    if display_notes:
                        ctx["display_review_notes"] = display_notes
                    if context_notes:
                        ctx["source_context_notes"] = context_notes
                    episode["issues"] = rules
                    ctx["rule_findings"] = rules
                    if instruction:
                        ctx["draft_instruction"] = instruction
                    def commit_review(review):
                        self._commit_review(session_id, episode, ctx, instruction, review)

                    def reviewed(_):
                        checkpoint("reviewed", claims=claims, rules=rules, finalize_recovery=finalize_recovery)

                    try:
                        model_call("critic", ctx, commit_result=commit_review, on_return=reviewed)
                    except (IncompleteRead, OutputContractError) as exc:
                        # Both failures share one retry on the already saved draft.
                        if episode.get("critic_receipt_retry_used"):
                            raise
                        if isinstance(exc, IncompleteRead):
                            retry_context = ctx
                            route = {"reason": "retry_interrupted_critic_response"}
                        else:
                            if exc.code not in {"invalid_critic_json", "unknown_issue_target"}:
                                raise
                            retry_context = {**ctx, "critic_receipt_repair": {
                                "previous_contract_error": exc.code,
                                "instruction": ("Return the full review for this unchanged draft as a single JSON object. "
                                                "Copy each issue target_id exactly from claims[].id or requirements[].id. "
                                                "Retain all substantive objections and review each current claim and requirement.")
                            }}
                            route = {"reason": "retry_invalid_critic_receipt", "contract_error": exc.code}
                        guard("model")
                        if not self._role_inputs_current(session_id, "critic", ctx, {"issues": []}):
                            raise StopEpisode("role_inputs_changed")
                        episode["critic_receipt_retry_used"] = True
                        self.store.add_event(session_id, {"type": "controller_route", "episode_id": episode["id"],
                            "from": "critic", "to": "critic", **route})
                        model_call("critic", retry_context, commit_result=commit_review, on_return=reviewed)
                    stage = "reviewed"
                elif stage == "reviewed":
                    claims, rules, finalize_recovery = (state[key] for key in ("claims", "rules", "finalize_recovery"))

                if stage == "reviewed":
                    pending_recovery = episode.pop("tool_recovery", None)
                    if finalize_recovery or pending_recovery:
                        # A reviewed partial answer ends this recovery path.
                        # Never reopen the same failed call after the review.
                        raise StopEpisode("completed" if self._complete(session_id, episode) else "unresolved_items")
                    if self.stop_on_tool_fault and any(i["issue_type"] in {
                            "numerical_inconsistency", "source_time_mismatch"} for i in rules):
                        # Preserve this review and the independently supported claims on delivery.
                        raise StopEpisode("deterministic_check_requires_inspection")
                    fingerprint = digest({"claims": claims, "issues": episode["issues"],
                                          "scope": self.store.get_session(session_id)["scope"]})
                    progress = fingerprint not in episode["seen_results"]
                    if progress:
                        episode["seen_results"].append(fingerprint)
                    episode["no_progress"] = 0 if progress else episode["no_progress"] + 1
                    bounded_answer = bool(episode.get("followup_limits"))
                    if self.planner is None and (bounded_answer or self.finish_after_review and not episode["issues"]):
                        guard()
                        if self._complete(session_id, episode, require_all_claims=True):
                            guard()
                            self.store.add_event(session_id, {"type":"controller_route","episode_id":episode["id"],
                                "from":"verification","to":"finish","reason":(
                                    "user_questions_verified_with_disclosed_limits" if bounded_answer
                                    else "all_claims_and_requirements_verified")})
                            raise StopEpisode("completed")
                    stage = "plan"
                    checkpoint(stage)
        except StopEpisode as exc:
            episode["stop_reason"] = self._resolve_stop(session_id, episode, str(exc))
        except Exception as exc:
            episode["stop_reason"] = "execution_error"
            episode["error_type"] = type(exc).__name__
            self.store.add_event(session_id, {"type": "episode_error", "episode_id": episode["id"], "error_type": type(exc).__name__})
        episode["active_seconds"] = elapsed()
        if not episode["reviewed"] and episode.get("draft_attempted"):
            self._restore_review(session_id, episode)
        episode["status"] = "complete" if episode["stop_reason"] == "completed" else "partial"
        episode["finished_at"] = utcnow()
        saved = self._save_delivery(session_id, episode)
        return {"status": episode["status"], "report": saved, "episode": episode}

    def _check_rules(self, ctx, **notes):
        companies = getattr(getattr(self.tools, "data", None), "manifest", {}).get("companies", [])
        return check_rules(ctx, companies=companies, **notes)

    def _reuse_document_window(self, sid, episode, context, arguments, elapsed):
        check_replay = getattr(self.tools, "replay_document_unchanged", None)
        if not callable(check_replay):
            return None
        reusable = planner_evidence.reusable_document_window(context, episode.get("recent_tool_results", []), arguments)
        if not reusable:
            return None
        prior, passage = reusable
        visible = planner_evidence.planner_read_context(self.model, context)
        if (not any(row["id"] == passage["id"] and row.get("text") == passage["text"] for row in visible["evidence"])
                or not check_replay(passage)):
            return None
        offset = prior["data"].get("next_offset")
        detail = f"本轮已读取同一冻结文档的字符 [{passage['start']}:{passage['end']}]，该窗口未附带可计算金额。"
        next_page = f"同一文档的下一页从 offset={offset} 开始。" if offset is not None else "此窗口已到文档末尾。"
        instruction = detail + next_page + "需要更多证据时选择不同窗口或其他适用工具，保留原文出处。"
        provenance = {"tool": "read_document", "arguments": arguments, "next_offset": offset,
                      "prior_observation_sha256": prior["observation_sha256"]}
        updates = {"tool_recovery": {**provenance, "detail": detail, "instruction": instruction},
                   "no_progress": episode["no_progress"] + 1, "active_seconds": elapsed()}
        with self.store.atomic_step():
            if not self.store.matches_role_inputs(sid, prior["scope_version"], [("evidence", passage)]):
                return None
            self.store.add_event(sid, {**provenance, "type": "tool_dispatch_skipped", "episode_id": episode["id"],
                "reason": "unchanged_frozen_document_window", "scope_version": prior["scope_version"],
                "record_versions": prior["record_versions"]})
            self._checkpoint(sid, {**episode, **updates})
        episode.update(updates)
        return instruction

    def _context(self, sid, episode, all_claims=False):
        from .context import as_of_context, select_history
        ctx = self.store.get_context(sid, report_mode="summaries")
        ctx["scope"] = ctx["session"]["scope"]
        ctx["requirements"] = ctx["scope"].get("requirements", [])
        if episode.get("resolve_followup_checklist"):
            ctx["scope"] = {**ctx["scope"],"requirements":[]}
            ctx["session"] = {**ctx["session"],"scope":ctx["scope"]}
            ctx["requirements"] = []
        parts = episode.get("clarified_parts", episode.get("inherited_parts"))
        if parts is None:
            parts = request_parts(episode["request"])
        if not parts and episode.get("new_followup"):
            parts = {"part-1": episode["request"]}
        if parts:
            ctx["request_parts"] = parts
        if episode.get("followup_focus"):
            ctx["followup_focus"] = episode["followup_focus"]
        if episode.get("margin_formula_target"):
            ctx["margin_formula_target"] = episode["margin_formula_target"]
        if episode.get("retry_period_bounds"):
            ctx["fiscal_target"] = episode.get("skill_fiscal_quarter") or episode["retry_period_bounds"]
        elif episode.get("quarter_followup_parts") and episode.get("skill_fiscal_quarter"):
            ctx["fiscal_target"] = episode["skill_fiscal_quarter"]
        if not all_claims:
            active = set(episode["active_claim_ids"])
            ctx["claims"] = [c for c in ctx["claims"] if c["id"] in active]
            # A newer draft may reuse the same claim ID. Read the immutable, reviewed version.
            versions = episode.get("restored_claim_versions", {}) if episode.get("reviewed") else {}
            for index, claim in enumerate(ctx["claims"]):
                version = versions.get(claim["id"])
                if version is not None and claim["version"] != version:
                    ctx["claims"][index] = next(c for c in self.store.history(sid, "claim", claim["id"]) if c["version"] == version)
        ctx["tools"] = self.tools.available_tools() if hasattr(self.tools, "available_tools") else self.tools.list_tools()
        if not self.auto_skills or self.planner is not None or getattr(self.model, "method_pack", None):
            ctx["tools"] = [t for t in ctx["tools"] if t["name"] != "analyze_disclosures"]
        if hasattr(self.tools, "evidence_access"):
            ctx["evidence_access"] = self.tools.evidence_access()
        # Selected old turns are verbatim excerpts, never a generated summary.
        selection = select_history(ctx["messages"], ctx["reports"], request=episode["request"], scope=ctx["scope"])
        ctx["history_omitted"] = selection["omitted"]
        ctx["history_selection"] = selection
        ctx["messages"] = [ctx["messages"][i] for i in selection["message_indices"]]
        # Check each archived version's own provenance before reducing it to the
        # user-visible summary. A source may have been updated since that report.
        ctx["reports"] = [self.store.get_report(sid, v) for v in selection["report_versions"]]
        ctx = as_of_context(ctx)
        from .context import current_tool_receipts, history_report_views, issuer_scoped_context, reopen_comparison_receipt
        ctx["reports"] = history_report_views(ctx)
        ctx["episode"] = {k: v for k, v in episode.items() if k not in {"seen_results", "revision_memory", "draft_reference_issues", "planner_added_requirements", "followup_limits", "market_window_history"}}
        companies = getattr(getattr(self.tools, "data", None), "manifest", {}).get("companies", [])
        if hasattr(self.tools, "searched_issuer_aliases"):
            labels = list(ctx["scope"].get("entities") or ())
            labels.extend(row.get("arguments", {}).get("entity")
                          for row in episode.get("recent_tool_results", ()) if isinstance(row, dict))
            aliases = self.tools.searched_issuer_aliases(sid, labels)
            if aliases:
                companies = [{**c, "aliases": [*c.get("aliases", ()),
                              *(name for name, ticker in aliases.items() if ticker == c["ticker"])]}
                             for c in companies]
        ctx = issuer_scoped_context(ctx, companies)
        policy = source_scope.source_constraint(episode["request"])
        catalog = self.tools.source_catalog(ctx["scope"]) if policy.publisher and hasattr(self.tools,"source_catalog") else ()
        ctx = source_scope.bound_context(ctx, policy=policy, catalog=catalog, companies=companies)
        if policy.excludes_sec and latest_quarter.mentions_latest_quarter(episode["request"]):
            ctx["source_document_scope"]["instruction"] += " " + source_scope.LATEST_COVERAGE_GAP
            ctx["source_document_scope"]["latest_coverage_gap"] = True
        reopen_comparison_receipt(ctx, self.store)
        ctx = current_tool_receipts(ctx)
        if found := issuer_attribution.evidence_context(ctx):
            ctx["issuer_attribution"] = found
        if current_income := income_followup.current_bridge(ctx):
            ctx["income_bridge"]=current_income
            if explanation := income_followup.explanation_context(ctx):
                ctx["income_explanation"] = {**explanation, **{k:current_income[k] for k in
                                             ("episode_id","scope_version","part_ids")}}
        if not income_followup.reason_action(ctx) or episode.get("income_reason_attempted"):
            ctx["tools"] = [t for t in ctx["tools"] if t["name"] != income_followup.EXPLANATION_TOOL]
        if self.model is not None and self.auto_skills and self.planner is None and not getattr(self.model, "method_pack", None):
            check = planner_evidence.margin_draft_check(ctx)
            if check:
                ctx["operating_margin_check"] = check
        ctx["issues"] = episode["issues"]
        memory = episode.get("revision_memory", {})
        if episode["issues"] and memory.get("open") and memory["scope_version"] == ctx["session"]["scope_version"]:
            since = episode["tool_calls"] - memory["tool_calls_at_review"]
            recent = ctx["episode"].get("recent_tool_results", [])
            ctx["repair_feedback"] = {
                "targets_and_consecutive_reviews_on_same_sources": memory["open"],
                "reviews_at_current_or_lower_question_coverage": memory.get("review_span", 1),
                "same_evidence_review_limit": episode["budget"].get("max_no_progress", Budget.max_no_progress),
                "previous_draft_instruction": memory["last_draft_instruction"],
                "tool_calls_since_review": since,
                "last_tool_in_this_turn": (
                    {k: recent[-1][k] for k in ("tool", "ok", "error", "evidence_ids", "calculation_ids", "record_status")
                     if k in recent[-1]}
                    if recent else None),
                "remaining_model_calls": episode["budget"]["max_model_calls"] - episode["model_calls"],
            }
            if any(tool.get("name") == "calculate" for tool in ctx["tools"]):
                ctx["repair_feedback"]["numeric_records_with_metric_and_unit"] = [
                    r["id"] for r in ctx["evidence"] + ctx["calculations"]
                    if not r.get("stale") and r.get("metric") and r.get("unit")
                    and type(r.get("value")) in (int, float)]
        return ctx

    def _historical_quarter(self, sid, issuer, scope, *, version=None, match_dates=True):
        """Resolve a prior answer by conversation position or by its dated, reviewed sources."""
        if scope.get("entities") != [issuer]:
            return None
        history = self.store.get_context(sid, report_mode="none")
        if version is not None and not 1 <= version <= history["report_count"]:
            return None
        records = {r["id"]: r for r in history["evidence"] + history["calculations"]}
        versions = [version] if version is not None else range(history["report_count"], 0, -1)
        bounds = {k:scope[k] for k in ("period_start", "period_end") if match_dates and scope.get(k)}
        candidate = None
        for number in versions:
            report = self.store.get_report(sid, number)
            quarter = fiscal_followup.reviewed_quarter(report, issuer, records, scope.get("cutoff"))
            if not quarter or any(quarter[k] != v for k, v in bounds.items()):
                continue
            if candidate and any(candidate[k] != quarter[k] for k in ("quarter", "period_start", "period_end")):
                return None
            candidate = quarter
        return candidate

    def _apply_scope(self, sid, decision, *, preserve_requirements=False, retrying=False):
        if "scope" not in decision and "requirements" not in decision:
            return False
        scope = dict(self.store.get_session(sid)["scope"])
        prior_requirements = {r["id"]: r["text"] for r in scope.get("requirements", [])}
        patch = decision.get("scope", {})
        if not isinstance(patch, dict):
            raise ValueError("Scope must be an object")
        scope.update(patch)
        if "requirements" in decision:
            scope["requirements"] = decision["requirements"]
        requirements = scope.get("requirements", [])
        if not isinstance(requirements, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] or not isinstance(r.get("text"), str) or not r["text"].strip() for r in requirements):
            raise ValueError("Requirements need nonempty id and text")
        if len({r["id"] for r in requirements}) != len(requirements):
            raise ValueError("Requirement IDs must be unique")
        next_requirements = {r["id"]: r["text"] for r in requirements}
        if preserve_requirements and retrying:
            # An unchanged user request can acquire a new item, but its original
            # obligations must still reach synthesis if the new plan omits one.
            retained = [{"id":key, "text":value} for key, value in prior_requirements.items()]
            retained.extend(r for r in requirements if r["id"] not in prior_requirements
                            and r["text"] not in prior_requirements.values())
            scope["requirements"] = retained
        elif preserve_requirements and any(next_requirements.get(key) != value for key, value in prior_requirements.items()):
            raise ValueError("Internal replanning cannot remove or rewrite required-answer items without a new user turn")
        if scope == self.store.get_session(sid)["scope"]:
            return False
        self.store.update_scope(sid, scope)
        return True

    def _save_research(self, sid, episode, result, context):
        from .margin_formula import reusable_reference

        if not isinstance(result, dict) or not isinstance(result.get("claims"), list):
            raise ValueError("Researcher must return claims")
        if "research_skills" in episode:
            # Bind reported methods to the actual draft, even if a later plan changes before delivery.
            episode["draft_skill_selection"] = {"skills":list(episode["research_skills"]),
                                               "source":episode.get("skill_selection_source", "not_specified")}
        # Invalidate the old adjudication before changing even the first record.
        episode.pop("followup_limits", None)
        episode.update(reviewed=False, request_coverage=[], draft_scope_version=self.store.get_session(sid)["scope_version"])
        for field in ("restored_claim_versions", "reviewed_claim_ids", "reviewed_requirement_ids", "reviewed_scope_version"):
            episode.pop(field, None)
        ids = []
        requirements = {r["id"] for r in self.store.get_session(sid)["scope"].get("requirements", [])}
        for key in ("answered_requirement_ids", "unanswered_requirement_ids"):
            values = result.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or v not in requirements for v in values):
                raise ValueError("Unknown requirement in researcher answer")
            episode[key] = values
        if set(episode["answered_requirement_ids"]) & set(episode["unanswered_requirement_ids"]):
            raise ValueError("An item cannot be both answered and unanswered")
        if set(episode["answered_requirement_ids"] + episode["unanswered_requirement_ids"]) != requirements:
            raise ValueError("Researcher must account for every required-answer item")
        for item in result["claims"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError("Each claim needs id and text")
            # Provenance versions, stale flags and status are controller/store
            # metadata, never authority delegated to a model response.
            claim = {key: item[key] for key in ("id", "text", "evidence_ids", "calculation_ids", "requirement_ids", "constraints", "asserted_value", "asserted_unit") if key in item}
            if claim["id"] in ids:
                raise ValueError("Duplicate claim ID")
            for key in ("evidence_ids", "calculation_ids", "requirement_ids"):
                if not isinstance(claim.get(key, []), list) or any(not isinstance(v, str) for v in claim.get(key, [])):
                    raise ValueError("Claim references must be lists of IDs")
                claim.setdefault(key, [])
            if not set(claim["requirement_ids"]).issubset(requirements):
                raise ValueError("Claim refers to unknown requirement")
            if calc := reusable_reference(claim, context):
                if self.store.matches_role_inputs(sid, episode["draft_scope_version"], [("calculation", calc)]):
                    claim["calculation_ids"] = [calc["id"]]
                    self.store.add_event(sid, {"type": "calculation_reference_bound", "episode_id": episode["id"],
                                              "claim_id": claim["id"], "calculation_id": calc["id"],
                                              "operand_ids": calc["operand_ids"]})
            claim.update(status="Pending Verification", scope_version=self.store.get_session(sid)["scope_version"])
            self.store.put_claim(sid, claim)
            ids.append(claim["id"])
        episode.pop("draft_reference_issues", None)
        quarantine = result.get("reference_quarantine")
        if quarantine:
            event = self.store.add_event(sid, {"type":"researcher_claims_quarantined", "episode_id":episode["id"],
                                               "scope_version":episode["draft_scope_version"], **quarantine})
            issues = []
            for row in quarantine["claims"]:
                claim = row["claim"]
                if "contract_error" in row:
                    issues.append({"target_id":row["diagnostic_target"], "issue_type":"invalid_record", "evidence_refs":[],
                                   "missing_support":f"第 {row['index']+1} 条草稿未送核验：{row['contract_error']}。",
                                   "proposed_action":"修正这一条的字段后再提交核验，现有合格主张可继续。",
                                   "contract_diagnostic":{"code":"invalid_researcher_claim", "field":f"claims[{row['index']}]",
                                                          "event_id":event["id"]}})
                    continue
                unknown = row["unknown_references"]
                fields = "；".join(field + " = " + ", ".join(refs) for field, refs in unknown.items())
                issues.append({"target_id":claim["id"], "issue_type":"missing_reference", "evidence_refs":[],
                               "missing_support":f"该条草稿未送核验，引用不在对应记录中（{fields}）。原主张：{claim['text']}",
                               "proposed_action":"查阅相应来源，使用实际留存的记录重新起草；证据不足的部分保留为缺口。",
                               "contract_diagnostic":{"code":"unknown_researcher_reference", "field":f"claims[{row['index']}]",
                                                      "reported_value":unknown, "event_id":event["id"]}})
            episode["draft_reference_issues"] = issues
        episode.update(active_claim_ids=ids, draft_saved=bool(ids) or not quarantine, reviewed=False,
                       issues=[], summary=str(result.get("summary", "")))

    @staticmethod
    def _dedupe_issues(issues):
        unique = {}
        for issue in issues:
            unique[digest(issue)] = issue
        return list(unique.values())

    def _role_inputs_current(self, sid, role, ctx, response):
        """Bind a returned draft or verdict to the evidence the caller actually supplied."""
        claims = ctx["claims"] if role == "critic" else response["claims"]
        refs = {ref for c in claims for field in ("evidence_ids", "calculation_ids") for ref in c.get(field, [])}
        records = []
        if role == "critic":
            records.extend(("claim", c) for c in claims)
            refs.update(ref for issue in response["issues"] for ref in issue.get("evidence_refs", []))
            refs.update(ref for verdict in response.get("claim_verdicts", []) for ref in verdict["evidence_refs"])
        for kind, rows in (("evidence", ctx["evidence"]), ("calculation", ctx["calculations"])):
            records.extend((kind, record) for record in rows if record["id"] in refs)
        return self.store.matches_role_inputs(sid, ctx["session"]["scope_version"], records)

    def _commit_review(self, sid, episode, ctx, instruction, review):
        parts = ctx.get("request_parts", {})
        from .request_review import check_period_coverage, grounded_review_rows
        from .answer_periods import restricted_latest_coverage
        verdicts = {v["claim_id"]:v for v in review.get("claim_verdicts", [])}
        rows = grounded_review_rows(parts,review.get("request_coverage", []),ctx["claims"],verdicts,episode.get("summary"))
        rows = check_period_coverage(parts, rows, ctx["claims"], ctx["rule_findings"], ctx["requirements"])
        if ctx.get("fiscal_target"):
            rows = fiscal_followup.check_answer_periods(rows, ctx["claims"], ctx["fiscal_target"], parts,
                                                       ctx["evidence"] + ctx["calculations"])
        from .request_review import check_comparison_coverage, reviewed_followup_limits
        from .margin_formula import check_coverage as check_formula_coverage
        rows, comparison_gap = check_comparison_coverage(ctx, rows)
        rows = check_formula_coverage(ctx, rows, verdicts)
        from .platform_coverage import check_coverage as check_platform_coverage
        companies = getattr(getattr(self.tools, "data", None), "manifest", {}).get("companies", [])
        rows = check_platform_coverage(ctx, rows, verdicts, companies)
        from .market_coverage import check_coverage as check_market_coverage
        rows = check_market_coverage(ctx, rows)
        unanswered_ids = set(episode["unanswered_requirement_ids"])
        gap = None
        lacks_latest = ctx.get("source_document_scope",{}).get("latest_coverage_gap")
        if not parts and lacks_latest:
            gap = source_scope.LATEST_COVERAGE_GAP
        elif not parts and ctx["requirements"] and all(r["id"] in unanswered_ids for r in ctx["requirements"]):
            gap = "本稿仍缺少以下要求的答案：" + "；".join(r["text"] for r in ctx["requirements"])
        elif not parts:
            period_defects = {i["target_id"]: i["missing_support"] for i in ctx["rule_findings"]
                              if i.get("scope") == ANSWER_PERIOD_CHECK}
            if ctx["claims"] and all(c["id"] in period_defects for c in ctx["claims"]):
                gap = next(iter(period_defects.values()))
            else:
                gap = comparison_gap or single_question_mismatch(episode["request"],ctx["claims"])
        if gap:
            # Retain the unresolved single question for redrafting and show any
            # separately supported background under the same rules as a compound turn.
            parts = {"part-1":episode["request"]}
            episode["clarified_parts" if "clarified_parts" in episode else "inherited_parts"] = parts
            rows = [{"part":"part-1","status":"gap","missing_support":gap}]
        if lacks_latest:
            rows = restricted_latest_coverage(parts,rows)
        coverage = coverage_issues(parts, {"request_coverage":rows}, ctx["claims"],
                                   requirements=ctx["requirements"],unanswered_ids=unanswered_ids)
        episode["request_coverage"] = rows
        episode["issues"] = self._dedupe_issues(ctx["rule_findings"] + review["issues"] + coverage
                                                + episode.get("draft_reference_issues", []))
        episode["reviewed_claim_ids"] = review["reviewed_claim_ids"]
        episode["reviewed_requirement_ids"] = review["reviewed_requirement_ids"]
        episode["reviewed_scope_version"] = ctx["session"]["scope_version"]
        episode.pop("followup_limits", None)
        if not (ctx["rule_findings"] or coverage or episode.get("draft_reference_issues")):
            limits = reviewed_followup_limits(ctx, episode, review, rows)
            if limits:
                episode["followup_limits"] = limits
        record_review(episode, ctx, instruction)
        rule_targets = {issue["target_id"] for issue in ctx["rule_findings"]
                        if issue.get("scope") != "question_coverage"}
        versions = self._set_claim_statuses(sid, episode, rule_targets, verdicts)
        fields = ("active_claim_ids", "answered_requirement_ids", "unanswered_requirement_ids",
                  "summary", "issues", "reviewed_claim_ids", "reviewed_requirement_ids", "reviewed_scope_version", "request_coverage")
        snapshot = {"claim_versions": versions, "scope_version": episode["reviewed_scope_version"],
                    "request": episode["request"], "sources": episode["revision_memory"]["sources"],
                    "fields": {k: episode[k] for k in (*fields, "draft_skill_selection", "draft_reference_issues", "followup_limits") if k in episode}}
        self.store.add_event(sid, {"type":"verification", "episode_id":episode["id"], "issues":episode["issues"],
                                  "reviewed_claim_ids":review["reviewed_claim_ids"], "reviewed_requirement_ids":review["reviewed_requirement_ids"],
                                  "review_snapshot":snapshot, "summary":f"Reviewed {len(versions)} claims; {len(episode['issues'])} issues remain."})
        episode["reviewed"] = True

    def _set_claim_statuses(self, sid, episode, rule_targets, verdicts):
        active = set(episode["active_claim_ids"])
        versions = {}
        # A missed task requirement leaves the answer open. It does not refute
        # a separately reviewed assertion such as the issuer's reported growth.
        claim_issues = [issue for issue in episode["issues"] if issue.get("scope") != "question_coverage"]
        for original in self.store.get_context(sid, report_mode="none")["claims"]:
            if original["id"] not in active:
                continue
            claim = dict(original)
            verdict = verdicts.get(claim["id"])
            problems = [i for i in claim_issues if i["target_id"] == claim["id"]]
            if claim.get("stale") or claim["id"] not in episode.get("reviewed_claim_ids", []):
                status = "Pending Verification"
            elif verdict is None or verdict["status"] == "Pending Verification":
                status = "Pending Verification"
            elif any("conflict" in i["issue_type"].lower() for i in problems):
                status = "Conflicting Evidence"
            elif problems and (claim["id"] in rule_targets or verdict["status"] == "Supported"):
                status = "Insufficient Evidence"
            else:
                status = verdict["status"]
            claim["status"] = status
            if verdict:
                claim["critic_verdict"] = verdict
            saved = self.store.put_claim(sid, claim)
            versions[claim["id"]] = saved["version"]
        return versions

    def _resolve_stop(self, sid, episode, reason):
        # A final Critic can consume the last model call. Decide from the
        # persisted review and coverage before reserving another Planner call.
        if reason in ("model_budget_exhausted","insufficient_time_for_reviewed_revision") and self._complete(sid, episode):
            return "completed"
        return reason

    def _complete(self, sid, episode, *, require_all_claims=False):
        limits = episode.get("followup_limits", [])
        if not episode["reviewed"] or (episode["issues"] and not limits):
            return False
        ctx = self._context(sid, episode)
        if episode.get("reviewed_scope_version") != ctx["session"]["scope_version"]:
            return False
        requirements = {r["id"] for r in ctx["requirements"]}
        if require_all_claims or limits:
            claim_ids = {c["id"] for c in ctx["claims"]}
            if (not episode.get("draft_attempted") or claim_ids != set(episode["active_claim_ids"])
                    or claim_ids != set(episode.get("reviewed_claim_ids", ()))
                    or requirements != set(episode.get("reviewed_requirement_ids", ()))
                    or any(c.get("status") != "Supported" or c.get("stale") for c in ctx["claims"])):
                return False
        covered = {r for c in ctx["claims"] if c.get("status") == "Supported" and not c.get("stale") for r in c.get("requirement_ids", [])}
        deferred = {rid for item in limits for rid in item["requirement_ids"]}
        return (bool(requirements and ctx["claims"]) and requirements <= covered
                and requirements <= set(episode["answered_requirement_ids"]) | deferred
                and requirements <= set(episode.get("reviewed_requirement_ids", []))
                and set(episode["unanswered_requirement_ids"]) <= deferred and not self._check_rules(ctx))

    def _current_review(self, sid, episode, ctx):
        if not episode.get("reviewed") or episode.get("reviewed_scope_version") != ctx["session"]["scope_version"]:
            return False
        last = self.store.latest_event(sid, "verification")
        snapshot = last.get("review_snapshot") if last and last.get("episode_id") == episode["id"] else None
        return bool(snapshot and snapshot.get("request") == episode["request"]
                    and snapshot["scope_version"] == ctx["session"]["scope_version"]
                    and snapshot["fields"].get("summary") == episode.get("summary")
                    and snapshot["claim_versions"] == {c["id"]:c["version"] for c in ctx["claims"]}
                    and snapshot["sources"] == review_sources(ctx))

    def _report(self, sid, episode):
        ctx = self._context(sid, episode)
        reviewed = self._current_review(sid, episode, ctx)
        if episode["reviewed"] and not reviewed:
            episode.update(reviewed=False, request_coverage=[])
            if episode["status"] == "complete":
                episode.update(status="partial", stop_reason="review_inputs_changed_before_delivery")
        ctx["claims"] = [{**c, "status": "Pending Verification"} if not reviewed or c.get("stale") else c for c in ctx["claims"]]
        from .display_numbers import display_check, DISPLAY_LABELS
        records = {r["id"]: r for r in ctx["evidence"] + ctx["calculations"]}
        for index, claim in enumerate(ctx["claims"]):
            refs = claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
            display = display_check(claim, [records[ref] for ref in refs if ref in records], record_index=records)
            if display:
                label = DISPLAY_LABELS[display["method"]]
                if display.get("negated_displays") and display["method"] == "matched_records":
                    label = "肯定陈述中的数值已与所引记录核对。否定表述的含义由独立审读判断。"
                ctx["claims"][index] = {**claim, "display_check":{**display, "label":label}}
        # Fresh rule findings must be visible even if budget/cancellation stopped a review.
        issues = self._dedupe_issues(episode["issues"] + self._check_rules(ctx) + episode.get("draft_reference_issues", []))
        period_targets = {i["target_id"] for i in issues if i.get("scope") == ANSWER_PERIOD_CHECK}
        if reviewed and period_targets:
            ctx["claims"] = [{**c, "status": "Insufficient Evidence"}
                             if c["id"] in period_targets and c.get("status") in ("Supported", "Partially Supported")
                             else c for c in ctx["claims"]]
            if episode["status"] == "complete":
                episode.update(status="partial", stop_reason="unresolved_items")
        supported = {r for c in ctx["claims"] if c.get("status") == "Supported" and not c.get("stale") and reviewed for r in c.get("requirement_ids", [])}
        unanswered = [r for r in ctx["requirements"]
                      if r["id"] not in supported or r["id"] in episode["unanswered_requirement_ids"]]
        summary_kind = "unverified_researcher_draft" if episode["summary"] else "see_report_findings"
        report = {"episode_id": episode["id"], "request": episode["request"], "status": episode["status"], "stop_reason": episode["stop_reason"], "scope": ctx["scope"], "scope_version": ctx["session"]["scope_version"], "summary": episode["summary"] or "No verified answer is available yet.", "summary_kind": summary_kind, "model_mode": getattr(self.model, "mode", "configured_model"), "claims": ctx["claims"], "evidence": ctx["evidence"], "calculations": ctx["calculations"], "issues": issues, "unanswered": unanswered, "verification_available": reviewed, "usage": {"model_calls": episode["model_calls"], "tool_calls": episode["tool_calls"], "planner_steps": episode.get("planner_steps", 0), "active_seconds": episode["active_seconds"], **episode["usage"]}, "created_at": utcnow()}
        if episode.get("request_id"):
            report["request_id"] = episode["request_id"]
        if episode["stop_reason"] == "execution_error" and episode.get("codex_failure"):
            report["codex_failure"] = episode["codex_failure"]
        if episode["stop_reason"] == "execution_error" and episode.get("researcher_output_error"):
            report["researcher_output_error"] = episode["researcher_output_error"]
        if episode["stop_reason"] == "execution_error" and episode.get("planner_error"):
            report["planner_error"] = episode["planner_error"]
        if episode.get("latest_sec_gap"):
            report["skill_gap"] = episode["latest_sec_gap"]
            if not episode["draft_saved"]:
                report["summary"] = episode["latest_sec_gap"]
        if ctx.get("source_document_scope",{}).get("latest_coverage_gap"):
            report["source_coverage_gap"] = source_scope.LATEST_COVERAGE_GAP
        if "research_skills" in episode:
            draft_methods = episode.get("draft_skill_selection", {})
            report["research_skills"] = draft_methods.get("skills", episode["research_skills"])
            report["skill_selection_source"] = draft_methods.get("source", episode.get("skill_selection_source", "not_specified"))
        if episode.get("issuer_conflict"):
            report["issuer_conflict"] = episode["issuer_conflict"]
        if episode["stop_reason"] == "local_context_limit":
            report["context_limit"] = episode["context_limit"]
        if "retry_of_version" in episode:
            report["retry_of_version"] = episode["retry_of_version"]
        if "skill_assessments" in episode:
            report["skill_assessments"] = episode["skill_assessments"]
        if episode["stop_reason"] == "tool_fault_requires_inspection" and episode.get("recent_tool_results"):
            last = episode["recent_tool_results"][-1]
            reason = last.get("error") or "The repeated tool call returned no new evidence."
            report["tool_fault"] = {"tool": last["tool"], "reason": reason}
        if episode["stop_reason"] in {"repeated_retrieval_without_new_evidence", "no_progress",
                                      "tool_budget_exhausted", "model_budget_exhausted", "execution_error"}:
            recent = ctx["episode"].get("recent_tool_results", [])
            if recent:
                last = recent[-1]
                gap = last.get("data", {}).get("acquisition_gap")
                if not gap and last.get("ok") is False:
                    gap = last.get("error") or last.get("summary")
                if gap:
                    report["tool_fault"] = {"tool": last["tool"], "reason": gap}
        if report["status"] == "partial":
            from .market import market_window_gap
            price_history = episode.get("market_window_history", {})
            receipts = (price_history["receipts"] if price_history.get("scope_version") == ctx["session"]["scope_version"]
                        else ctx["episode"].get("recent_tool_results", []))
            if gap := market_window_gap(receipts, ctx["scope"], ctx["evidence"]):
                report["tool_fault"] = gap
        if ctx.get("request_parts"):
            report["request_parts"] = ctx["request_parts"]
            report["request_coverage"] = report_coverage(ctx["request_parts"], episode.get("request_coverage", []),
                                                       ctx["claims"], reviewed=reviewed,requirements=ctx["requirements"],
                                                       unanswered_ids=episode["unanswered_requirement_ids"], findings=issues)
            report["unanswered"] = unresolved_checklist(ctx["requirements"],unanswered,ctx["request_parts"],report["request_coverage"])
            from .reporting import background_claim_ids
            background = background_claim_ids(report)
            if background:
                report["background_claim_ids"] = background
        if reviewed and report["status"] == "complete" and episode.get("followup_limits"):
            # The immutable review and this audit field retain the original objections.
            # A later user question can promote one of the disclosed limits to a new task.
            report["followup_limits"] = episode["followup_limits"]
            report["checklist_observations"] = report["issues"]
            report["issues"] = []
            report["unanswered"] = []
        if reviewed and episode.get("restored_claim_versions"):
            # The database may contain a later, unreviewed draft of the same claim.
            report["record_versions"] = {kind: {r["id"]: r["version"] for r in ctx[group]}
                                         for kind, group in (("claim", "claims"), ("evidence", "evidence"), ("calculation", "calculations"))}
        # Save reviewed fiscal context alongside the verdict; editing scope here
        # would invalidate the very review that established this quarter.
        entity = fiscal_followup.report_issuer(report) if reviewed else None
        bounds = episode.get("retry_period_bounds", {})
        if entity and not (bounds and (episode.get("quarter_followup_parts") or bounds.get("conflicting_quarter"))):
            confirmed = fiscal_followup.reviewed_quarter(report, entity, records, ctx["scope"].get("cutoff"))
            if confirmed:
                episode["skill_fiscal_quarter"] = confirmed
        if "skill_fiscal_quarter" in episode:
            report["skill_fiscal_quarter"] = episode["skill_fiscal_quarter"]
        if episode.get("followup_focus"):
            report["followup_focus"] = episode["followup_focus"]
        if episode.get("margin_formula_target"):
            report["margin_formula_target"] = episode["margin_formula_target"]
        if episode.get("quarter_followup_parts"):
            report["quarter_followup_parts"] = episode["quarter_followup_parts"]
        if bounds:
            report["retry_period_bounds"] = bounds
        if "offline_part_findings" in episode:
            report["offline_part_findings"] = episode["offline_part_findings"]
        if lookup := episode.get("archived_source"):
            latest = {c["id"]: c for c in self.store.get_context(sid, report_mode="none")["claims"]}
            claim_versions = lookup.get("claim_versions", {})
            if (claim_versions and all(source_link_followup.current_claim_version(latest.get(ref), version)
                                       for ref, version in claim_versions.items())
                    and all(ref in records and not records[ref].get("stale") and records[ref]["version"] == version
                            for ref, version in lookup["proof_versions"].items())):
                report["archived_source"] = lookup
            else:
                original = report["offline_part_findings"]["part-1"]
                report["offline_part_findings"] = {"part-1": {"status": "gap",
                    "missing_support": source_link_followup.ARCHIVE_GAP, "quarter": original["quarter"]}}
        if "income_bridge" in ctx:
            report["income_bridge"]=ctx["income_bridge"]
            if "income_explanation" in ctx:
                report["income_explanation"] = ctx["income_explanation"]
            if episode.get("model_mode", "offline_tools_only" if self.model is None else "configured_model") == "offline_tools_only":
                report.setdefault("offline_part_findings",{}).update(income_followup.offline_answers(report))
        report["model_mode"] = episode.get("model_mode", report["model_mode"])
        if report["model_mode"] == "offline_tools_only" or (self.model is None and "model_mode" not in episode):
            from .skill_calculations import evidence_notes
            report["model_mode"] = "offline_tools_only"
            report["skill_notes"] = evidence_notes(episode, ctx)
            if episode.get("skill_gap"):
                report["skill_gap"] = episode["skill_gap"]
            if report.get("offline_part_findings") and not report["claims"]:
                from .reporting import report_view
                view = report_view(report)
                report["unanswered"], report["issues"] = view["unanswered"], view["issues"]
                answers = []
                for row in view.get("question_answers", []):
                    if row["status"] in {"calculated", "source_excerpt", "partial", "gap"}:
                        answers.extend(text for key in ("answer_sentence", "missing_support") if (text := row.get(key)))
                if answers:
                    report.update(summary=" ".join(dict.fromkeys(answers)), summary_kind="offline_source_results")
        if "draft_saved" in episode:
            report["delivery_stage"] = "draft_unverified" if episode["draft_saved"] else "no_draft"
        return self.store.report_with_stage(sid, report)
