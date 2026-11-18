"""Offline, independent-human E2 answer and mistaken-tool-call assessment.

Protocol completion, failed/repeated calls and model support labels are never
used as correctness judgments. Reuses E1 content-unit/assertion validation.
"""
from __future__ import annotations

from .evaluation import EvaluationError, _hash, _text
from .engine import digest as _observation_hash
from .outcome_scoring import (_answer_metrics, _assessment, _check_seal, _copy,
                              _index, _ratio, _seal, _time)
from .planning_evaluation import CONDITIONS, PROTOCOL_VERSION, FrozenPlanningCase
from .planning import TaskSpec
from .study import _json, _write_files


PACKET_PROTOCOL = "e2-human-packet-v1"
ANNOTATION_PROTOCOL = "e2-human-annotation-v1"
SCORE_PROTOCOL = "e2-human-score-v1"
CALL_VERDICTS = {"justified", "mistaken", "unassessable"}


def _observation(event):
    value = event.get("observation")
    if value is None:
        if event.get("observation_sha256") is not None:
            raise EvaluationError("Missing tool observation has a non-null hash")
        return None  # Older saved logs did not retain instantaneous results.
    if not isinstance(value, dict) or event.get("observation_sha256") != _observation_hash(value):
        raise EvaluationError("Saved tool observation/hash mismatch")
    return _copy(value)


def _stage(value, requirements):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EvaluationError("Saved E2 answer stage must be an object or null")
    result = {key: _copy(value.get(key, [])) for key in ("claims", "evidence", "calculations")}
    for key in result:
        _index(result[key], key)
    result["scope"] = _copy(value.get("scope", {}))
    result["requirements"] = _copy(requirements)
    result["controller_requirements"] = _copy(value.get("requirements", value.get("scope", {}).get("requirements", [])))
    for claim in result["claims"]:
        for key in ("status", "confidence", "support_status"):
            claim.pop(key, None)
    return result


def _run(run):
    if not isinstance(run, dict) or run.get("protocol") != PROTOCOL_VERSION:
        raise EvaluationError("Expected a saved E2 paired planning run")
    arms = run.get("conditions")
    if not isinstance(arms, dict) or set(arms) != set(CONDITIONS):
        raise EvaluationError("A paired E2 result must preserve both conditions, including failed turns")
    turn_numbers = None
    for name, arm in arms.items():
        if (not isinstance(arm, dict) or arm.get("condition") != name or arm.get("context_sha256") != run.get("context_sha256")
                or arm.get("source_manifest_sha256") != run.get("source_manifest_sha256")
                or arm.get("settings_sha256") != _hash(run.get("settings"))):
            raise EvaluationError("E2 condition/context/source/settings binding mismatch")
        turns = arm.get("turns")
        if not isinstance(turns, list) or not turns:
            raise EvaluationError("E2 result needs saved user-turn outcomes")
        numbers = [t.get("turn") for t in turns if isinstance(t, dict)]
        if numbers != list(range(1, len(turns) + 1)) or (turn_numbers is not None and numbers != turn_numbers):
            raise EvaluationError("Both E2 conditions must retain the same contiguous public user turns")
        turn_numbers = numbers
        for turn in turns:
            for field, digest in (("before_verification", "before_verification_sha256"), ("report", "after_verification_sha256")):
                value = turn.get(field)
                if turn.get(digest) != (_hash(value) if value is not None else None):
                    raise EvaluationError("Saved E2 answer-stage hash mismatch")
            events = turn.get("events")
            if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
                raise EvaluationError("E2 turn events must be a record array")
            for event in events:
                if event.get("type") == "tool_call":
                    _observation(event)
    return turn_numbers


def build_planning_packet(case: FrozenPlanningCase, run: dict, condition: str, *, turn=1, trial_id="trial-1"):
    numbers = _run(run)
    if not isinstance(case, FrozenPlanningCase) or run.get("case_id") != case.case_id or run.get("context_sha256") != case.context_sha256:
        raise EvaluationError("Planning run does not match the frozen public case")
    public = case.context()
    if len(public["turns"]) != len(numbers) or condition not in CONDITIONS or type(turn) is not int or turn not in numbers:
        raise EvaluationError("Unknown condition/turn or incomplete public-case run")
    _text(trial_id, "trial_id")
    arm = run["conditions"][condition]
    outcome = arm["turns"][turn - 1]
    request = public["turns"][turn - 1]
    requirements = (TaskSpec.from_dict(request["task"]).scope()["requirements"] if request["task"] is not None else
                    [{"id": "public-request", "text": request["message"]}])
    calls, decisions = [], []
    for index, event in enumerate(outcome["events"], 1):
        if event.get("type") == "tool_call":
            _text(event.get("tool"), "tool name")
            if not isinstance(event.get("arguments"), dict):
                raise EvaluationError("Saved tool-call arguments must be an object")
            calls.append({"call_id": f"call-{index}", "order": index, "event_sha256": _hash(event),
                          "observation": _observation(event), "observation_sha256": event.get("observation_sha256"),
                          **{k: _copy(event.get(k)) for k in ("tool", "arguments", "ok", "summary")}})
        elif event.get("type") == "planner_decision" and isinstance(event.get("decision"), dict):
            # Explicit condition labels, fixed-planner event types and support
            # badges do not belong in the reviewer presentation.
            decisions.append({"order": index, "decision": {k: _copy(v) for k, v in event["decision"].items()
                                                           if k in {"action", "tool", "arguments", "scope", "question"}}})
    return _seal({"protocol": PACKET_PROTOCOL,
                  "binding": {"case_id": case.case_id, "context_sha256": case.context_sha256, "trial_id": trial_id,
                              "condition": condition, "turn": turn, "run_sha256": _hash(run), "arm_sha256": _hash(arm),
                              "turn_sha256": _hash(outcome), "before_sha256": outcome["before_verification_sha256"],
                              "after_sha256": outcome["after_verification_sha256"], "events_sha256": _hash(outcome["events"])},
                  "public_case": case.to_dict(), "public_request": request, "requirements": requirements,
                  "public_history": _copy(public["history"]), "prior_requests": _copy(public["turns"][:turn - 1]),
                  "model_mode": run.get("model_mode", "unknown"), "settings_sha256": _hash(run.get("settings")),
                  "source_manifest_sha256": run.get("source_manifest_sha256"),
                  "before": _stage(outcome.get("before_verification"), requirements),
                  "after": _stage(outcome.get("report"), requirements),
                  "question": outcome.get("question"), "controller_status": outcome.get("status"),
                  "stop_reason": (outcome.get("episode") or {}).get("stop_reason"), "error_type": outcome.get("error_type"),
                  "tool_calls": calls, "decisions": decisions,
                  "prior_answers": [{"turn": prior["turn"], "answer": _stage(prior.get("report"), []), "question": prior.get("question")}
                                    for prior in arm["turns"][:turn - 1]],
                  "truth_source": "independent_human_required"}, "packet_sha256")


def validate_planning_packet(packet):
    _check_seal(packet, "packet_sha256")
    if packet.get("protocol") != PACKET_PROTOCOL:
        raise EvaluationError("Unknown E2 human packet protocol")
    public = FrozenPlanningCase.from_dict(packet.get("public_case"))
    binding = packet.get("binding", {})
    if public.case_id != binding.get("case_id") or public.context_sha256 != binding.get("context_sha256"):
        raise EvaluationError("E2 annotation packet public-case binding mismatch")
    turn = binding.get("turn")
    if type(turn) is not int or not 1 <= turn <= len(public.context()["turns"]) or binding.get("condition") not in CONDITIONS:
        raise EvaluationError("Unknown E2 annotation turn/condition")
    request = public.context()["turns"][turn - 1]
    requirements = (TaskSpec.from_dict(request["task"]).scope()["requirements"] if request["task"] is not None else
                    [{"id": "public-request", "text": request["message"]}])
    if packet.get("requirements") != requirements or packet.get("public_request") != request:
        raise EvaluationError("E2 human requirements must preserve the original shared public task")
    if (packet.get("public_history") != public.context()["history"]
            or packet.get("prior_requests") != public.context()["turns"][:turn - 1]):
        raise EvaluationError("E2 review must preserve the preceding public conversation, without future turns")
    for phase in ("before", "after"):
        if packet.get(phase) is not None and packet[phase].get("requirements") != requirements:
            raise EvaluationError("Answer assessment checklist differs from the shared public task")
    calls = _index(packet.get("tool_calls"), "tool calls", "call_id")
    for call in calls.values():
        _observation(call)
    return _copy(packet)


def make_planning_annotation_template(packet, annotator_id):
    validate_planning_packet(packet)
    _text(annotator_id, "annotator_id")
    units = [{"id": "unit:" + row["id"], "requirement_id": row["id"], "description": row["text"],
              "answerability": None, "rationale": ""} for row in packet["requirements"]]
    def stage(context, prefix):
        if context is None:
            return None
        return {"claims_segmentation_complete": False,
                "units": [{"unit_id": u["id"], "verdict": None, "assertion_ids": [], "rationale": ""} for u in units],
                "assertions": [{"id": prefix + ":" + c["id"] + ":1", "claim_id": c["id"], "text": c["text"],
                                "requirement_unit_ids": [], "verdict": None, "evidence_refs": [], "rationale": ""}
                               for c in context["claims"]]}
    return {"protocol": ANNOTATION_PROTOCOL, "packet_sha256": packet["packet_sha256"], "source": "independent_human",
            "annotator_id": annotator_id, "independent": False, "completed_at": None, "guideline_version": "e2-human-v1",
            "assessment": {"units": units, "before": stage(packet["before"], "before"), "after": stage(packet["after"], "after"),
                           "tool_calls": [{"call_id": c["call_id"], "verdict": None, "rationale": ""} for c in packet["tool_calls"]]}}


def _validate_assessment(packet, assessment):
    if not isinstance(assessment, dict):
        raise EvaluationError("E2 human assessment must be an object")
    units = assessment.get("units")
    # Reuse E1 unit validation even if BOTH observed stages are missing. This
    # empty structural adapter is not displayed, returned or scored as an answer.
    indexed = _index(units, "human required units")
    empty = {"requirements": packet["requirements"], "claims": [], "evidence": [], "calculations": []}
    empty_judgments = {"claims_segmentation_complete": True, "assertions": [],
                       "units": [{"unit_id": key, "verdict": "missing", "assertion_ids": [], "rationale": "Schema validation only"}
                                 for key in indexed]}
    _assessment({"baseline": empty, "candidate": None}, {"units": units, "baseline": empty_judgments, "candidate": None})
    for phase in ("before", "after"):
        context, judgment = packet[phase], assessment.get(phase)
        if context is None:
            if judgment is not None:
                raise EvaluationError("A missing E2 answer stage must stay null, not receive invented judgments")
        else:
            _assessment({"baseline": context, "candidate": None}, {"units": units, "baseline": judgment, "candidate": None})
    expected = _index(packet["tool_calls"], "tool calls", "call_id")
    calls = _index(assessment.get("tool_calls"), "tool-call judgments", "call_id")
    if set(calls) != set(expected):
        raise EvaluationError("Every observed tool attempt needs a human judgment, including failed/repeated calls")
    for call in calls.values():
        if call.get("verdict") not in CALL_VERDICTS:
            raise EvaluationError("Tool-call judgment must be justified, mistaken or unassessable")
        _text(call.get("rationale"), "tool-call rationale")
    return _copy(assessment)


def validate_planning_annotation(packet, annotation):
    validate_planning_packet(packet)
    if (not isinstance(annotation, dict) or annotation.get("protocol") != ANNOTATION_PROTOCOL
            or annotation.get("packet_sha256") != packet["packet_sha256"] or annotation.get("source") != "independent_human"
            or annotation.get("independent") is not True):
        raise EvaluationError("Completed independent E2 human annotation bound to this packet is required")
    _text(annotation.get("annotator_id"), "annotator_id")
    _text(annotation.get("guideline_version"), "guideline_version")
    _time(annotation.get("completed_at"), "annotation.completed_at")
    _validate_assessment(packet, annotation.get("assessment"))
    return _copy(annotation)


def adjudicate_planning_annotations(packet, annotations, resolution):
    if not isinstance(annotations, list) or len(annotations) < 2:
        raise EvaluationError("At least two independently completed E2 annotations are required")
    originals = [validate_planning_annotation(packet, row) for row in annotations]
    people = [a["annotator_id"] for a in originals]
    if len(set(people)) != len(people) or len({a["guideline_version"] for a in originals}) != 1:
        raise EvaluationError("E2 annotators must be distinct and use the same guideline")
    if (not isinstance(resolution, dict) or resolution.get("source") != "independent_human"
            or resolution.get("adjudicated") is not True or resolution.get("packet_sha256") != packet["packet_sha256"]):
        raise EvaluationError("Explicit E2 human adjudication bound to this packet is required")
    people_resolving = resolution.get("adjudicators")
    if (not isinstance(people_resolving, list) or not people_resolving
            or any(not isinstance(p, str) or not p.strip() for p in people_resolving)
            or len(set(people_resolving)) != len(people_resolving)):
        raise EvaluationError("Actual adjudicator identities are required")
    when = _time(resolution.get("completed_at"), "resolution.completed_at")
    if any(_time(a["completed_at"], "annotation.completed_at") > when for a in originals):
        raise EvaluationError("Adjudication cannot precede independent annotations")
    final = _validate_assessment(packet, resolution.get("assessment"))
    notes = resolution.get("resolution_notes", {})
    if not isinstance(notes, dict):
        raise EvaluationError("resolution_notes must be an object")
    for section in ("units", "before", "after", "tool_calls"):
        if len({_hash(a["assessment"].get(section)) for a in originals} | {_hash(final.get(section))}) > 1:
            _text(notes.get(section), "Resolution note for differing " + section)
    return _seal({"protocol": "e2-human-adjudication-v1", "packet_sha256": packet["packet_sha256"],
                  "source": "independent_human", "adjudicated": True, "annotations": originals,
                  "annotation_sha256": [_hash(a) for a in originals], "resolution": _copy(resolution),
                  "guideline_version": originals[0]["guideline_version"]}, "adjudication_sha256")


def score_planning_outcome(packet, adjudication):
    validate_planning_packet(packet)
    _check_seal(adjudication, "adjudication_sha256")
    if adjudicate_planning_annotations(packet, adjudication.get("annotations"), adjudication.get("resolution")) != adjudication:
        raise EvaluationError("E2 adjudication content differs from its original annotations/resolution")
    assessment = adjudication["resolution"]["assessment"]
    units = _index(assessment["units"], "units")
    phases = {}
    for phase in ("before", "after"):
        metrics = _answer_metrics(units, assessment[phase])
        if metrics is not None:
            unknown = metrics["unassessable_answerable_units"] + metrics["unassessable_gap_units"] + metrics["unassessable_assertions"]
            metrics["human_complete_supported_answer"] = None if unknown else (
                metrics["fulfilled_required_units"] == len(units) and metrics["unsupported_assertions"] == 0)
        phases[phase] = metrics
    calls = assessment["tool_calls"]
    mistaken = sum(c["verdict"] == "mistaken" for c in calls)
    unknown = sum(c["verdict"] == "unassessable" for c in calls)
    return _seal({"protocol": SCORE_PROTOCOL, "binding": packet["binding"], "packet_sha256": packet["packet_sha256"],
                  "adjudication_sha256": adjudication["adjudication_sha256"], "source": "independent_human",
                  "model_mode": packet["model_mode"], "settings_sha256": packet["settings_sha256"],
                  "source_manifest_sha256": packet["source_manifest_sha256"], "required_units_sha256": _hash(assessment["units"]),
                  "guideline_version": adjudication["guideline_version"], "controller_status": packet["controller_status"],
                  "stop_reason": packet["stop_reason"], "error_type": packet["error_type"], **phases,
                  "tool_calls": {"attempts": len(calls), "mistaken": mistaken, "unassessable": unknown,
                                 "justified": sum(c["verdict"] == "justified" for c in calls),
                                 "mistaken_rate": _ratio(mistaken, len(calls)) if not unknown else None,
                                 "mistaken_rate_lower_bound": _ratio(mistaken, len(calls)),
                                 "mistaken_rate_upper_bound": _ratio(mistaken + unknown, len(calls)),
                                 "mistaken_call_ids": [c["call_id"] for c in calls if c["verdict"] == "mistaken"]}}, "score_sha256")


def summarize_planning_scores(run, scores):
    numbers = _run(run)
    run_hash = _hash(run)
    indexed, rubrics = {}, {}
    trial_ids = set()
    for score in scores:
        _check_seal(score, "score_sha256")
        if score.get("protocol") != SCORE_PROTOCOL or score.get("source") != "independent_human":
            raise EvaluationError("Only validated human E2 scores can be summarized")
        binding = score["binding"]
        condition, turn = binding["condition"], binding["turn"]
        if condition not in CONDITIONS or turn not in numbers or binding["run_sha256"] != run_hash:
            raise EvaluationError("E2 score is from another run or public turn")
        if binding["context_sha256"] != run["context_sha256"] or binding["case_id"] != run["case_id"]:
            raise EvaluationError("E2 score public-case binding mismatch")
        outcome = run["conditions"][condition]["turns"][turn - 1]
        if (binding["turn_sha256"] != _hash(outcome) or binding["arm_sha256"] != _hash(run["conditions"][condition])
                or binding["before_sha256"] != outcome["before_verification_sha256"]
                or binding["after_sha256"] != outcome["after_verification_sha256"]
                or binding["events_sha256"] != _hash(outcome["events"])
                or score["settings_sha256"] != _hash(run["settings"]) or score["source_manifest_sha256"] != run["source_manifest_sha256"]
                or score["model_mode"] != run.get("model_mode", "unknown")):
            raise EvaluationError("E2 score arm/turn/model/source binding mismatch")
        key = condition, turn
        if key in indexed:
            raise EvaluationError("Duplicate E2 human score for one condition/turn")
        rubric = (score["required_units_sha256"], score["guideline_version"], binding["trial_id"])
        if turn in rubrics and rubric != rubrics[turn]:
            raise EvaluationError("E2 conditions must share the same per-turn human content units, guideline and trial")
        rubrics[turn] = rubric
        indexed[key] = score
        trial_ids.add(binding["trial_id"])
    if len(trial_ids) > 1:
        raise EvaluationError("One E2 run cannot mix trial identifiers across turns")
    rows, groups = [], []
    for condition in CONDITIONS:
        members = []
        for turn in numbers:
            score = indexed.get((condition, turn))
            outcome = run["conditions"][condition]["turns"][turn - 1]
            row = {"condition": condition, "turn": turn, "annotation_status": "scored" if score else "pending",
                   "controller_status": outcome.get("status"), "before": score.get("before") if score else None,
                   "stop_reason": (outcome.get("episode") or {}).get("stop_reason"), "error_type": outcome.get("error_type"),
                   "after": score.get("after") if score else None, "tool_calls": score.get("tool_calls") if score else None,
                   "before_available": outcome.get("before_verification") is not None, "after_available": outcome.get("report") is not None}
            rows.append(row)
            members.append(row)
        group = {"condition": condition, "planned_turns": len(numbers), "scored_turns": sum(r["annotation_status"] == "scored" for r in members)}
        for phase in ("before", "after"):
            group[phase] = {}
            for field in ("correct_content_coverage", "unsupported_assertion_rate", "human_complete_supported_answer"):
                values = [r[phase][field] for r in members if r[phase] is not None and r[phase][field] is not None]
                group[phase][field] = {"mean": sum(values) / len(values) if values else None, "observed_turns": len(values)}
            group[phase]["unavailable_turns"] = sum(not r[phase + "_available"] for r in members)
        assessed = [r["tool_calls"] for r in members if r["tool_calls"] is not None]
        calls, mistaken, unknown = (sum(r[field] for r in assessed) for field in ("attempts", "mistaken", "unassessable"))
        group["tool_calls"] = {"assessed_attempts": calls, "mistaken": mistaken, "unassessable": unknown,
                               "mistaken_rate": _ratio(mistaken, calls) if len(assessed) == len(numbers) and not unknown else None,
                               "pending_turns": len(numbers) - len(assessed)}
        groups.append(group)
    paired = {}
    for phase in ("before", "after", "tool_calls"):
        paired[phase] = {}
        fields = ("mistaken_rate",) if phase == "tool_calls" else (
            "correct_content_coverage", "unsupported_assertion_rate", "human_complete_supported_answer")
        for field in fields:
            differences = []
            for turn in numbers:
                left, right = indexed.get(("adaptive", turn)), indexed.get(("fixed", turn))
                if (left is not None and right is not None and left[phase] is not None and right[phase] is not None
                        and left[phase][field] is not None and right[phase][field] is not None):
                    differences.append(left[phase][field] - right[phase][field])
            paired[phase][field] = {"adaptive_minus_fixed_mean": sum(differences) / len(differences) if differences else None,
                                    "paired_turns": len(differences), "excluded_turns": len(numbers) - len(differences)}
    return _seal({"protocol": "e2-human-summary-v1", "case_id": run["case_id"], "context_sha256": run["context_sha256"],
                  "run_sha256": run_hash, "model_mode": run.get("model_mode", "unknown"), "rows": rows, "groups": groups,
                  "paired": paired,
                  "annotation_complete": len(indexed) == len(CONDITIONS) * len(numbers),
                  "interpretation": "Independent human judgments only; null/unavailable phases remain missing. Repeated or failed calls are not automatically mistaken. Upstream public-task resolution cost is not measured by E2."},
                 "summary_sha256")


def write_planning_annotation_bundle(packet, output_dir, annotator_ids, *, overwrite=False):
    validate_planning_packet(packet)
    if (not isinstance(annotator_ids, (list, tuple)) or len(annotator_ids) < 2
            or any(not isinstance(p, str) or not p.strip() for p in annotator_ids) or len(set(annotator_ids)) != len(annotator_ids)):
        raise EvaluationError("At least two actual distinct reviewer identities are required")
    # Do not distribute administrative metadata or role/model-count traces.
    visible = {key: packet[key] for key in ("packet_sha256", "public_request", "public_history", "prior_requests", "requirements", "before", "after",
                                          "question", "tool_calls", "decisions", "prior_answers")}
    visible["status"] = "pending_independent_human_assessment"
    files = {"administrative/packet.json": _json(packet),
             "README.txt": "PENDING human work. Give each reviewer-N folder separately; keep administrative files private.\n"
                           "Use docs/PLANNING_ANNOTATION.md. Repetition/failure is not a mistaken-call gold label.\n"}
    for index, person in enumerate(annotator_ids, 1):
        template = make_planning_annotation_template(packet, person)
        files[f"reviewer-{index}/input.json"] = _json(visible)
        files[f"reviewer-{index}/annotation.json"] = _json(template)
    resolution = {"source": "independent_human", "adjudicated": False, "packet_sha256": packet["packet_sha256"],
                  "adjudicators": [], "completed_at": None, "assessment": template["assessment"], "resolution_notes": {}}
    files["administrative/resolution-template.json"] = _json(resolution)
    return {"status": "pending_human_annotation", "completed_annotations": 0, "truth_scored": False,
            "files": _write_files(output_dir, files, overwrite), "packet_sha256": packet["packet_sha256"]}
