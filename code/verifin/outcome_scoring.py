"""Human-only E1 outcome assessment and honest resource accounting.

These functions validate supplied judgments; they do not establish financial
truth, consult a model, or manufacture completed annotation from templates.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json

from .evaluation import CONDITIONS, PROTOCOL_VERSION, EvaluationError, FrozenCase, _canonical, _hash, _text


PACKET_VERSION = "e1-outcome-packet-v1"
ANNOTATION_VERSION = "e1-outcome-annotation-v1"
SCORE_VERSION = "e1-outcome-score-v1"
UNIT_VERDICTS = {"correct", "partial", "incorrect", "missing", "legitimate_gap", "unassessable"}
ASSERTION_VERDICTS = {"supported", "unsupported", "contradicted", "unassessable"}
BAD_ASSERTIONS = {"unsupported", "contradicted"}


def _copy(value):
    return json.loads(_canonical(value))


def _seal(record, field):
    result = _copy(record)
    result[field] = _hash(result)
    return result


def _check_seal(record, field):
    if not isinstance(record, dict) or record.get(field) != _hash({k: v for k, v in record.items() if k != field}):
        raise EvaluationError(field + " mismatch")


def _index(rows, label, key="id"):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise EvaluationError(label + " must be a record array")
    result = {}
    for row in rows:
        identifier = _text(row.get(key), label + "." + key)
        if identifier in result:
            raise EvaluationError("Duplicate " + label + " identifier")
        result[identifier] = row
    return result


def _ids(value, allowed, label):
    if (not isinstance(value, list) or any(not isinstance(v, str) for v in value)
            or len(value) != len(set(value)) or not set(value) <= set(allowed)):
        raise EvaluationError(label + " must contain unique known IDs")
    return value


def _time(value, label):
    _text(value, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise EvaluationError(label + " must be a timezone-aware ISO timestamp") from None
    return parsed


def _run_binding(case, detection, revision):
    if not isinstance(detection, dict) or detection.get("protocol") != PROTOCOL_VERSION:
        raise EvaluationError("Unknown detection protocol")
    if revision is not None and not isinstance(revision, dict):
        raise EvaluationError("Revision run must be an object")
    for run in (detection, revision):
        if run is not None and (run.get("case_id") != case.case_id or run.get("context_sha256") != case.context_sha256):
            raise EvaluationError("Run is not bound to this frozen case")
    if revision is not None and (revision.get("protocol") != PROTOCOL_VERSION
            or revision.get("phase") != "candidate_revision" or revision.get("settings") != detection.get("settings")):
        raise EvaluationError("Revision protocol or model settings do not match detection")


def build_outcome_packet(case: FrozenCase, detection_run: dict, revision_run: dict, condition: str,
                         *, trial_id="trial-1") -> dict:
    """Bind displayed original/revised answers to exact immutable run outputs."""
    _run_binding(case, detection_run, revision_run)
    if condition not in CONDITIONS:
        raise EvaluationError("Unknown evaluation condition")
    _text(trial_id, "trial_id")
    detector = detection_run.get("conditions", {}).get(condition)
    revised = revision_run.get("conditions", {}).get(condition) if revision_run else None
    if not isinstance(detector, dict) or detector.get("condition") != condition:
        raise EvaluationError("Missing named detection condition")
    if (detector.get("case_id") != case.case_id or detector.get("context_sha256") != case.context_sha256
            or detector.get("output_sha256") != _hash(detector.get("feedback"))
            or detector.get("settings_sha256") != _hash(detection_run.get("settings"))):
        raise EvaluationError("Detection output binding is invalid")
    if revised is not None and not isinstance(revised, dict):
        raise EvaluationError("Invalid revision condition")
    candidate = None
    availability = "missing_revision"
    if revised:
        availability = revised.get("status")
        if availability not in {"candidate", "partial", "unavailable"}:
            raise EvaluationError("Unknown revision status")
        if availability != "unavailable":
            if (detector.get("status") != "ok" or revised.get("input_sha256") != case.context_sha256
                    or revised.get("feedback_sha256") != detector["output_sha256"]
                    or revised.get("settings") != detection_run.get("settings")):
                raise EvaluationError("Revision does not match its source case/feedback/settings")
            raw = revised.get("context")
            if not isinstance(raw, dict) or raw.get("requirements") != case.context()["requirements"]:
                raise EvaluationError("Candidate changed the original required-answer checklist")
            if raw.get("scope") != {**case.context()["scope"], "requirements": case.context()["requirements"]}:
                raise EvaluationError("Candidate changed the original research scope")
            if revised.get("claims") != raw.get("claims"):
                raise EvaluationError("Revision candidate claim copies disagree")
            candidate = {k: _copy(raw.get(k, [] if k in {"claims", "evidence", "calculations", "requirements", "messages"} else {}))
                         for k in ("scope", "requirements", "claims", "evidence", "calculations", "messages")}
            # Human assessors do not receive system support/completion badges.
            for claim in candidate["claims"]:
                for key in ("status", "confidence", "support_status"):
                    claim.pop(key, None)
    binding = {"case_id": case.case_id, "context_sha256": case.context_sha256,
               "trial_id": trial_id, "condition": condition,
               "detection_run_sha256": _hash(detection_run),
               "detection_output_sha256": detector["output_sha256"],
               "revision_run_sha256": _hash(revision_run) if revision_run is not None else None,
               "revision_output_sha256": _hash(revised) if revised is not None else None}
    return _seal({"protocol": PACKET_VERSION, "binding": binding,
                  "settings": _copy(detection_run.get("settings")),
                  "model_mode": detection_run.get("model_mode", "unknown"),
                  "availability": availability, "stop_reason": revised.get("stop_reason") if revised else None,
                  "baseline": case.context(), "candidate": candidate,
                  "truth_source": "independent_human_required"}, "packet_sha256")


def validate_packet(packet):
    _check_seal(packet, "packet_sha256")
    if packet.get("protocol") != PACKET_VERSION:
        raise EvaluationError("Unknown outcome packet protocol")
    if packet.get("availability") not in {"candidate", "partial", "unavailable", "missing_revision"}:
        raise EvaluationError("Unknown outcome availability")
    if (packet.get("candidate") is None) != (packet["availability"] in {"unavailable", "missing_revision"}):
        raise EvaluationError("Outcome availability disagrees with displayed candidate")
    binding = packet.get("binding", {})
    baseline = FrozenCase.from_context(binding.get("case_id"), packet.get("baseline"))
    if baseline.context_sha256 != binding.get("context_sha256"):
        raise EvaluationError("Packet original context hash mismatch")
    if packet.get("candidate") is not None:
        if packet["candidate"].get("requirements") != baseline.context()["requirements"]:
            raise EvaluationError("Packet candidate checklist mismatch")
        for field in ("claims", "evidence", "calculations"):
            _index(packet["candidate"].get(field), field)
    return _copy(packet)


def make_annotation_template(packet: dict, annotator_id: str) -> dict:
    """Incomplete by construction: humans must fill and attest each assessment."""
    validate_packet(packet)
    _text(annotator_id, "annotator_id")
    units = [{"id": "unit:" + r["id"], "requirement_id": r["id"], "description": r["text"],
              "answerability": None, "rationale": ""} for r in packet["baseline"]["requirements"]]
    def answer(context, prefix):
        if context is None:
            return None
        return {"claims_segmentation_complete": False,
                "units": [{"unit_id": u["id"], "verdict": None, "assertion_ids": [], "rationale": ""} for u in units],
                "assertions": [{"id": prefix + ":" + c["id"] + ":1", "claim_id": c["id"], "text": c["text"],
                                "requirement_unit_ids": [], "verdict": None, "evidence_refs": [], "rationale": "",
                                **({"baseline_assertion_ids": []} if prefix == "after" else {})}
                               for c in context["claims"]]}
    return {"protocol": ANNOTATION_VERSION, "packet_sha256": packet["packet_sha256"],
            "source": "independent_human", "annotator_id": annotator_id, "independent": False,
            "completed_at": None, "guideline_version": "e1-human-v1",
            "assessment": {"units": units, "baseline": answer(packet["baseline"], "before"),
                           "candidate": answer(packet["candidate"], "after")}}


def _assessment(packet, assessment):
    if not isinstance(assessment, dict):
        raise EvaluationError("assessment must be an object")
    requirements = _index(packet["baseline"]["requirements"], "requirements")
    units = _index(assessment.get("units"), "human required units")
    if not units:
        raise EvaluationError("At least one independently specified required content unit is needed")
    for unit in units.values():
        if unit.get("requirement_id") not in requirements:
            raise EvaluationError("Human unit refers to an unknown original requirement")
        _text(unit.get("description"), "unit.description")
        _text(unit.get("rationale"), "unit.rationale")
        if unit.get("answerability") not in {"answerable", "legitimate_gap"}:
            raise EvaluationError("Unit answerability must be independently resolved")
    if {u["requirement_id"] for u in units.values()} != set(requirements):
        raise EvaluationError("Human required units must cover every original requirement")
    baseline_assertions = {}
    for phase in ("baseline", "candidate"):
        context, answer = packet[phase], assessment.get(phase)
        if context is None:
            if answer is not None:
                raise EvaluationError("An unavailable candidate cannot receive invented judgments")
            continue
        if not isinstance(answer, dict) or answer.get("claims_segmentation_complete") is not True:
            raise EvaluationError("Human must confirm complete assertion segmentation")
        claims = _index(context["claims"], "claims")
        sources = set(_index(context["evidence"], "evidence")) | set(_index(context["calculations"], "calculations"))
        assertions = _index(answer.get("assertions"), "assertions")
        for assertion in assertions.values():
            if assertion.get("claim_id") not in claims:
                raise EvaluationError("Assertion must refer to an existing displayed claim")
            _text(assertion.get("text"), "assertion.text")
            _text(assertion.get("rationale"), "assertion.rationale")
            if assertion.get("verdict") not in ASSERTION_VERDICTS:
                raise EvaluationError("Assertion verdict is incomplete or unknown")
            _ids(assertion.get("requirement_unit_ids"), units, "assertion.requirement_unit_ids")
            _ids(assertion.get("evidence_refs"), sources, "assertion.evidence_refs")
            if phase == "candidate":
                _ids(assertion.get("baseline_assertion_ids"), baseline_assertions, "assertion.baseline_assertion_ids")
        if {a["claim_id"] for a in assertions.values()} != set(claims):
            raise EvaluationError("Every displayed claim must be covered by human assertion judgments")
        judgments = _index(answer.get("units"), "unit judgments", "unit_id")
        if set(judgments) != set(units):
            raise EvaluationError("Every human required unit needs an explicit judgment")
        for key, judgment in judgments.items():
            verdict = judgment.get("verdict")
            if verdict not in UNIT_VERDICTS:
                raise EvaluationError("Required-unit verdict is incomplete or unknown")
            _text(judgment.get("rationale"), "unit judgment rationale")
            refs = _ids(judgment.get("assertion_ids"), assertions, "unit.assertion_ids")
            if any(key not in assertions[i]["requirement_unit_ids"] for i in refs):
                raise EvaluationError("Assertion/required-unit links disagree")
            if verdict == "missing" and refs:
                raise EvaluationError("A missing unit cannot cite an asserted answer")
            if verdict in {"correct", "legitimate_gap", "partial", "incorrect"} and not refs:
                raise EvaluationError("An answered unit needs a human-linked assertion")
            if verdict in {"correct", "legitimate_gap"} and any(assertions[i]["verdict"] != "supported" for i in refs):
                raise EvaluationError("A fulfilled unit cannot rely on unsupported/unknown assertions")
            if verdict == "correct" and units[key]["answerability"] != "answerable":
                raise EvaluationError("A legitimate evidence gap cannot be scored as substantive correct content")
            if verdict == "legitimate_gap" and units[key]["answerability"] != "legitimate_gap":
                raise EvaluationError("Abstaining from an answerable unit is not legitimate-gap fulfillment")
        if phase == "baseline":
            baseline_assertions = assertions
    return _copy(assessment)


def validate_annotation(packet, annotation):
    validate_packet(packet)
    if (not isinstance(annotation, dict) or annotation.get("protocol") != ANNOTATION_VERSION
            or annotation.get("packet_sha256") != packet["packet_sha256"]):
        raise EvaluationError("Annotation is not bound to this packet")
    if annotation.get("source") != "independent_human" or annotation.get("independent") is not True:
        raise EvaluationError("Completed independent human annotation is required")
    _text(annotation.get("annotator_id"), "annotator_id")
    _text(annotation.get("guideline_version"), "guideline_version")
    _time(annotation.get("completed_at"), "annotation.completed_at")
    _assessment(packet, annotation.get("assessment"))
    return _copy(annotation)


def adjudicate_annotations(packet, annotations, resolution):
    """Validate an explicit human resolution; never choose a majority verdict."""
    validate_packet(packet)
    if not isinstance(annotations, list) or len(annotations) < 2:
        raise EvaluationError("At least two completed independent human annotations are required")
    records = [validate_annotation(packet, row) for row in annotations]
    people = [row["annotator_id"] for row in records]
    if len(set(people)) != len(people):
        raise EvaluationError("Independent annotations need distinct annotator IDs")
    if len({row["guideline_version"] for row in records}) != 1:
        raise EvaluationError("Annotators must use the same guideline version")
    if (not isinstance(resolution, dict) or resolution.get("source") != "independent_human"
            or resolution.get("adjudicated") is not True or resolution.get("packet_sha256") != packet["packet_sha256"]):
        raise EvaluationError("Explicit human adjudication bound to this packet is required")
    adjudicators = resolution.get("adjudicators")
    if (not isinstance(adjudicators, list) or not adjudicators or any(not isinstance(p, str) or not p.strip() for p in adjudicators)
            or len(set(adjudicators)) != len(adjudicators)):
        raise EvaluationError("Adjudicator identities are required")
    completed = _time(resolution.get("completed_at"), "adjudication.completed_at")
    if any(_time(row["completed_at"], "annotation.completed_at") > completed for row in records):
        raise EvaluationError("Adjudication cannot precede its independent annotations")
    final = _assessment(packet, resolution.get("assessment"))
    notes = resolution.get("resolution_notes", {})
    if not isinstance(notes, dict):
        raise EvaluationError("resolution_notes must be an object")
    for section in ("units", "baseline", "candidate"):
        variants = {_hash(row["assessment"].get(section)) for row in records} | {_hash(final.get(section))}
        if len(variants) > 1:
            _text(notes.get(section), "Explicit resolution note for differing " + section)
    return _seal({"protocol": "e1-outcome-adjudication-v1", "packet_sha256": packet["packet_sha256"],
                  "source": "independent_human", "adjudicated": True, "annotators": people,
                  "guideline_version": records[0]["guideline_version"], "annotations": records,
                  "annotation_sha256": [_hash(row) for row in records], "resolution": _copy(resolution)},
                 "adjudication_sha256")


def _ratio(n, d):
    return n / d if d else None


def _answer_metrics(units, answer):
    if answer is None:
        return None
    judgments = _index(answer["units"], "units", "unit_id")
    assertions = answer["assertions"]
    answerable = [key for key, unit in units.items() if unit["answerability"] == "answerable"]
    correct = sum(judgments[key]["verdict"] == "correct" for key in answerable)
    unknown_content = sum(judgments[key]["verdict"] == "unassessable" for key in answerable)
    gaps = [key for key, unit in units.items() if unit["answerability"] == "legitimate_gap"]
    gaps_handled = sum(judgments[key]["verdict"] == "legitimate_gap" for key in gaps)
    unknown_gaps = sum(judgments[key]["verdict"] == "unassessable" for key in gaps)
    unsupported = sum(a["verdict"] in BAD_ASSERTIONS for a in assertions)
    unknown_assertions = sum(a["verdict"] == "unassessable" for a in assertions)
    return {"required_units": len(units), "answerable_units": len(answerable), "correct_units": correct,
            "unassessable_answerable_units": unknown_content,
            "correct_content_coverage": _ratio(correct, len(answerable)) if not unknown_content else None,
            "correct_content_coverage_lower_bound": _ratio(correct, len(answerable)),
            "correct_content_coverage_upper_bound": _ratio(correct + unknown_content, len(answerable)),
            "legitimate_gap_units": len(gaps), "legitimate_gaps_handled": gaps_handled,
            "unassessable_gap_units": unknown_gaps,
            "legitimate_gap_handling_rate": _ratio(gaps_handled, len(gaps)) if not unknown_gaps else None,
            "fulfilled_required_units": correct + gaps_handled,
            "required_unit_fulfillment": _ratio(correct + gaps_handled, len(units)) if not unknown_content + unknown_gaps else None,
            "missing_units": sum(j["verdict"] == "missing" for j in judgments.values()),
            "assertions": len(assertions), "unsupported_assertions": unsupported,
            "contradicted_assertions": sum(a["verdict"] == "contradicted" for a in assertions),
            "unassessable_assertions": unknown_assertions,
            "unsupported_assertion_rate": _ratio(unsupported, len(assertions)) if not unknown_assertions else None,
            "unsupported_assertion_rate_lower_bound": _ratio(unsupported, len(assertions)),
            "unsupported_assertion_rate_upper_bound": _ratio(unsupported + unknown_assertions, len(assertions))}


def score_outcome(packet, adjudication):
    validate_packet(packet)
    _check_seal(adjudication, "adjudication_sha256")
    validated = adjudicate_annotations(packet, adjudication.get("annotations"), adjudication.get("resolution"))
    if validated != adjudication:
        raise EvaluationError("Adjudication content does not match its source annotations/resolution")
    assessment = adjudication["resolution"]["assessment"]
    units = _index(assessment["units"], "units")
    before, after = assessment["baseline"], assessment["candidate"]
    result = {"protocol": SCORE_VERSION, "binding": packet["binding"], "packet_sha256": packet["packet_sha256"],
              "adjudication_sha256": adjudication["adjudication_sha256"], "source": "independent_human",
              "model_mode": packet["model_mode"], "availability": packet["availability"],
              "settings_sha256": _hash(packet.get("settings")),
              "required_units_sha256": _hash(assessment["units"]), "baseline_assessment_sha256": _hash(before),
              "guideline_version": adjudication["guideline_version"],
              "stop_reason": packet["stop_reason"], "baseline": _answer_metrics(units, before),
              "candidate": _answer_metrics(units, after), "changes": None,
              "quality_change": "unavailable", "safe_improvement": None}
    if after is not None:
        prior_units = _index(before["units"], "before units", "unit_id")
        final_units = _index(after["units"], "after units", "unit_id")
        fulfilled = {"correct", "legitimate_gap"}
        corrected_units = [key for key in units if prior_units[key]["verdict"] not in fulfilled | {"unassessable"}
                           and final_units[key]["verdict"] in fulfilled]
        regressed_units = [key for key in units if prior_units[key]["verdict"] in fulfilled
                           and final_units[key]["verdict"] not in fulfilled | {"unassessable"}]
        deleted_units = [key for key in regressed_units if final_units[key]["verdict"] == "missing"]
        prior_assertions = _index(before["assertions"], "before assertions")
        final_assertions = _index(after["assertions"], "after assertions")
        descendants = {key: [a for a in final_assertions.values() if key in a["baseline_assertion_ids"]]
                       for key in prior_assertions}
        corrected_assertions, removed_optional, removed_required = [], [], []
        for key, assertion in prior_assertions.items():
            if assertion["verdict"] not in BAD_ASSERTIONS:
                continue
            children = descendants[key]
            if children and all(a["verdict"] == "supported" for a in children) and all(
                    final_units[u]["verdict"] in fulfilled for u in assertion["requirement_unit_ids"]):
                corrected_assertions.append(key)
            elif not children:
                (removed_required if assertion["requirement_unit_ids"] else removed_optional).append(key)
        new_bad = [key for key, a in final_assertions.items() if a["verdict"] in BAD_ASSERTIONS and
                   (not a["baseline_assertion_ids"] or all(prior_assertions[i]["verdict"] == "supported" for i in a["baseline_assertion_ids"]))]
        unknown = any(j["verdict"] == "unassessable" for answer in (before, after) for j in answer["units"] + answer["assertions"])
        improvement = bool(corrected_units or corrected_assertions or removed_optional)
        regression = bool(regressed_units or new_bad)
        bad_count_increase = max(0, result["candidate"]["unsupported_assertions"] - result["baseline"]["unsupported_assertions"])
        regression = regression or bad_count_increase > 0
        result["changes"] = {"corrected_required_unit_ids": corrected_units,
                             "regressed_required_unit_ids": regressed_units,
                             "deleted_correct_required_unit_ids": deleted_units,
                             "corrected_assertion_ids": corrected_assertions,
                             "removed_unsupported_optional_assertion_ids": removed_optional,
                             "removed_unsupported_required_assertion_ids": removed_required,
                             "new_unsupported_assertion_ids": new_bad,
                             "unsupported_assertion_count_increase": bad_count_increase}
        result["quality_change"] = "unassessable" if unknown else ("mixed" if improvement and regression else "improved" if improvement else "regressed" if regression else "unchanged")
        result["safe_improvement"] = None if unknown else improvement and not regression
    return _seal(result, "score_sha256")


def validate_prices(prices, model):
    if prices is None:
        return None
    if isinstance(prices, dict) and "schema_version" in prices:
        from .costs import validate_catalog
        try:
            return _copy(validate_catalog(prices))
        except (ValueError, TypeError) as exc:
            raise EvaluationError("Invalid dated price catalog: " + str(exc)) from None
    if not isinstance(prices, dict) or prices.get("model") != model:
        raise EvaluationError("Explicit prices must match the exact configured model")
    for key in ("currency", "source", "effective_date"):
        _text(prices.get(key), "prices." + key)
    try:
        datetime.strptime(prices["effective_date"], "%Y-%m-%d")
    except ValueError:
        raise EvaluationError("prices.effective_date must be YYYY-MM-DD") from None
    result = _copy(prices)
    # Old two-rate files do not establish cache discounts, write premiums,
    # context bands or service tiers. Preserve their arithmetic as a reference
    # unless the caller explicitly declares a genuinely flat-rate protocol.
    scheme = prices.get("pricing_scheme", "unadjusted_reference")
    if scheme not in {"flat_rate", "unadjusted_reference"}:
        raise EvaluationError("Legacy prices require flat_rate or unadjusted_reference pricing_scheme")
    result["pricing_scheme"] = scheme
    for key in ("input_per_million", "output_per_million"):
        try:
            if isinstance(prices.get(key), bool):
                raise ValueError()
            value = Decimal(str(prices[key]))
            if not value.is_finite() or value < 0:
                raise ValueError()
        except (InvalidOperation, ValueError, KeyError):
            raise EvaluationError("Explicit nonnegative finite token prices are required") from None
        result[key] = str(value)
    return result


def _measurement(values):
    if any(value is not None and not _nonnegative(value) for value, complete in values):
        raise EvaluationError("Resource counts/durations cannot be negative or nonfinite")
    known = sum(value for value, complete in values if value is not None)
    complete = all(valid for value, valid in values)
    return {"value": known if complete else None, "known_lower_bound": known, "complete": complete}


def _nonnegative(value, integer=False):
    import math
    return (type(value) is int if integer else type(value) in {int, float}) and value >= 0 and math.isfinite(value)


def summarize_resources(detection_run, revision_run=None, *, prices=None, trial_id="trial-1"):
    """Per-condition model estimates; no fabricated token zeros or price lookup."""
    if not isinstance(detection_run, dict) or detection_run.get("protocol") != PROTOCOL_VERSION:
        raise EvaluationError("Unknown detection resource protocol")
    _text(trial_id, "trial_id")
    if revision_run is not None and any(revision_run.get(key) != detection_run.get(key)
                                       for key in ("protocol", "case_id", "context_sha256", "settings")):
        raise EvaluationError("Resource stages are from different cases/settings")
    price = validate_prices(prices, detection_run.get("settings", {}).get("model"))
    rows = {}
    for name, detect in detection_run.get("conditions", {}).items():
        if name not in CONDITIONS or detect.get("condition") != name:
            raise EvaluationError("Unknown or mixed detection resource condition")
        if (detect.get("case_id") != detection_run.get("case_id") or detect.get("context_sha256") != detection_run.get("context_sha256")
                or detect.get("settings_sha256") != _hash(detection_run.get("settings"))
                or detect.get("output_sha256") != _hash(detect.get("feedback"))):
            raise EvaluationError("Resource condition is not bound to the original run output")
        revision = revision_run.get("conditions", {}).get(name) if revision_run is not None else None
        detector_usage = detect.get("usage")
        stages = [("detection", detect.get("model_attempts"), detector_usage if isinstance(detector_usage, dict) else {}, detect.get("seconds"))]
        revision_selected = revision_run is not None and name in revision_run.get("conditions", {})
        if revision_selected:
            if not isinstance(revision, dict) or revision.get("status") not in {"candidate", "partial", "unavailable"}:
                raise EvaluationError("Unknown revision resource status")
            if revision["status"] != "unavailable" and (
                    revision.get("input_sha256") != detection_run.get("context_sha256")
                    or revision.get("feedback_sha256") != detect["output_sha256"]
                    or revision.get("settings") != detection_run.get("settings")):
                raise EvaluationError("Revision resources are not bound to this context/feedback/settings")
            skipped = revision.get("revision_invocations") == 0
            usage = revision.get("usage")
            stages.append(("revision", 0 if skipped else usage.get("model_calls") if isinstance(usage, dict) else None,
                           usage if isinstance(usage, dict) else {}, 0 if skipped else usage.get("active_seconds") if isinstance(usage, dict) else None))
        tokens = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            observations = []
            for phase, calls, usage, seconds in stages:
                reported = usage.get(key + "_reported_calls")
                if reported is not None and (not _nonnegative(reported, True)
                        or (_nonnegative(calls, True) and reported > calls)):
                    raise EvaluationError("Token reporting counters exceed model attempts or are invalid")
                if calls == 0:
                    observations.append((0, True))
                else:
                    value = usage.get(key)
                    valid = _nonnegative(value, True)
                    all_reported = (phase == "detection" and calls == 1) or usage.get(key + "_reported_calls") == calls
                    observations.append((value if valid else None, bool(valid and _nonnegative(calls, True) and all_reported)))
            tokens[key] = _measurement(observations)
        row = {"detection_status": detect.get("status"), "detection_output_sha256": detect.get("output_sha256"),
               "revision_status": revision.get("status") if revision else None,
               "revision_selected": revision_selected,
               "model_calls": _measurement([(v, _nonnegative(v, True)) for _, v, _, _ in stages]),
               "seconds": _measurement([(s, _nonnegative(s)) for _, _, _, s in stages]), **tokens}
        for key in ("tool_calls", "network_requests"):
            value = 0 if not revision_selected or revision.get("revision_invocations") == 0 else (revision.get("usage") or {}).get(key)
            row[key] = _measurement([(value if _nonnegative(value, True) else None, _nonnegative(value, True))])
        input_tokens, output_tokens = tokens["prompt_tokens"], tokens["completion_tokens"]
        row["model_estimated_cost"] = None
        row["model_cost_known_lower_bound"] = None
        row["cost_method"] = "unpriced"
        if price and price.get("schema_version") == 1:
            from .costs import cost_ledger
            call_events = list(detect.get("events", []))
            if revision_selected and revision.get("revision_invocations") != 0:
                call_events.extend(revision.get("events", []))
            try:
                ledger = cost_ledger(call_events, price, expected_attempts=row["model_calls"]["value"])
            except (ValueError, TypeError) as exc:
                raise EvaluationError("Invalid per-call cost records: " + str(exc)) from None
            row.update(cost_method="dated_catalog", cost_ledger=ledger,
                       model_estimated_cost=ledger["estimated_total"],
                       model_cost_known_lower_bound=ledger["priced_calls_subtotal"]
                           if ledger["estimated_calls"] else None)
            if row["model_calls"]["value"] == 0:
                row.update(model_estimated_cost="0", model_cost_known_lower_bound="0")
        elif price:
            amount = (Decimal(input_tokens["known_lower_bound"]) * Decimal(price["input_per_million"])
                      + Decimal(output_tokens["known_lower_bound"]) * Decimal(price["output_per_million"])) / Decimal(1000000)
            row.update(cost_method="unadjusted_reference",
                       unadjusted_token_cost_reference=str(amount),
                       cost_reason="Two-rate arithmetic is not a monetary lower bound; declare a flat-rate protocol or supply a dated per-call catalog")
            settings = detection_run.get("settings", {})
            detailed_usage = any(set(usage) & {"cached_input_tokens", "cache_write_tokens",
                                "cache_write_30m_tokens", "cache_write_12h_tokens"}
                                 for _, _, usage, _ in stages)
            needs_catalog = (settings.get("api_mode") == "responses" or
                             settings.get("service_tier") is not None or detailed_usage)
            if price["pricing_scheme"] == "flat_rate" and not needs_catalog:
                row.update(cost_method="declared_flat_rate", cost_reason="Explicit flat-rate model-token estimate; not a provider invoice",
                           model_cost_known_lower_bound=str(amount))
                if input_tokens["complete"] and output_tokens["complete"]:
                    row["model_estimated_cost"] = str(amount)
            elif needs_catalog:
                row["cost_reason"] = "Responses, service-tier or cache usage requires a dated per-call price catalog; two-rate reference excluded from cost totals"
        rows[name] = row
    return _seal({"protocol": "e1-resources-v1", "case_id": detection_run.get("case_id"),
                  "trial_id": trial_id, "settings_sha256": _hash(detection_run.get("settings")),
                  "context_sha256": detection_run.get("context_sha256"), "model_mode": detection_run.get("model_mode", "unknown"),
                  "detection_run_sha256": _hash(detection_run), "revision_run_sha256": _hash(revision_run) if revision_run else None,
                  "prices": price, "conditions": rows,
                  "cost_scope": "Per-call catalog or explicitly declared flat-rate model-token estimate only, never an invoice. Legacy unadjusted references are excluded from monetary totals; tools, taxes and other charges are excluded.",
                  "latency_scope": "sum of measured stage execution durations; not user wall-clock or annotation time"}, "resources_sha256")
