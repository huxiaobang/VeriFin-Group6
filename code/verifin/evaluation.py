"""E1 paired frozen-evidence protocol; human annotations remain outside inputs.

This is evaluation scaffolding, not an experiment result or automatic truth
judge. Candidate revision is executable; outcome truth scoring and E2/E3 are not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import time
from typing import Protocol

from .model import COMMON_PROMPT, ROLE_PROMPTS, USAGE_FIELDS, validate_response
from .verification import check_rules


PROTOCOL_VERSION = "e1-frozen-detection-v1"
CONDITIONS = ("no_verification", "rules_only", "generic_review", "structured_critic")
CONTEXT_FIELDS = frozenset({"request", "messages", "session", "scope", "requirements",
                            "claims", "evidence", "calculations", "tools", "margin_formula_target"})
GOLD_FIELDS = frozenset({"gold", "gold_issues", "annotations", "human_judgments", "expected_answer"})
CLAIM_FIELDS = frozenset({"id", "text", "evidence_ids", "calculation_ids", "requirement_ids",
                          "constraints", "asserted_value", "asserted_unit", "scope_version", "stale",
                          "evidence_versions", "calculation_versions", "version"})


class EvaluationError(ValueError):
    pass


def _canonical(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise EvaluationError("Evaluation data must be finite JSON") from None


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{label} must be a nonempty string")
    return value


def _records(value, label):
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise EvaluationError(f"{label} must be an array of records")
    result = {}
    for row in value:
        key = _text(row.get("id"), label + ".id")
        if key in result:
            raise EvaluationError(f"Duplicate {label} ID")
        result[key] = row
    return result


def _no_annotation_fields(value):
    if isinstance(value, dict):
        if GOLD_FIELDS & value.keys():
            raise EvaluationError("Gold/annotation fields must remain outside model context")
        for child in value.values():
            _no_annotation_fields(child)
    elif isinstance(value, list):
        for child in value:
            _no_annotation_fields(child)


def _fiscal_scope_target(target, scope):
    if target is None:
        return None
    if not isinstance(target, dict):
        raise EvaluationError("fiscal_target must be an object")
    if not target or target.get("quarter"):
        # E1 needs the user's date bounds for the edited-window rule.
        # Derived quarter labels and earlier evidence links are not inputs.
        return None
    entity = _text(target.get("entity"), "fiscal_target.entity")
    bounds = {key: target[key] for key in ("period_start", "period_end") if target.get(key) is not None}
    try:
        valid_dates = all(date.fromisoformat(value).isoformat() == value for value in bounds.values())
    except (TypeError, ValueError):
        valid_dates = False
    if (not bounds or not valid_dates or scope.get("entities") != [entity]
            or any(scope.get(key) != value for key, value in bounds.items())
            or ("period_start" in bounds and "period_end" in bounds and bounds["period_start"] > bounds["period_end"])):
        raise EvaluationError("fiscal_target must agree with the frozen issuer and date bounds")
    return {"entity": entity, **bounds}


@dataclass(frozen=True)
class FrozenCase:
    case_id: str
    context_sha256: str
    _context_json: str

    @classmethod
    def from_context(cls, case_id: str, context: dict) -> "FrozenCase":
        _text(case_id, "case_id")
        if not isinstance(context, dict):
            raise EvaluationError("context must be an object")
        _no_annotation_fields(context)
        episode = context.get("episode", {})
        if isinstance(episode, dict) and episode.get("reviewed") is True:
            raise EvaluationError("Freeze E1 candidates before their semantic review, not from an already reviewed episode")
        for name in ("critic_review", "generic_review", "review_result", "reviewed_claim_ids", "reviewed_requirement_ids"):
            if context.get(name):
                raise EvaluationError("Prior review outputs must not be included in a frozen detection case")
        messages = context.get("messages", [])
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise EvaluationError("messages must be an array of conversation records")
        for message in messages:
            if message.get("role") in {"critic", "generic_reviewer", "reviewer"}:
                raise EvaluationError("Reviewer messages leak prior detection findings into E1 inputs")
        # Freeze source/task inputs, not earlier reports, status badges or
        # generated review findings. JSON copy prevents caller mutation.
        frozen = json.loads(_canonical({k: v for k, v in context.items() if k in CONTEXT_FIELDS}))
        if "request" not in frozen and isinstance(episode, dict) and isinstance(episode.get("request"), str):
            frozen["request"] = episode["request"]
        if "messages" in frozen:
            frozen["messages"] = [{k: v for k, v in message.items() if k in {"id", "role", "content", "created_at"}}
                                  for message in frozen["messages"]]
        if "session" in frozen and not isinstance(frozen["session"], dict):
            raise EvaluationError("session must be an object")
        scope = frozen.get("scope", frozen.get("session", {}).get("scope", {}))
        if not isinstance(scope, dict):
            raise EvaluationError("scope must be an object")
        frozen["scope"] = scope
        if target := _fiscal_scope_target(context.get("fiscal_target"), scope):
            frozen["fiscal_target"] = target
        frozen["requirements"] = frozen.get("requirements", scope.get("requirements", []))
        if "requirements" in scope and scope["requirements"] != frozen["requirements"]:
            raise EvaluationError("Frozen scope and top-level requirements disagree")
        if "scope" in frozen.get("session", {}) and frozen["session"]["scope"] != scope:
            raise EvaluationError("Frozen session scope and top-level scope disagree")
        for field in ("claims", "evidence", "calculations", "requirements"):
            _records(frozen.get(field, []), field)
            frozen.setdefault(field, [])
        frozen["claims"] = [{k: v for k, v in claim.items() if k in CLAIM_FIELDS} for claim in frozen["claims"]]
        if "session" in frozen:
            frozen["session"] = {k: v for k, v in frozen["session"].items() if k in {"id", "scope", "scope_version"}}
        encoded = _canonical(frozen)
        return cls(case_id, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), encoded)

    def context(self) -> dict:
        result = json.loads(self._context_json)
        if _hash(result) != self.context_sha256:
            raise EvaluationError("Frozen case context/hash mismatch")
        return result


def _settings(model) -> dict:
    # Intentionally excludes API keys and arbitrary instance state.
    names = ("model", "base_url", "max_output_tokens", "max_context_chars", "enable_thinking", "timeout",
             "api_mode", "reasoning_effort", "service_tier")
    result = {name: getattr(model, name, None) for name in names}
    for name in ("researcher_max_output_tokens", "critic_max_output_tokens", "critic_reasoning_effort"):
        if getattr(model, name, None) is not None:
            result[name] = getattr(model, name)
    if not isinstance(result["model"], str) or not result["model"]:
        raise EvaluationError("Evaluation model must expose its configured model ID")
    if type(result["max_output_tokens"]) is not int or result["max_output_tokens"] < 1:
        raise EvaluationError("Evaluation model must expose a positive output token limit")
    return result


def _model_call_event(model, role):
    """One reserved attempt with the configured identity, never credentials."""
    return {"type": "model_call", "role": role, "ok": False,
            "mode": getattr(model, "mode", "configured_model"),
            "model": getattr(model, "model", None),
            "base_url": getattr(model, "base_url", None),
            "api_mode": getattr(model, "api_mode", None),
            "service_tier": getattr(model, "service_tier", None)}


def _finish_model_event(event, model, seconds):
    # Keep the same bounded provider identifiers on successful and failed calls.
    import re
    metadata = getattr(model, "last_response_metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    event.update(seconds=max(0.0, seconds),
                 usage={k: v for k, v in (getattr(model, "last_usage", {}) or {}).items()
                        if k in USAGE_FIELDS and type(v) is int and v >= 0},
                 response_metadata={k: v for k, v in metadata.items()
                     if k in {"model", "service_tier"} and isinstance(v, str)
                     and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", v)})


def _served_model_consistency(events):
    calls = [e for e in events if e.get("type") == "model_call"]
    identities = [(e.get("response_metadata") or {}).get("model") for e in calls]
    models = sorted({m for m in identities if m})
    return {"status": "mismatch" if len(models) > 1 else
            "matched" if calls and all(identities) else "unreported",
            "served_models": models, "reported_calls": sum(bool(m) for m in identities),
            "model_attempts": len(calls)}


def evaluate_frozen_case(case: FrozenCase, model, *, clock=time.monotonic,
                         review_order=("generic_review", "structured_critic")) -> dict:
    """One paired case; 0, 0, 1, 1 model attempts respectively, no actions.

    Failures are preserved as unavailable observations, not successful empty
    reviews. Both model conditions receive exactly the same serialized context
    (including rule findings) and the same configured output/time limits.
    """
    context = case.context()
    rule_started = clock()
    display_notes = []
    context_notes = []
    rules = check_rules(context, display_notes=display_notes, context_notes=context_notes)
    rule_seconds = max(0.0, clock() - rule_started)
    baseline = _settings(model)
    if len(review_order) != 2 or set(review_order) != {"generic_review", "structured_critic"}:
        raise EvaluationError("review_order must contain each model review condition exactly once")
    shared = {**context, "rule_findings": rules}
    if context_notes:
        shared["source_context_notes"] = context_notes
    if display_notes:
        shared["display_review_notes"] = display_notes
    shared_hash = _hash(shared)
    outputs = {}
    for name in ("no_verification", "rules_only", *review_order):
        feedback = {"rule_findings": [] if name == "no_verification" else json.loads(_canonical(rules))}
        result = {"condition": name, "case_id": case.case_id, "context_sha256": case.context_sha256,
                  "model_mode": getattr(model, "mode", "configured_model"),
                  "status": "ok", "model_attempts": 0,
                  "rule_seconds": 0.0 if name == "no_verification" else rule_seconds,
                  "model_seconds": 0.0, "usage": {},
                  "feedback": feedback, "settings_sha256": _hash(baseline), "events": []}
        role = {"generic_review": "generic_reviewer", "structured_critic": "critic"}.get(name)
        if role:
            result["review_input_sha256"] = shared_hash
            if _settings(model) != baseline:
                raise EvaluationError("Model settings changed between paired review conditions")
            started = clock()
            result["model_attempts"] = 1
            event = _model_call_event(model, role)
            try:
                review_context = json.loads(_canonical(shared))
                model.last_usage = {}
                if hasattr(model, "last_response_metadata"):
                    model.last_response_metadata = {}
                response = model.complete(role, review_context)
                feedback["review"] = validate_response(role, response, shared)
                event["ok"] = True
                if _settings(model) != baseline:
                    raise EvaluationError("Model settings changed during paired review")
            except EvaluationError:
                raise
            except Exception as exc:
                result.update(status="unavailable", error_type=type(exc).__name__)
                event.update(ok=False, error_type=type(exc).__name__)
            finally:
                result["model_seconds"] = max(0.0, clock() - started)
                _finish_model_event(event, model, result["model_seconds"])
                result["usage"] = event["usage"]
                result["events"].append(event)
        result["seconds"] = result["rule_seconds"] + result["model_seconds"]
        result["output_sha256"] = _hash(feedback)
        outputs[name] = result
    return {"protocol": PROTOCOL_VERSION, "case_id": case.case_id, "context_sha256": case.context_sha256,
            "created_at": datetime.now(timezone.utc).isoformat(), "settings": baseline,
            "review_order": list(review_order),
            "served_model_consistency": _served_model_consistency(
                [event for result in outputs.values() for event in result["events"]]),
            "model_mode": getattr(model, "mode", "configured_model"),
            "prompt_sha256": {"shared": _hash(COMMON_PROMPT),
                              "generic_reviewer": _hash(ROLE_PROMPTS["generic_reviewer"]),
                              "critic": _hash(ROLE_PROMPTS["critic"])},
            "conditions": outputs, "revision_evaluated": False, "truth_scored": False}


def _human_metadata(record: dict, case: FrozenCase, label: str) -> None:
    if not isinstance(record, dict) or record.get("source") != "independent_human":
        raise EvaluationError(f"{label} requires independently supplied human annotation")
    if record.get("case_id") != case.case_id or record.get("context_sha256") != case.context_sha256:
        raise EvaluationError(f"{label} does not match the frozen case")
    annotators = record.get("annotators")
    if (not isinstance(annotators, list) or len(annotators) < 2 or
            any(not isinstance(a, str) or not a.strip() for a in annotators) or len(set(annotators)) != len(annotators)):
        raise EvaluationError(f"{label} requires at least two distinct human annotators")
    if record.get("adjudicated") is not True:
        raise EvaluationError(f"{label} disagreements must be adjudicated before scoring")
    _text(record.get("guideline_version"), label + ".guideline_version")


def score_detection(case: FrozenCase, condition_result: dict, gold: dict, judgments: dict) -> dict:
    """Count human-adjudicated findings; never infer truth from model badges.

    Human reviewers segment each condition's full feedback into findings and
    map each to one independent gold issue or null. Each gold issue counts once;
    extra duplicate detections count as false positives. No LLM is a judge here.
    """
    _human_metadata(gold, case, "gold")
    _human_metadata(judgments, case, "judgments")
    if condition_result.get("condition") not in CONDITIONS or condition_result.get("status") != "ok":
        raise EvaluationError("Unavailable or unknown conditions cannot be scored as successful reviews")
    if condition_result.get("case_id") != case.case_id or condition_result.get("context_sha256") != case.context_sha256:
        raise EvaluationError("Condition result does not match the frozen case")
    output_hash = _hash(condition_result.get("feedback"))
    if condition_result.get("output_sha256") != output_hash or judgments.get("output_sha256") != output_hash:
        raise EvaluationError("Human judgments must bind to the exact condition output")
    if judgments.get("condition") != condition_result["condition"] or judgments.get("segmentation_complete") is not True:
        raise EvaluationError("Judgments must cover the full named condition feedback")
    context = case.context()
    targets = set(_records(context["claims"], "claims")) | set(_records(context["requirements"], "requirements"))
    gold_issues = _records(gold.get("issues"), "gold issues")
    for issue in gold_issues.values():
        if issue.get("target_id") not in targets:
            raise EvaluationError("Gold issue targets an unknown claim or requirement")
        _text(issue.get("description"), "gold issue description")
    findings = _records(judgments.get("findings"), "human findings")
    if condition_result["condition"] == "no_verification" and findings:
        raise EvaluationError("No-verification control emits no defect findings")
    matches = []
    for finding in findings.values():
        _text(finding.get("text"), "finding text")
        if "gold_issue_id" not in finding:
            raise EvaluationError("Every finding needs a human gold_issue_id or null")
        match = finding["gold_issue_id"]
        if match is not None:
            if not isinstance(match, str) or match not in gold_issues:
                raise EvaluationError("Finding refers to an unknown gold issue")
            matches.append(match)
    true_positives = len(set(matches))
    false_positives = len(findings) - true_positives
    false_negatives = len(gold_issues) - true_positives
    denom = 2 * true_positives + false_positives + false_negatives
    scored = {"protocol": "e1-detection-score-v1", "case_id": case.case_id, "condition": condition_result["condition"],
            "context_sha256": case.context_sha256, "output_sha256": output_hash,
            "gold_sha256": _hash(gold), "judgments_sha256": _hash(judgments),
            "trial_id": _text(judgments.get("trial_id", "trial-1"), "trial_id"),
            "model_mode": condition_result.get("model_mode", "unknown"),
            "true_positives": true_positives, "false_positives": false_positives, "false_negatives": false_negatives,
            "duplicate_matches": len(matches) - len(set(matches)),
            "precision": true_positives / len(findings) if findings else None,
            "recall": true_positives / len(gold_issues) if gold_issues else None,
            "f1": 2 * true_positives / denom if denom else None,
            "gold_guideline_version": gold["guideline_version"], "judgment_guideline_version": judgments["guideline_version"],
            "source": "independent_human_annotations", "revision_evaluated": False}
    scored["score_sha256"] = _hash(scored)
    return scored


class RevisionExecutor(Protocol):
    """Common interface used identically by every verification condition."""
    def revise(self, context: dict, feedback: dict, *, budget: dict) -> dict: ...


def revision_input(case: FrozenCase, condition_result: dict) -> dict:
    """Prepare the same executor input shape for any condition; execute nothing."""
    if condition_result.get("context_sha256") != case.context_sha256 or condition_result.get("case_id") != case.case_id:
        raise EvaluationError("Revision input must belong to this frozen case")
    if condition_result.get("condition") not in CONDITIONS or condition_result.get("status") != "ok":
        raise EvaluationError("Only successful known conditions can supply revision input")
    if _hash(condition_result.get("feedback")) != condition_result.get("output_sha256"):
        raise EvaluationError("Revision feedback hash mismatch")
    return {"context": case.context(), "feedback": json.loads(_canonical(condition_result["feedback"]))}


def run_revisions(case: FrozenCase, detection_run: dict, executor: RevisionExecutor, *, budget: dict,
                  conditions=("generic_review", "structured_critic")) -> dict:
    """Execute isolated revisions under fixed settings/budgets, without judging truth.

    Condition labels stay in this coordinator; the executor receives only the
    same frozen context and that condition's feedback. No additional review arm
    is inserted. Failed detection arms remain explicitly unavailable.
    """
    if (detection_run.get("protocol") != PROTOCOL_VERSION or detection_run.get("case_id") != case.case_id
            or detection_run.get("context_sha256") != case.context_sha256):
        raise EvaluationError("Detection run does not match this frozen case/protocol")
    if (not isinstance(conditions, (tuple, list)) or not conditions or len(set(conditions)) != len(conditions)
            or any(c not in CONDITIONS for c in conditions)):
        raise EvaluationError("Revision conditions must be a nonempty unique selection of known conditions")
    model = getattr(executor, "model", None)
    baseline = _settings(model)
    if detection_run.get("settings") != baseline:
        raise EvaluationError("Revision model settings must match the paired detection run")
    budget = json.loads(_canonical(budget))
    results = {}
    prepared = {}
    # Validate the complete pair before spending anything on its first arm.
    for name in conditions:
        condition = detection_run.get("conditions", {}).get(name)
        if not isinstance(condition, dict) or condition.get("condition") != name:
            raise EvaluationError("Detection run is missing the named condition")
        if condition.get("settings_sha256") != _hash(baseline):
            raise EvaluationError("Detection condition settings hash is inconsistent")
        if (condition.get("case_id") != case.case_id or condition.get("context_sha256") != case.context_sha256
                or condition.get("output_sha256") != _hash(condition.get("feedback"))):
            raise EvaluationError("Detection condition context or feedback hash is inconsistent")
        if condition.get("status") not in {"ok", "unavailable"}:
            raise EvaluationError("Unknown detection condition status")
        prepared[name] = revision_input(case, condition) if condition["status"] == "ok" else None
    for name in conditions:
        if _settings(model) != baseline:
            raise EvaluationError("Model settings changed between paired revisions")
        condition = detection_run["conditions"][name]
        if condition.get("status") != "ok":
            results[name] = {"status": "unavailable", "stop_reason": "detection_unavailable",
                             "verification_available": False, "truth_scored": False, "revision_invocations": 0}
            continue
        inputs = prepared[name]
        try:
            result = executor.revise(**inputs, budget=json.loads(_canonical(budget)))
        except Exception as exc:
            result = {"status": "unavailable", "stop_reason": "executor_error", "error_type": type(exc).__name__,
                      "verification_available": False, "truth_scored": False, "usage": None}
        if _settings(model) != baseline:
            raise EvaluationError("Model settings changed during paired revision")
        if result.get("verification_available") is not False or result.get("truth_scored") is not False:
            raise EvaluationError("Revision executor must not self-certify its candidate answer")
        if result.get("status") != "unavailable":
            if result.get("input_sha256") != case.context_sha256 or result.get("feedback_sha256") != condition["output_sha256"]:
                raise EvaluationError("Revision output does not bind to its frozen context and feedback")
        result["revision_invocations"] = 1
        results[name] = result
    return {"protocol": PROTOCOL_VERSION, "phase": "candidate_revision", "case_id": case.case_id,
            "context_sha256": case.context_sha256, "settings": baseline, "budget": budget, "conditions": results,
            "revision_order": list(conditions), "model_mode": getattr(model, "mode", "configured_model"),
            "served_model_consistency": _served_model_consistency(
                [event for row in detection_run["conditions"].values() for event in row.get("events", [])] +
                [event for row in results.values() for event in row.get("events", [])]),
            "prompt_sha256": {"shared": _hash(COMMON_PROMPT), "revision_planner": _hash(ROLE_PROMPTS["revision_planner"]),
                              "researcher": _hash(ROLE_PROMPTS["researcher"])},
            "truth_scored": False, "outcome_assessment": "independent_human_assessment_required"}
