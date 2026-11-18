"""Condition-blind, isolated, budgeted candidate revision for E1 evaluation.

No Critic, generic review or additional rule review is scheduled here. Every
output remains a candidate for independent assessment, even if the planner stops.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import tempfile
import time

from .evaluation import (EvaluationError, FrozenCase, _canonical, _hash, _settings,
                         _model_call_event, _finish_model_event, _served_model_consistency)
from .model import USAGE_FIELDS, validate_response
from .storage import SessionStore


@dataclass(frozen=True)
class RevisionBudget:
    max_model_calls: int = 10
    max_tool_calls: int = 8
    max_seconds: float = 180
    max_no_progress: int = 3
    max_planner_steps: int = 64

    def __post_init__(self):
        for key in ("max_model_calls", "max_tool_calls", "max_no_progress", "max_planner_steps"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise EvaluationError(key + " must be a positive integer")
        if (isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float))
                or not math.isfinite(self.max_seconds) or self.max_seconds <= 0):
            raise EvaluationError("max_seconds must be a positive finite number")


class _Stop(Exception):
    pass


def _copy(value):
    return json.loads(_canonical(value))


def _without_storage_metadata(record):
    return {k: v for k, v in record.items() if k not in {"version", "created_at", "updated_at", "invalidation_reasons"}}


def _mismatch(record, field, originals):
    return any(key not in originals or originals[key].get("version", 1) != version
               for key, version in record.get(field, {}).items())


def _import_context(store, sid, original):
    """Remap storage versions, preserving invalidity instead of healing it."""
    scope = _copy(original["scope"])
    scope["requirements"] = _copy(original["requirements"])
    session = store.update_scope(sid, scope)
    current_scope_version = session["scope_version"]
    original_evidence = {r["id"]: r for r in original["evidence"]}
    original_calculations = {r["id"]: r for r in original["calculations"]}
    originals = {**original_evidence, **original_calculations}
    mappings = {"scope": {"original": original.get("session", {}).get("scope_version"), "working": current_scope_version},
                "evidence": {}, "calculations": {}, "claims": {}}
    for record in sorted(original["evidence"], key=lambda r: (r.get("source_observed_at") or "", r["id"])):
        imported = store.put_evidence(sid, _without_storage_metadata(record))
        mappings["evidence"][record["id"]] = {"original_version": record.get("version", 1),
                                               "working_version": imported["version"], "input_sha256": _hash(record)}
    pending = dict(original_calculations)
    blocked = {}
    while pending:
        current = store.get_context(sid)
        available = {r["id"]: r for r in current["evidence"] + current["calculations"]}
        progressed = False
        for key, record in list(pending.items()):
            operands = record.get("operand_ids", [])
            if any(i in pending for i in operands):
                continue
            payload = _without_storage_metadata(record)
            payload["scope_version"] = current_scope_version
            payload["stale"] = bool(payload.get("stale") or _mismatch(record, "operand_versions", originals))
            payload.pop("operand_versions", None)
            if not operands or any(i not in available or available[i].get("stale") for i in operands):
                payload.update(stale=True, import_blocked="missing_or_stale_operand")
                blocked[key] = payload
                imported = None
            else:
                imported = store.put_calculation(sid, payload)
                available[key] = imported
            mappings["calculations"][key] = {"original_version": record.get("version", 1),
                                             "working_version": imported["version"] if imported else None,
                                             "input_sha256": _hash(record), "import_blocked": imported is None}
            del pending[key]
            progressed = True
        if not progressed:
            for key, record in pending.items():
                blocked[key] = {**_without_storage_metadata(record), "stale": True,
                                "scope_version": current_scope_version, "import_blocked": "cyclic_dependency"}
                mappings["calculations"][key] = {"original_version": record.get("version", 1), "working_version": None,
                                                 "input_sha256": _hash(record), "import_blocked": True}
            break
    for record in original["claims"]:
        payload = _without_storage_metadata(record)
        payload.update(status="Pending Verification", scope_version=current_scope_version,
                       stale=bool(record.get("stale") or _mismatch(record, "evidence_versions", original_evidence)
                                  or _mismatch(record, "calculation_versions", original_calculations)
                                  or (record.get("scope_version") is not None
                                      and original.get("session", {}).get("scope_version") is not None
                                      and record["scope_version"] != original["session"]["scope_version"])))
        payload.pop("evidence_versions", None)
        payload.pop("calculation_versions", None)
        imported = store.put_claim(sid, payload)
        mappings["claims"][record["id"]] = {"original_version": record.get("version", 1),
                                             "working_version": imported["version"], "input_sha256": _hash(record)}
    return mappings, blocked


def _progress_value(value):
    if isinstance(value, dict):
        return {k: _progress_value(v) for k, v in value.items()
                if k not in {"retrieved_at", "source_observed_at", "network_requests", "retrieval_score", "created_at", "updated_at",
                             "ranking_latency_ms", "runtime_encoding"}}
    if isinstance(value, list):
        return [_progress_value(v) for v in value]
    return value


class CommonRevisionExecutor:
    def __init__(self, model, tools_factory, *, clock=time.monotonic, cancel_check=None):
        self.model = model
        self.tools_factory = tools_factory
        self.clock = clock
        self.cancel_check = cancel_check or (lambda: False)

    def revise(self, context: dict, feedback: dict, *, budget: dict) -> dict:
        """Create an independent working copy, revise, then discard its SQLite DB.

        tools_factory(store) must return a fresh registry. Its immutable source
        cache should live outside the temporary DB directory when live evidence
        must remain inspectable after this method returns.
        """
        if not isinstance(budget, dict):
            raise EvaluationError("revision budget must be an object")
        try:
            limits = RevisionBudget(**budget)
        except TypeError:
            raise EvaluationError("Unknown revision budget field") from None
        if not isinstance(feedback, dict):
            raise EvaluationError("revision feedback must be an object")
        frozen = FrozenCase.from_context("revision-input", context)
        original = frozen.context()
        if not original["requirements"]:
            raise EvaluationError("Revision requires a nonempty fixed required-answer checklist")
        feedback = _copy(feedback)
        baseline_settings = _settings(self.model)
        started = self.clock()
        usage = {"model_calls": 0, "tool_calls": 0, "network_requests": 0, "planner_steps": 0,
                 "prompt_tokens_reported_calls": 0, "completion_tokens_reported_calls": 0,
                 "total_tokens_reported_calls": 0}
        events = []
        mappings = {}
        state = {"active_claim_ids": [c["id"] for c in original["claims"]],
                 "answered_requirement_ids": [], "unanswered_requirement_ids": [],
                 "summary": "", "last_tool_result": None, "no_progress": 0}
        seen = set()
        reason, error_type = "execution_error", None
        fixed_scope = {**original["scope"], "requirements": original["requirements"]}
        fixed_requirements = _copy(original["requirements"])

        def elapsed():
            return max(0.0, self.clock() - started)

        def guard(kind=None):
            if self.cancel_check():
                raise _Stop("cancelled")
            if elapsed() >= limits.max_seconds:
                raise _Stop("time_budget_exhausted")
            if kind == "model" and usage["model_calls"] >= limits.max_model_calls:
                raise _Stop("model_budget_exhausted")
            if kind == "tool" and usage["tool_calls"] >= limits.max_tool_calls:
                raise _Stop("tool_budget_exhausted")
            if kind == "planner" and usage["planner_steps"] >= limits.max_planner_steps:
                raise _Stop("planner_budget_exhausted")

        with tempfile.TemporaryDirectory(prefix="verifin-e1-revision-") as directory:
            store = SessionStore(Path(directory) / "working.sqlite3")
            try:
                sid = store.create_session("Isolated E1 candidate revision")["id"]
                mappings, blocked = _import_context(store, sid, original)
                tools = self.tools_factory(store)
                tool_specs = _copy(tools.list_tools())
                if original.get("tools") is not None and original["tools"] != tool_specs:
                    raise EvaluationError("Working tool registry differs from the frozen case tool interface")

                def working_context():
                    saved = store.get_context(sid)
                    active = set(state["active_claim_ids"])
                    calculations = {r["id"]: r for r in saved["calculations"]}
                    calculations = {**blocked, **calculations}
                    # Stable logical session metadata avoids random SQLite IDs
                    # and import timestamps becoming experimental differences.
                    return {"phase": "evaluation_revision", "request": original.get("request", ""),
                            "session": {"id": "evaluation-workspace", "scope": _copy(fixed_scope),
                                        "scope_version": saved["session"]["scope_version"]},
                            "scope": _copy(fixed_scope), "requirements": _copy(fixed_requirements),
                            "messages": [{"role": m.get("role", "user"), "content": m.get("content", "")}
                                         for m in original.get("messages", [])],
                            "evidence": [_without_volatile(r) for r in saved["evidence"]],
                            "calculations": [_without_volatile(r) for r in calculations.values()],
                            "claims": [_without_volatile(r) for r in saved["claims"] if r["id"] in active],
                            "tools": tool_specs, "revision_feedback": feedback,
                            "revision_state": _copy(state), "remaining_allowance": {
                                "model_calls": limits.max_model_calls - usage["model_calls"],
                                "tool_calls": limits.max_tool_calls - usage["tool_calls"]}}

                def model_call(role):
                    guard("model")
                    if _settings(self.model) != baseline_settings:
                        raise EvaluationError("Model settings changed during candidate revision")
                    usage["model_calls"] += 1
                    old_timeout = getattr(self.model, "timeout", None)
                    if old_timeout is not None:
                        self.model.timeout = max(0.001, min(old_timeout, limits.max_seconds - elapsed()))
                    call_started = self.clock()
                    event = _model_call_event(self.model, role)
                    model_context = working_context()
                    try:
                        self.model.last_usage = {}
                        if hasattr(self.model, "last_response_metadata"):
                            self.model.last_response_metadata = {}
                        response = validate_response(role, self.model.complete(role, model_context), model_context)
                        event["ok"] = True
                    except Exception as exc:
                        event["error_type"] = type(exc).__name__
                        raise
                    finally:
                        if old_timeout is not None:
                            self.model.timeout = old_timeout
                        _finish_model_event(event, self.model, self.clock() - call_started)
                        events.append(event)
                        for key, value in (getattr(self.model, "last_usage", {}) or {}).items():
                            if key in USAGE_FIELDS and type(value) is int and value >= 0:
                                usage[key] = usage.get(key, 0) + value
                                usage[key + "_reported_calls"] = usage.get(key + "_reported_calls", 0) + 1
                    if _settings(self.model) != baseline_settings:
                        raise EvaluationError("Model settings changed during candidate revision")
                    guard()
                    return response

                def record_progress(value, successful=True):
                    digest = _hash(_progress_value(value))
                    progress = successful and digest not in seen
                    state["no_progress"] = 0 if progress else state["no_progress"] + 1
                    if progress:
                        seen.add(digest)

                try:
                    while True:
                        guard()
                        if state["no_progress"] >= limits.max_no_progress:
                            raise _Stop("no_progress")
                        guard("model")
                        guard("planner")
                        usage["planner_steps"] += 1
                        decision = model_call("revision_planner")
                        patch = decision.get("scope", {})
                        if any(fixed_scope.get(k) != v for k, v in patch.items()):
                            raise _Stop("scope_change_refused")
                        if "requirements" in decision and decision["requirements"] != fixed_requirements:
                            raise _Stop("requirements_change_refused")
                        action = decision["action"]
                        if action == "finish":
                            raise _Stop("planner_finished")
                        if action == "clarify":
                            state["question"] = decision["question"]
                            raise _Stop("clarification_required")
                        if action == "tool":
                            guard("tool")
                            usage["tool_calls"] += 1
                            old_timeout = getattr(tools, "timeout", None)
                            if old_timeout is not None:
                                tools.timeout = max(0.001, min(old_timeout, limits.max_seconds - elapsed()))
                            call_started = self.clock()
                            try:
                                result = tools.execute(decision["tool"], decision["arguments"], sid, _copy(fixed_scope))
                            except Exception as exc:
                                result = {"ok": False, "error_type": type(exc).__name__, "summary": "Tool execution failed",
                                          "evidence": [], "calculations": [], "data": {}}
                            finally:
                                if old_timeout is not None:
                                    tools.timeout = old_timeout
                            state["last_tool_result"] = _copy(result)
                            network_calls = result.get("data", {}).get("network_requests", 0)
                            if type(network_calls) is int and network_calls >= 0:
                                usage["network_requests"] += network_calls
                            events.append({"type": "tool_call", "tool": decision["tool"], "arguments": decision["arguments"],
                                           "ok": bool(result.get("ok")), "seconds": max(0.0, self.clock() - call_started),
                                           "network_requests": network_calls, "summary": result.get("summary", "")})
                            observation = result.get("data", {}).get("source_observation")
                            if observation is not None:
                                store.observe_document_source(sid, observation)
                            if result.get("ok"):
                                for row in result.get("evidence", []):
                                    store.put_evidence(sid, row)
                                for row in result.get("calculations", []):
                                    store.put_calculation(sid, row)
                                    blocked.pop(row["id"], None)
                            guard()
                            record_progress({"tool": decision["tool"], "arguments": decision["arguments"], "result": result}, bool(result.get("ok")))
                        elif action == "research":
                            research = model_call("researcher")
                            active = []
                            for row in research["claims"]:
                                payload = {k: v for k, v in row.items() if k in {
                                    "id", "text", "evidence_ids", "calculation_ids", "requirement_ids",
                                    "constraints", "asserted_value", "asserted_unit"}}
                                payload.update(status="Pending Verification", scope_version=store.get_session(sid)["scope_version"])
                                store.put_claim(sid, payload)
                                active.append(row["id"])
                            state.update(active_claim_ids=active, answered_requirement_ids=research["answered_requirement_ids"],
                                         unanswered_requirement_ids=research["unanswered_requirement_ids"], summary=research["summary"])
                            record_progress(research)
                except _Stop as exc:
                    reason = str(exc)
                except Exception as exc:
                    reason, error_type = "execution_error", type(exc).__name__
                    events.append({"type": "revision_error", "error_type": error_type})
                final = working_context()
                final.pop("revision_feedback", None)
                final.pop("remaining_allowance", None)
                for claim in final["claims"]:
                    claim["status"] = "Pending Verification"
                linked = {r for claim in final["claims"] if not claim.get("stale") for r in claim.get("requirement_ids", [])}
                unanswered = [r for r in fixed_requirements if r["id"] not in linked or r["id"] in state["unanswered_requirement_ids"]]
                usage["active_seconds"] = elapsed()
                result = {"status": "candidate" if reason == "planner_finished" and not unanswered else "partial",
                          "stop_reason": reason, "verification_available": False, "truth_scored": False,
                          "coverage_kind": "syntactic_only_not_semantic_completeness", "context": final,
                          "claims": final["claims"], "unanswered": unanswered, "usage": usage, "events": events,
                          "served_model_consistency": _served_model_consistency(events),
                          "budget": asdict(limits), "version_mapping": mappings, "input_sha256": frozen.context_sha256,
                          "feedback_sha256": _hash(feedback), "settings": baseline_settings,
                          "tools_sha256": _hash(tool_specs), "model_mode": getattr(self.model, "mode", "configured_model"),
                          "workspace_disposed": True}
                if error_type:
                    result["error_type"] = error_type
                return result
            finally:
                store.close()


def _without_volatile(record):
    return {k: v for k, v in record.items() if k not in {"created_at", "updated_at"}}
