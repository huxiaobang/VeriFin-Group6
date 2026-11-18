"""Offline study planning, annotation materials and descriptive aggregation.

No function calls a model or turns an unfilled human worksheet into a result.
Related events, dialogues and perturbations cannot cross dataset splits.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import random
import re

from .evaluation import CONDITIONS, EvaluationError, _hash, _text
from .outcome_scoring import (_check_seal, _copy, _index, _nonnegative, _ratio, _seal,
                              make_annotation_template, validate_packet)


STUDY_VERSION = "verifin-study-v1"
SPLITS = {"development", "validation", "heldout"}


def case_manifest_entry(case, *, group_id, kind, event_id, issuer_ids, dialogue_id=None, parent_case_id=None):
    """Metadata only; supports FrozenCase and compatible frozen E2 case objects."""
    context = case.context()
    turns = sum(m.get("role") == "user" for m in context.get("messages", []) if isinstance(m, dict))
    if isinstance(context.get("turns"), list):
        turns = len(context["turns"])
    row = {"case_id": case.case_id, "context_sha256": case.context_sha256,
           "group_id": group_id, "kind": kind, "event_id": event_id, "issuer_ids": issuer_ids,
           "dialogue_id": dialogue_id, "parent_case_id": parent_case_id, "user_turns": turns}
    _manifest([row], permit_external_parent=True)
    return _copy(row)


def _manifest(entries, *, permit_external_parent=False):
    rows = _index(entries, "case manifest", "case_id")
    for row in rows.values():
        for key in ("group_id", "event_id"):
            _text(row.get(key), key)
        if not isinstance(row.get("context_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["context_sha256"]):
            raise EvaluationError("Manifest context_sha256 must be a SHA-256 digest")
        if row.get("kind") not in {"natural", "injected"}:
            raise EvaluationError("Natural cases and injected diagnostics must be explicitly distinguished")
        issuers = row.get("issuer_ids")
        if (not isinstance(issuers, list) or not issuers or any(not isinstance(i, str) or not i.strip() for i in issuers)
                or len(set(issuers)) != len(issuers)):
            raise EvaluationError("issuer_ids must contain distinct explicit issuer identifiers")
        if type(row.get("user_turns", 0)) is not int or row.get("user_turns", 0) < 0:
            raise EvaluationError("user_turns must be a nonnegative integer")
        for key in ("dialogue_id", "parent_case_id"):
            if row.get(key) is not None:
                _text(row[key], key)
        parent = row.get("parent_case_id")
        if row["kind"] == "injected" and parent is None:
            raise EvaluationError("Injected diagnostics must identify their parent case")
        if parent == row["case_id"] or (parent is not None and parent not in rows and not permit_external_parent):
            raise EvaluationError("Parent case is missing or self-referential")
        if "split" in row and row["split"] not in SPLITS:
            raise EvaluationError("Unknown dataset split")
    if not permit_external_parent:
        for row in rows.values():
            seen, current = set(), row
            while current.get("parent_case_id") is not None:
                if current["case_id"] in seen:
                    raise EvaluationError("Parent-case lineage contains a cycle")
                seen.add(current["case_id"])
                current = rows[current["parent_case_id"]]
    return rows


def assign_grouped_splits(entries, *, heldout_group_ids, validation_group_ids=()):
    rows = _manifest(entries)
    groups = {row["group_id"] for row in rows.values()}
    for selected in (heldout_group_ids, validation_group_ids):
        if (not isinstance(selected, (list, tuple, set)) or any(not isinstance(g, str) for g in selected)
                or len(selected) != len(set(selected)) or not set(selected) <= groups):
            raise EvaluationError("Split selections must name distinct existing groups")
    heldout, validation = set(heldout_group_ids), set(validation_group_ids)
    if heldout & validation:
        raise EvaluationError("A group cannot belong to multiple splits")
    assigned = {key: {**_copy(row), "split": "heldout" if row["group_id"] in heldout else
                      "validation" if row["group_id"] in validation else "development"} for key, row in rows.items()}
    # Checking each relation edge also protects its entire transitive component.
    links = {}
    for row in assigned.values():
        for field in ("group_id", "event_id", "dialogue_id", "context_sha256"):
            value = row.get(field)
            if value is None:
                continue
            key = (field, value)
            if key in links and links[key] != row["split"]:
                raise EvaluationError("Related cases leak across splits through " + field)
            links[key] = row["split"]
        parent = row.get("parent_case_id")
        if parent is not None and assigned[parent]["split"] != row["split"]:
            raise EvaluationError("A perturbation/related turn cannot cross its parent case's split")
    return [assigned[key] for key in sorted(assigned)]


def blank_heldout_plan(count=12):
    if type(count) is not int or count < 1:
        raise EvaluationError("Slot count must be a positive integer")
    return {"protocol": "verifin-heldout-slots-v1", "status": "pending_human_case_selection",
            "completed_cases": 0, "completed_annotations": 0,
            "requirements": ["New events distinct from development", "Related turns/perturbations share one group",
                             "Include short dialogues", "Include an issuer absent from development",
                             "Two independent human annotations and explicit adjudication"],
            "slots": [{"slot_id": f"heldout-{i:02d}", "status": "pending", "case_id": None,
                       "group_id": None, "event_id": None, "issuer_ids": [], "source_urls": [],
                       "request": None, "dialogue_id": None, "context_sha256": None,
                       "annotator_ids": [], "gold_completed": False} for i in range(1, count + 1)]}


def _readiness(plan):
    entries = plan["cases"]
    heldout = [r for r in entries if r["split"] == "heldout" and r["kind"] == "natural"]
    unique_natural = len({r["context_sha256"] for r in heldout})
    used_issuers = {i for r in entries if r["split"] != "heldout" for i in r["issuer_ids"]}
    unseen = sorted({i for r in heldout for i in r["issuer_ids"]} - used_issuers)
    missing = []
    if unique_natural < plan["expected_heldout_cases"]:
        missing.append("insufficient_distinct_natural_heldout_cases")
    if plan["require_unseen_issuer"] and not unseen:
        missing.append("no_heldout_issuer_absent_from_development_and_validation")
    if plan["require_dialogue"] and not any(r.get("user_turns", 0) > 1 for r in heldout):
        missing.append("no_heldout_dialogue")
    settings = plan.get("model_settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("model"), str) or not settings["model"].strip():
        missing.append("model_snapshot_not_specified")
    if not isinstance(plan.get("budgets"), dict) or not plan["budgets"]:
        missing.append("budgets_not_specified")
    elif (any(not _nonnegative(plan["budgets"].get(k), True) or plan["budgets"][k] == 0
              for k in ("max_model_calls", "max_tool_calls"))
          or not _nonnegative(plan["budgets"].get("max_seconds")) or plan["budgets"]["max_seconds"] == 0):
        missing.append("finite_positive_model_tool_time_budgets_required")
    if not isinstance(plan.get("tolerances"), dict) or not plan["tolerances"]:
        missing.append("quality_and_cost_tolerances_not_specified")
    if set(plan["conditions"]) != set(CONDITIONS):
        missing.append("primary_E1_requires_all_four_conditions")
    return {"ready_to_freeze": not missing, "missing": missing,
            "distinct_natural_heldout_cases": unique_natural, "heldout_only_issuers": unseen}


def create_study_plan(entries, *, study_id, heldout_group_ids, validation_group_ids=(), seed=0,
                      model_settings=None, budgets=None, tolerances=None, expected_heldout_cases=12,
                      repetitions=1, conditions=CONDITIONS, require_unseen_issuer=True, require_dialogue=True):
    _text(study_id, "study_id")
    if type(seed) is not int or type(repetitions) is not int or repetitions < 1:
        raise EvaluationError("Seed and positive repetitions must be integers")
    if type(expected_heldout_cases) is not int or expected_heldout_cases < 1:
        raise EvaluationError("Expected heldout case count must be positive")
    if type(require_unseen_issuer) is not bool or type(require_dialogue) is not bool:
        raise EvaluationError("Study requirements must be booleans")
    if (not isinstance(conditions, (list, tuple)) or not conditions or any(not isinstance(c, str) for c in conditions) or len(set(conditions)) != len(conditions)
            or any(c not in CONDITIONS for c in conditions)):
        raise EvaluationError("Conditions must be distinct known E1 conditions")
    for value in (model_settings, budgets, tolerances):
        if value is not None and not isinstance(value, dict):
            raise EvaluationError("Settings, budgets and tolerances must be objects or null")
    cases = assign_grouped_splits(entries, heldout_group_ids=heldout_group_ids, validation_group_ids=validation_group_ids)
    order = [row["case_id"] for row in cases if row["split"] == "heldout"]
    random.Random(seed).shuffle(order)
    schedule = []
    for trial in range(1, repetitions + 1):
        for index, case_id in enumerate(order):
            reversed_order = (index + trial - 1) % 2
            review_order = ["generic_review", "structured_critic"]
            if reversed_order:
                review_order.reverse()
            shift = (index + trial - 1) % len(conditions)
            condition_order = list(conditions[shift:]) + list(conditions[:shift])
            schedule.append({"case_id": case_id, "trial_id": f"trial-{trial}", "review_order": review_order,
                             "revision_conditions": condition_order})
    plan = {"protocol": STUDY_VERSION, "study_id": study_id, "status": "draft",
            "created_at": datetime.now(timezone.utc).isoformat(), "seed": seed, "repetitions": repetitions,
            "expected_heldout_cases": expected_heldout_cases, "conditions": list(conditions),
            "require_unseen_issuer": require_unseen_issuer, "require_dialogue": require_dialogue,
            "model_settings": _copy(model_settings), "budgets": _copy(budgets), "tolerances": _copy(tolerances),
            "cases": cases, "schedule": schedule,
            "manifest_sha256": _hash(cases), "human_annotation_complete": False, "model_runs_complete": False,
            "analysis_policy": "Separate natural/injected and execution modes; retain missing/failed pairs; descriptive small-sample reporting."}
    plan["readiness"] = _readiness(plan)
    return _seal(plan, "plan_sha256")


def validate_study_plan(plan, *, require_frozen=False):
    _check_seal(plan, "plan_sha256")
    if plan.get("protocol") != STUDY_VERSION or plan.get("status") not in {"draft", "frozen"}:
        raise EvaluationError("Unknown study plan protocol/status")
    _manifest(plan.get("cases"))
    heldout = {r["group_id"] for r in plan["cases"] if r["split"] == "heldout"}
    validation = {r["group_id"] for r in plan["cases"] if r["split"] == "validation"}
    rebuilt = create_study_plan(plan["cases"], study_id=plan["study_id"], heldout_group_ids=sorted(heldout),
                                validation_group_ids=sorted(validation), seed=plan["seed"], model_settings=plan["model_settings"],
                                budgets=plan["budgets"], tolerances=plan["tolerances"], expected_heldout_cases=plan["expected_heldout_cases"],
                                repetitions=plan["repetitions"], conditions=plan["conditions"],
                                require_unseen_issuer=plan["require_unseen_issuer"], require_dialogue=plan["require_dialogue"])
    for key in ("cases", "schedule", "manifest_sha256", "readiness"):
        if plan.get(key) != rebuilt[key]:
            raise EvaluationError("Study plan's derived " + key + " is inconsistent")
    if (require_frozen or plan["status"] == "frozen") and (plan["status"] != "frozen" or not plan["readiness"]["ready_to_freeze"]):
        raise EvaluationError("A complete frozen study plan is required")
    if plan["status"] == "frozen":
        from .outcome_scoring import _time
        _time(plan.get("frozen_at"), "plan.frozen_at")
    return _copy(plan)


def freeze_study_plan(plan):
    validate_study_plan(plan)
    if plan["status"] == "frozen":
        return _copy(plan)
    if not plan["readiness"]["ready_to_freeze"]:
        raise EvaluationError("Study is not ready: " + ", ".join(plan["readiness"]["missing"]))
    frozen = {k: v for k, v in plan.items() if k != "plan_sha256"}
    frozen.update(status="frozen", frozen_at=datetime.now(timezone.utc).isoformat())
    return _seal(frozen, "plan_sha256")


def _write_files(directory, files, overwrite):
    directory = Path(directory)
    # Check the entire batch first, so a conflict does not leave a half-created bundle.
    for relative in files:
        path = directory / relative
        if path.exists() and not overwrite:
            raise EvaluationError("Output already exists: " + str(path))
    written = []
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w" if overwrite else "x", encoding="utf-8", newline="") as stream:
            stream.write(content)
        written.append(str(path.resolve()))
    return written


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_annotation_bundle(packet, output_dir, annotator_ids, *, overwrite=False):
    validate_packet(packet)
    if (not isinstance(annotator_ids, (list, tuple)) or len(annotator_ids) < 2
            or any(not isinstance(a, str) or not a.strip() for a in annotator_ids) or len(set(annotator_ids)) != len(annotator_ids)):
        raise EvaluationError("Provide at least two distinct annotator identities; no fake completed annotation is generated")
    # Distribute only each reviewer folder. Administrative files disclose condition identity.
    visible = {"packet_sha256": packet["packet_sha256"], "status": "pending_independent_human_assessment",
               "baseline": packet["baseline"], "candidate": packet["candidate"], "candidate_availability": packet["availability"]}
    files = {"administrative/packet.json": _json(packet),
             "README.txt": "PENDING HUMAN WORK. Distribute each reviewer-N directory separately. Keep administrative files private.\n"
                           "The templates are deliberately incomplete and cannot be scored. Follow docs/ANNOTATION.md.\n"}
    templates = []
    for index, person in enumerate(annotator_ids, 1):
        template = make_annotation_template(packet, person)
        templates.append(template)
        files[f"reviewer-{index}/input.json"] = _json(visible)
        files[f"reviewer-{index}/annotation.json"] = _json(template)
    resolution = {"source": "independent_human", "adjudicated": False, "packet_sha256": packet["packet_sha256"],
                  "adjudicators": [], "completed_at": None, "assessment": templates[0]["assessment"], "resolution_notes": {}}
    files["administrative/resolution-template.json"] = _json(resolution)
    paths = _write_files(output_dir, files, overwrite)
    return {"status": "pending_human_annotation", "packet_sha256": packet["packet_sha256"], "files": paths,
            "completed_annotations": 0, "truth_scored": False}


def _score_key(score):
    binding = score["binding"]
    return binding["case_id"], binding["trial_id"], binding["condition"]


def aggregate_results(plan, scores, *, resources=(), detection_scores=(), require_complete=False):
    """Descriptive summary; missing/failed observations are never perfect zeros."""
    validate_study_plan(plan)
    manifest = {row["case_id"]: row for row in plan["cases"] if row["split"] == "heldout"}
    expected = {(run["case_id"], run["trial_id"], condition) for run in plan["schedule"] for condition in plan["conditions"]}
    scored, baselines, resource_map, detected, golds = {}, {}, {}, {}, {}
    for score in scores:
        _check_seal(score, "score_sha256")
        if score.get("protocol") != "e1-outcome-score-v1" or score.get("source") != "independent_human":
            raise EvaluationError("Only validated independent-human outcome scores can be aggregated")
        key = _score_key(score)
        if key not in expected or score["binding"]["context_sha256"] != manifest[key[0]]["context_sha256"]:
            raise EvaluationError("Outcome belongs to an unplanned trial/split/context")
        if key in scored:
            raise EvaluationError("Duplicate outcome for one planned case/trial/condition")
        if plan["model_settings"] is not None and score.get("settings_sha256") != _hash(plan["model_settings"]):
            raise EvaluationError("Outcome model settings differ from the study plan")
        baseline = (score["required_units_sha256"], score["baseline_assessment_sha256"], score["guideline_version"])
        if key[:2] in baselines and baselines[key[:2]] != baseline:
            raise EvaluationError("Paired conditions must use the same human required units and baseline judgments")
        baselines[key[:2]] = baseline
        scored[key] = score
    for resource in resources:
        _check_seal(resource, "resources_sha256")
        if resource.get("protocol") != "e1-resources-v1":
            raise EvaluationError("Unknown resource summary protocol")
        case_id, trial = resource.get("case_id"), resource.get("trial_id", "trial-1")
        if case_id not in manifest or resource.get("context_sha256") != manifest[case_id]["context_sha256"]:
            raise EvaluationError("Resource summary belongs to an unplanned context")
        if plan["model_settings"] is not None and resource.get("settings_sha256") != _hash(plan["model_settings"]):
            raise EvaluationError("Resource model settings differ from the study plan")
        for condition, measurements in resource["conditions"].items():
            key = case_id, trial, condition
            if key not in expected:
                continue
            if key in resource_map:
                raise EvaluationError("Duplicate resources for one planned case/trial/condition")
            if key in scored and any(resource[field] != scored[key]["binding"][field]
                                     for field in ("detection_run_sha256", "revision_run_sha256")):
                raise EvaluationError("Outcome and resource summaries refer to different runs")
            resource_map[key] = (resource, measurements)
    for score in detection_scores:
        _check_seal(score, "score_sha256")
        if score.get("protocol") != "e1-detection-score-v1" or score.get("source") != "independent_human_annotations":
            raise EvaluationError("Only bound human detection scores can be aggregated")
        key = score["case_id"], score.get("trial_id", "trial-1"), score["condition"]
        if key not in expected or score["context_sha256"] != manifest[key[0]]["context_sha256"] or key in detected:
            raise EvaluationError("Duplicate/unplanned detection score")
        if key in scored and score["output_sha256"] != scored[key]["binding"]["detection_output_sha256"]:
            raise EvaluationError("Detection and outcome scores refer to different review outputs")
        if key in resource_map and score["output_sha256"] != resource_map[key][1].get("detection_output_sha256"):
            raise EvaluationError("Detection score and resource summary refer to different review outputs")
        if key[:2] in golds and golds[key[:2]] != score["gold_sha256"]:
            raise EvaluationError("Paired detection scores must use identical independent gold issues")
        golds[key[:2]] = score["gold_sha256"]
        detected[key] = score
    missing = sorted(expected - scored.keys())
    if require_complete and missing:
        raise EvaluationError("Some planned case/trial/condition outcomes remain unscored")
    rows = []
    for key in sorted(expected):
        case_id, trial, condition = key
        score = scored.get(key)
        row = {"case_id": case_id, "trial_id": trial, "condition": condition, "kind": manifest[case_id]["kind"],
               "group_id": manifest[case_id]["group_id"], "status": "pending_annotation" if score is None else score["availability"],
               "model_mode": score["model_mode"] if score else "unknown", "quality_change": score["quality_change"] if score else None,
               "safe_improvement": score["safe_improvement"] if score else None}
        for phase in ("baseline", "candidate"):
            metrics = score.get(phase) if score else None
            for metric in ("correct_content_coverage", "unsupported_assertion_rate", "correct_units", "answerable_units",
                           "unsupported_assertions", "assertions", "missing_units"):
                row[phase + "_" + metric] = metrics.get(metric) if metrics else None
        changes = score.get("changes") if score else None
        for metric in ("corrected_required_unit_ids", "regressed_required_unit_ids", "deleted_correct_required_unit_ids", "new_unsupported_assertion_ids"):
            row[metric.removesuffix("_ids")] = len(changes[metric]) if changes is not None else None
        resource, measurements = resource_map.get(key, (None, None))
        if resource and not score:
            row["model_mode"] = resource["model_mode"]
        for metric in ("model_calls", "tool_calls", "network_requests", "seconds", "prompt_tokens", "completion_tokens"):
            row[metric] = measurements[metric]["value"] if measurements else None
            row[metric + "_known_lower_bound"] = measurements[metric]["known_lower_bound"] if measurements else None
        row["model_estimated_cost"] = measurements["model_estimated_cost"] if measurements else None
        row["model_cost_known_lower_bound"] = measurements["model_cost_known_lower_bound"] if measurements else None
        row["cost_currency"] = (resource.get("prices") or {}).get("currency") if resource else None
        detection = detected.get(key)
        if detection is not None and not score and not resource:
            row["model_mode"] = detection.get("model_mode", "unknown")
        if detection is not None and (score or resource) and detection.get("model_mode") != row["model_mode"]:
            raise EvaluationError("Execution-mode labels disagree between outcome/detection/resources")
        for metric in ("precision", "recall", "f1", "true_positives", "false_positives", "false_negatives"):
            row["detection_" + metric] = detection[metric] if detection else None
        rows.append(row)
    groups = {}
    for row in rows:
        key = row["kind"], row["condition"], row["model_mode"]
        groups.setdefault(key, []).append(row)
    aggregates = []
    for (kind, condition, mode), members in sorted(groups.items()):
        group = {"kind": kind, "condition": condition, "model_mode": mode, "planned_rows": len(members),
                 "pending_annotations": sum(r["status"] == "pending_annotation" for r in members),
                 "unavailable_candidates": sum(r["status"] in {"unavailable", "missing_revision"} for r in members)}
        for field in ("baseline_correct_content_coverage", "candidate_correct_content_coverage",
                      "candidate_unsupported_assertion_rate", "safe_improvement", "seconds"):
            observed = [r[field] for r in members if r[field] is not None]
            group[field + "_mean"] = sum(observed) / len(observed) if observed else None
            group[field + "_observed_count"] = len(observed)
        for metric in ("model_calls", "tool_calls", "network_requests", "seconds", "prompt_tokens", "completion_tokens"):
            lower = [r[metric + "_known_lower_bound"] for r in members if r[metric + "_known_lower_bound"] is not None]
            group[metric + "_known_lower_bound_sum"] = sum(lower) if lower else None
            group[metric + "_sum"] = sum(r[metric] for r in members) if all(r[metric] is not None for r in members) else None
            group[metric + "_complete_rows"] = sum(r[metric] is not None for r in members)
        group["cost_by_currency"] = []
        for currency in sorted({r["cost_currency"] for r in members if r["cost_currency"] is not None}):
            priced = [r for r in members if r["cost_currency"] == currency]
            lower = [Decimal(r["model_cost_known_lower_bound"]) for r in priced if r["model_cost_known_lower_bound"] is not None]
            complete = len(priced) == len(members) and all(r["model_estimated_cost"] is not None for r in priced)
            group["cost_by_currency"].append({"currency": currency, "priced_rows": len(priced), "complete": complete,
                                             "value": str(sum(Decimal(r["model_estimated_cost"]) for r in priced)) if complete else None,
                                             "known_lower_bound": str(sum(lower)) if lower else None})
        aggregates.append(group)
    # Pair only the SAME trial/case/mode; keep exclusions visible, never compare different survivor sets.
    paired = []
    for kind in ("natural", "injected"):
        eligible = [k for k in {(r["case_id"], r["trial_id"]) for r in plan["schedule"]}
                    if manifest[k[0]]["kind"] == kind]
        deltas, excluded = [], []
        for key in sorted(eligible):
            a, b = scored.get((*key, "generic_review")), scored.get((*key, "structured_critic"))
            if not a or not b or a["model_mode"] != b["model_mode"] or a["candidate"] is None or b["candidate"] is None:
                excluded.append({"case_id": key[0], "trial_id": key[1], "reason": "missing_unavailable_or_mode_mismatch"})
                continue
            av, bv = a["candidate"]["correct_content_coverage"], b["candidate"]["correct_content_coverage"]
            if av is None or bv is None:
                excluded.append({"case_id": key[0], "trial_id": key[1], "reason": "unassessable_or_zero_denominator"})
                continue
            deltas.append({"case_id": key[0], "trial_id": key[1], "model_mode": a["model_mode"], "structured_minus_generic": bv - av})
        for mode in sorted({d["model_mode"] for d in deltas}):
            values = [d for d in deltas if d["model_mode"] == mode]
            paired.append({"kind": kind, "model_mode": mode, "metric": "correct_content_coverage",
                           "paired_cases": values, "mean_structured_minus_generic": sum(d["structured_minus_generic"] for d in values) / len(values),
                           "excluded": excluded})
        if not deltas:
            paired.append({"kind": kind, "model_mode": None, "metric": "correct_content_coverage", "paired_cases": [],
                           "mean_structured_minus_generic": None, "excluded": excluded})
    return _seal({"protocol": "verifin-study-summary-v1", "study_id": plan["study_id"], "plan_sha256": plan["plan_sha256"],
                  "plan_status": plan["status"], "rows": rows, "groups": aggregates, "paired": paired,
                  "missing_outcomes": [{"case_id": a, "trial_id": b, "condition": c} for a, b, c in missing],
                  "annotation_complete": bool(expected) and not missing, "expected_outcomes": len(expected), "scored_outcomes": len(scored),
                  "interpretation": "Descriptive human scores only. Audit execution labels. Fixture runs do not establish model effectiveness; missing/failed cases remain visible."},
                 "summary_sha256")


def _csv(rows):
    output = StringIO(newline="")
    fields = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue()


def export_study_summary(summary, output_dir, *, overwrite=False):
    _check_seal(summary, "summary_sha256")
    if summary.get("protocol") != "verifin-study-summary-v1":
        raise EvaluationError("Unknown study summary protocol")
    files = {"summary.json": _json(summary), "cases.csv": _csv(summary["rows"]),
             "groups.csv": _csv(summary["groups"]), "paired.csv": _csv(summary["paired"])}
    return {"summary_sha256": summary["summary_sha256"], "files": _write_files(output_dir, files, overwrite)}
