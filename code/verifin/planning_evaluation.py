"""E2 paired planning execution over a frozen public conversation and sources.

Recorded protocol diagnostics are not answer-quality scores. Completion badges,
failed calls and repeated calls do not establish correctness, planning mistakes,
or effectiveness. Independent assessment can inspect the saved candidates and
the original source snapshots without a gold action sequence entering either arm.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from .data import SnapshotData, cutoff_time
from .engine import Budget, ResearchEngine
from .evaluation import EvaluationError, _canonical, _hash, _settings
from .model import COMMON_PROMPT, ROLE_PROMPTS, ModelError, validate_response
from .planning import FixedWorkflowPlanner, TaskSpec, TaskSpecError, WORKFLOW_VERSION, _no_private_fields
from .storage import SessionStore
from .tools import ToolRegistry


PROTOCOL_VERSION = "e2-paired-planning-v1"
CONDITIONS = ("adaptive", "fixed")


def _copy(value):
    return json.loads(_canonical(value))


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(label + " must be a nonempty string")
    return value


class _PublicTaskModel:
    """Same model adapter in both arms; do not let a plan weaken public inputs.

    The underlying call is still made/countable before checking its response.
    Canonical issuer identifiers and source-resolved dates can be added to scope;
    the user-resolved requirements, cutoff and task description remain fixed.
    """
    def __init__(self, model):
        self._model = model

    def __getattr__(self, name):
        return getattr(self._model, name)

    @property
    def timeout(self):
        return self._model.timeout

    @timeout.setter
    def timeout(self, value):
        self._model.timeout = value

    @property
    def last_usage(self):
        return self._model.last_usage

    @last_usage.setter
    def last_usage(self, value):
        self._model.last_usage = value

    @property
    def last_response_metadata(self):
        return getattr(self._model, "last_response_metadata", {})

    @last_response_metadata.setter
    def last_response_metadata(self, value):
        self._model.last_response_metadata = value

    def complete(self, role, context):
        response = self._model.complete(role, context)
        if role != "planner" or not isinstance(response, dict):
            return response
        scope = context.get("scope", {})
        task = scope.get("planning_task")
        if task is None:
            return response
        patch = response.get("scope", {})
        if not isinstance(patch, dict):
            return response  # Common response validator reports its bad shape.
        for requirements in (response.get("requirements"), patch.get("requirements")):
            if requirements is None:
                continue
            if not isinstance(requirements, list) or any(not isinstance(r, dict) for r in requirements):
                raise ModelError("Public task requirements must remain intact")
            mapped = {r.get("id"): r.get("text") for r in requirements}
            if any(mapped.get(r["id"]) != r["text"] for r in scope.get("requirements", [])):
                raise ModelError("Planning cannot remove or rewrite the shared public required-answer items")
        if "cutoff" in patch and cutoff_time(patch["cutoff"]) != cutoff_time(task["cutoff"]):
            raise ModelError("Planning cannot change the shared public information cutoff")
        if "planning_task" in patch and patch["planning_task"] != task:
            raise ModelError("Planning cannot rewrite the shared public task specification")
        return response


@dataclass(frozen=True)
class FrozenPlanningCase:
    case_id: str
    context_sha256: str
    _json: str

    @classmethod
    def from_dict(cls, value):
        value = _copy(value)
        try:
            _no_private_fields(value)
        except TaskSpecError as exc:
            raise EvaluationError(str(exc)) from None
        if not isinstance(value, dict) or set(value) - {"case_id", "history", "turns"}:
            raise EvaluationError("Planning cases contain only case_id, public history and user turns")
        case_id = _text(value.get("case_id"), "case_id")
        history = value.get("history", [])
        if not isinstance(history, list) or len(history) > 100:
            raise EvaluationError("Public history must contain at most 100 messages")
        for message in history:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise EvaluationError("History messages contain role and content only")
            if message["role"] not in {"user", "assistant"}:
                raise EvaluationError("Reviewer, system and private tool histories are not public prehistory")
            _text(message["content"], "history.content")
        turns = value.get("turns")
        if not isinstance(turns, list) or not 1 <= len(turns) <= 20:
            raise EvaluationError("A planning case needs one to twenty user turns")
        for turn in turns:
            if not isinstance(turn, dict) or set(turn) != {"message", "task"}:
                raise EvaluationError("Each user turn contains message and its public resolved task (or null)")
            _text(turn["message"], "turn.message")
            if turn["task"] is not None:
                try:
                    spec = TaskSpec.from_dict(turn["task"])
                except TaskSpecError as exc:
                    raise EvaluationError(str(exc)) from None
                if spec.to_dict()["source"] != "replay":
                    raise EvaluationError("Paired planning requires frozen replay sources; capture live sources before comparing")
                turn["task"] = spec.to_dict()
        context = {"history": history, "turns": turns}
        encoded = _canonical(context)
        return cls(case_id, _hash(context), encoded)

    def context(self):
        result = json.loads(self._json)
        if _hash(result) != self.context_sha256:
            raise EvaluationError("Planning case content/hash mismatch")
        return result

    def to_dict(self):
        return {"case_id": self.case_id, **self.context()}


def _save(path, value):
    """Exclusive output creation prevents accidental overwriting of old runs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _freeze_sources(data_dir, destination):
    source = SnapshotData(data_dir, allow_network=False)
    manifest = _copy(source.manifest)
    if not manifest.get("files"):
        raise EvaluationError("Paired planning requires an explicit nonempty source provenance manifest")
    destination = Path(destination)
    destination.mkdir()
    # Copy only manifest-registered, integrity-checked source files. Unregistered
    # local cache entries and arbitrary files cannot become a private advantage.
    for name in sorted(manifest["files"]):
        raw = source.raw(name)
        target = (destination / name).resolve()
        if not target.is_relative_to(destination.resolve()) or target == destination.resolve() / "manifest.json":
            raise EvaluationError("Source manifest contains an unsafe or reserved file path")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(raw)
    _save(destination / "manifest.json", manifest)
    return _hash(manifest)


def _validate_sources(data_dir, expected_hash):
    source = SnapshotData(data_dir, allow_network=False)
    if _hash(source.manifest) != expected_hash:
        raise EvaluationError("Frozen source manifest changed during paired execution")
    for name in source.manifest["files"]:
        source.raw(name)


def _tool_settings(registry, frozen_path):
    data = getattr(registry, "data", None)
    if data is None or data.allow_network is not False or data.root.resolve() != Path(frozen_path).resolve():
        raise EvaluationError("Both tool registries must use this run's frozen source root with network disabled")
    settings = {"class": type(registry).__module__ + "." + type(registry).__qualname__,
                "tools": registry.list_tools(), "source_manifest_sha256": _hash(data.manifest),
                "configuration": getattr(registry, "evaluation_config", {}), "extra_artifacts": {}}
    if getattr(registry, "market_adapter", None) is not None:
        raise EvaluationError("Paired planning requires frozen market data, not an attached live market adapter")
    if getattr(registry, "retrieval_encoder", None) is not None:
        raise EvaluationError("Paired planning currently requires frozen retrieval vectors or BM25; runtime encoders are development-only")
    for field in ("retrieval_vectors", "market_data"):
        artifact = getattr(registry, field, None)
        if artifact is not None:
            raw = Path(artifact).read_bytes() if isinstance(artifact, (str, Path)) else _canonical(artifact).encode("utf-8")
            payload = json.loads(raw)
            settings["extra_artifacts"][field] = {"sha256": hashlib.sha256(raw).hexdigest(), "json_sha256": _hash(payload)}
    return _copy(settings)


def _freeze_registry_inputs(registry, workspace):
    data = getattr(registry, "data", None)
    expected_cache = (workspace / "cache").resolve()
    if data is None or data.cache_dir.resolve() != expected_cache:
        raise EvaluationError("Each paired tool registry needs its own assigned cache directory")
    if expected_cache.exists() and any(expected_cache.iterdir()):
        raise EvaluationError("A paired registry cannot start with private cached sources")
    for field in ("retrieval_vectors", "market_data"):
        artifact = getattr(registry, field, None)
        if artifact is None:
            continue
        raw = Path(artifact).read_bytes() if isinstance(artifact, (str, Path)) else _canonical(artifact).encode("utf-8")
        _canonical(json.loads(raw))  # Validate finite JSON without reformatting original bytes.
        target = workspace / "inputs" / (field + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(raw)
        setattr(registry, field, target)


def _diagnostics(events, episodes):
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    signatures = Counter(_canonical({"tool": e.get("tool"), "arguments": e.get("arguments")}) for e in tool_calls)
    model_events = [e for e in events if e.get("type") == "model_call"]
    last_episodes = {e["id"]: e for e in episodes}
    role_counts = Counter(e.get("role") for e in model_events)
    usage = Counter()
    for episode in last_episodes.values():
        for key, value in episode.get("usage", {}).items():
            if type(value) in {int, float} and value >= 0:
                usage[key] += value
    return {"episodes": len(last_episodes),
            "controller_complete_episodes": sum(e.get("status") == "complete" for e in last_episodes.values()),
            "model_attempts": sum(e.get("model_calls", 0) for e in last_episodes.values()),
            "model_attempts_by_role": dict(role_counts),
            "tool_attempts": sum(e.get("tool_calls", 0) for e in last_episodes.values()),
            "planner_steps": sum(e.get("planner_steps", 0) for e in last_episodes.values()),
            "failed_model_attempts": sum(not e.get("ok") for e in model_events),
            "failed_tool_attempts": sum(not e.get("ok") for e in tool_calls),
            "repeated_tool_signatures": sum(max(0, count - 1) for count in signatures.values()),
            "network_requests": sum(e.get("network_requests", 0) for e in tool_calls),
            "active_seconds": sum(e.get("active_seconds", 0) for e in last_episodes.values()),
            "usage": dict(usage), "truth_scored": False,
            "interpretation": "Controller completion, failure and exact-call repetition are diagnostics, not human correctness or mistaken-call scores."}


def run_planning_pair(case: FrozenPlanningCase, model_factory, data_dir, *, output_dir,
                      budget=None, tools_factory=None, condition_order=CONDITIONS,
                      fixed_revision_rounds=1, clock=time.monotonic):
    """Run two isolated conversations with shared public inputs and frozen sources.

    model_factory() is called twice before any inference; it receives no arm
    label. tools_factory(store, frozen_data_dir, cache_dir) also receives no arm
    label and may supply a declared, identical fault-injection configuration.
    Caller must explicitly configure any model; this function never installs a
    fake model or reads credentials. Offline tests inject scripted fixtures.

    Every substantive turn has the same allowance. Clarification resumes its
    existing episode allowance in the common controller. Generated histories
    may diverge; neither arm is given the other's responses or private state.
    """
    if not isinstance(case, FrozenPlanningCase):
        raise EvaluationError("Expected a FrozenPlanningCase")
    context = case.context()
    if not isinstance(condition_order, (list, tuple)) or len(condition_order) != 2 or set(condition_order) != set(CONDITIONS):
        raise EvaluationError("Run each planning condition exactly once")
    limits = budget or Budget()
    if not isinstance(limits, Budget):
        raise EvaluationError("budget must be the common engine Budget")
    # Validate workflow configuration before creating files or spending tokens.
    FixedWorkflowPlanner(max_revision_rounds=fixed_revision_rounds)
    models = {name: model_factory() for name in condition_order}
    if models["adaptive"] is models["fixed"]:
        raise EvaluationError("Use fresh model clients for isolated paired conversations")
    baseline = _settings(models[condition_order[0]])
    modes = {getattr(model, "mode", "configured_model") for model in models.values()}
    if len(modes) != 1 or any(_settings(model) != baseline for model in models.values()):
        raise EvaluationError("Paired planning must use identical model settings and modes")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise EvaluationError("Use an empty output directory to preserve existing evaluation artifacts")
    _save(root / "case.json", case.to_dict())
    source_hash = _freeze_sources(data_dir, root / "sources")
    factories = tools_factory or (lambda store, sources, cache: ToolRegistry(store, sources, allow_network=False, cache_dir=cache))
    stores, registries, sessions = {}, {}, {}
    run = {"protocol": PROTOCOL_VERSION, "case_id": case.case_id, "context_sha256": case.context_sha256,
           "created_at": datetime.now(timezone.utc).isoformat(), "condition_order": list(condition_order),
           "settings": baseline, "model_mode": next(iter(modes)), "budget_per_episode": asdict(limits),
           "source_manifest_sha256": source_hash, "fixed_workflow": {"version": WORKFLOW_VERSION,
           "max_revision_rounds": fixed_revision_rounds}, "conditions": {}, "truth_scored": False,
           "scope_resolution": "Public task specifications are shared inputs; their upstream creation cost is not measured here.",
           "prompt_sha256": {"shared": _hash(COMMON_PROMPT), **{r: _hash(ROLE_PROMPTS[r]) for r in ("planner", "researcher", "critic")}},
           "quality_assessment": "Independent human assessment required; no model badge or simulated test is an E2 quality result."}
    try:
        # Build and validate both registries before the first paid/model action.
        tool_baseline = None
        for name in condition_order:
            workspace = root / name
            workspace.mkdir()
            store = stores[name] = SessionStore(workspace / "session.sqlite")
            registry = registries[name] = factories(store, root / "sources", workspace / "cache")
            _freeze_registry_inputs(registry, workspace)
            settings = _tool_settings(registry, root / "sources")
            if tool_baseline is not None and settings != tool_baseline:
                raise EvaluationError("Tool access, source snapshots or fault configuration differ between paired arms")
            tool_baseline = settings
            session = store.create_session("E2 " + case.case_id)
            sessions[name] = session["id"]
            for message in context["history"]:
                store.append_message(session["id"], message["role"], message["content"])
        run["tool_settings"] = tool_baseline
        _save(root / "protocol.json", {k: v for k, v in run.items() if k != "conditions"})
        for name in condition_order:
            _validate_sources(root / "sources", source_hash)
            model, store, registry, sid = models[name], stores[name], registries[name], sessions[name]
            if _settings(model) != baseline or _tool_settings(registry, root / "sources") != tool_baseline:
                raise EvaluationError("Model/tool configuration changed before the paired condition")
            candidates = []
            engine = ResearchEngine(store, _PublicTaskModel(model), registry, clock=clock, auto_skills=False,
                                    planner=FixedWorkflowPlanner(max_revision_rounds=fixed_revision_rounds) if name == "fixed" else None,
                                    candidate_callback=lambda candidate: candidates.append(_copy(candidate)))
            turns, episodes = [], []
            for index, turn in enumerate(context["turns"], 1):
                if turn["task"] is not None:
                    store.update_scope(sid, TaskSpec.from_dict(turn["task"]).scope())
                else:
                    # A new unresolved task cannot silently retain the previous
                    # task's structured request and manufacture completion.
                    old = store.get_session(sid)["scope"]
                    store.update_scope(sid, {k: v for k, v in old.items() if k != "planning_task"})
                before_count = len(candidates)
                start_sequence = max((e.get("event_sequence", 0) for e in store.events(sid, limit=1)), default=0)
                try:
                    result = engine.ask(sid, turn["message"], budget=limits)
                    episode = result["episode"]
                    episodes.append(episode)
                    output = {"turn": index, "status": result["status"], "episode": episode,
                              "question": result.get("question"), "report": result.get("report"),
                              "error_type": episode.get("error_type")}
                except Exception as exc:
                    output = {"turn": index, "status": "unavailable", "error_type": type(exc).__name__,
                              "report": None, "question": None}
                if _settings(model) != baseline or _tool_settings(registry, root / "sources") != tool_baseline:
                    raise EvaluationError("Model/tool configuration changed during paired execution")
                events = [e for e in store.events(sid, exclude_types=["episode_checkpoint"]) if e["event_sequence"] > start_sequence]
                candidate = candidates[before_count] if len(candidates) > before_count else None
                output["before_verification"] = candidate
                output["before_verification_sha256"] = _hash(candidate) if candidate is not None else None
                output["after_verification_sha256"] = _hash(output["report"]) if output["report"] is not None else None
                counters = ("model_calls", "tool_calls", "planner_steps", "active_seconds", "usage")
                output["phase_usage"] = {
                    "before_first_verification": {k: candidate.get("episode", {}).get(k) for k in counters} if candidate else None,
                    "final_episode": {k: output.get("episode", {}).get(k) for k in counters} if output.get("episode") else None}
                output["events"] = events
                output["truth_scored"] = False
                _save(root / name / (f"turn-{index:02d}.json"), output)
                turns.append(output)
            events = store.events(sid, exclude_types=["episode_checkpoint"])
            arm = {"condition": name, "turns": turns, "diagnostics": _diagnostics(events, episodes),
                   "database": str(root / name / "session.sqlite"), "context_sha256": case.context_sha256,
                   "source_manifest_sha256": source_hash, "settings_sha256": _hash(baseline),
                   "candidate_truth_scored": False}
            _validate_sources(root / "sources", source_hash)
            run["conditions"][name] = arm
            _save(root / name / "result.json", arm)
        _save(root / "result.json", run)
        return run
    except Exception as exc:
        _save(root / "failure.json", {"protocol": PROTOCOL_VERSION, "status": "protocol_failure",
              "error_type": type(exc).__name__, "case_id": case.case_id,
              "completed_conditions": list(run["conditions"]), "truth_scored": False,
              "notice": "This comparison did not finish under the frozen protocol. Inspect preserved per-arm databases and turn logs."})
        raise
    finally:
        for store in stores.values():
            store.close()


def preview_fixed_workflow(task, data_dir, *, output_dir, max_tool_calls=16,
                           max_planner_steps=64, max_seconds=240, tools_factory=None,
                           clock=time.monotonic):
    """Execute only fixed acquisition on real replay tools; never call a model.

    Stops before synthesis or clarification, returning inspectable original
    evidence and calculations. This is a developer preview, not an E2 run or an
    answer. Its finite loop mirrors budget reservation but does not replace the
    common ResearchEngine used by run_planning_pair.
    """
    spec = task if isinstance(task, TaskSpec) else TaskSpec.from_dict(task)
    if spec.to_dict()["source"] != "replay":
        raise EvaluationError("Offline plan preview requires source=replay")
    limits = Budget(max_model_calls=1, max_tool_calls=max_tool_calls, max_planner_steps=max_planner_steps,
                    max_seconds=max_seconds)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise EvaluationError("Use an empty output directory for a plan preview")
    source_hash = _freeze_sources(data_dir, root / "sources")
    store = SessionStore(root / "session.sqlite")
    start = clock()
    episode = {"id": "preview", "request": "Offline acquisition preview", "planner_steps": 0,
               "tool_calls": 0, "model_calls": 0, "issues": [], "active_claim_ids": []}
    steps, stop_reason, question = [], "planner_budget_exhausted", None
    try:
        sid = store.create_session("Offline fixed workflow preview")["id"]
        store.update_scope(sid, spec.scope())
        factory = tools_factory or (lambda store, sources, cache: ToolRegistry(store, sources, allow_network=False, cache_dir=cache))
        registry = factory(store, root / "sources", root / "cache")
        _freeze_registry_inputs(registry, root)
        tool_settings = _tool_settings(registry, root / "sources")
        planner = FixedWorkflowPlanner(max_revision_rounds=0)
        while episode["planner_steps"] < limits.max_planner_steps:
            if clock() - start >= limits.max_seconds:
                stop_reason = "time_budget_exhausted"
                break
            episode["planner_steps"] += 1
            context = store.get_context(sid, report_mode="none")
            context.update(scope=context["session"]["scope"], episode=_copy(episode), issues=[], tools=registry.list_tools())
            context["requirements"] = context["scope"].get("requirements", [])
            decision = validate_response("planner", planner.decide(context), context)
            step = {"decision": decision, "step": episode["planner_steps"]}
            steps.append(step)
            if decision.get("scope"):
                store.update_scope(sid, {**context["scope"], **decision["scope"]})
            if decision["action"] == "clarify":
                stop_reason, question = "clarification_required", decision["question"]
                break
            if decision["action"] in {"research", "finish"}:
                stop_reason = "synthesis_required" if decision["action"] == "research" else "workflow_stopped"
                break
            if episode["tool_calls"] >= limits.max_tool_calls:
                stop_reason = "tool_budget_exhausted"
                break
            episode["tool_calls"] += 1
            registry.timeout = max(0.01, min(registry.timeout, limits.max_seconds - (clock() - start)))
            try:
                result = registry.execute(decision["tool"], decision["arguments"], sid, store.get_session(sid)["scope"])
            except Exception as exc:
                result = {"ok": False, "summary": "Tool failed", "error_type": type(exc).__name__, "data": {}}
            step["result"] = result
            if clock() - start >= limits.max_seconds:
                stop_reason = "time_budget_exhausted"
                break
            observation = result.get("data", {}).get("source_observation")
            if observation:
                store.observe_document_source(sid, observation)
            if result.get("ok"):
                for record in result.get("evidence", []):
                    store.put_evidence(sid, record)
                for record in result.get("calculations", []):
                    store.put_calculation(sid, record)
            episode["last_tool_result"] = result
        result = {"protocol": "fixed-workflow-preview-v1", "status": "preview_only", "stop_reason": stop_reason,
                  "question": question, "model_calls": 0, "tool_calls": episode["tool_calls"],
                  "planner_steps": episode["planner_steps"], "task_sha256": spec.sha256,
                  "source_manifest_sha256": source_hash, "tool_settings": tool_settings, "steps": steps,
                  "context": store.get_context(sid, report_mode="none"), "truth_scored": False,
                  "notice": "Acquisition preview only. No synthesis or semantic verification ran; this is not an E2 effectiveness result."}
        _validate_sources(root / "sources", source_hash)
        if _tool_settings(registry, root / "sources") != tool_settings:
            raise EvaluationError("Preview tool configuration changed during acquisition")
        _save(root / "preview.json", result)
        return result
    finally:
        store.close()
