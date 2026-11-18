"""Audit saved execution records without claiming semantic application acceptance."""
from __future__ import annotations

from collections import Counter
import hashlib
import json

from .usage import session_usage
from .verification import check_rules


def audit_session(store, session_id):
    context = store.get_context(session_id, report_mode="summaries")
    episodes = store.list_episode_summaries(session_id)
    reports = [store.get_report(session_id, row["version"]) for row in context["reports"]]
    rows, violations = [], []
    modes = set()
    for report in reports:
        version = report["version"]
        modes.add(report.get("model_mode", "unknown"))
        scope = report.get("scope", {})
        snapshot = {**report, "session": {"scope": scope, "scope_version": report.get("scope_version")},
                    "requirements": scope.get("requirements", [])}
        try:
            rules = check_rules(snapshot)
            invalid_structure = None
        except (ValueError, TypeError, KeyError) as exc:
            rules = []
            invalid_structure = type(exc).__name__
        claims = report.get("claims", [])
        supported = {rid for claim in claims if claim.get("status") == "Supported" and not claim.get("stale")
                     for rid in claim.get("requirement_ids", [])}
        required = {r["id"] for r in scope.get("requirements", [])}
        stale = [c["id"] for c in claims if c.get("stale")]
        reasons = []
        if report.get("status") == "complete":
            if not report.get("verification_available"):
                reasons.append("Completed report has unavailable verification")
            if not required or not required <= supported:
                reasons.append("Completed report does not link fresh supported claims to every required item")
            if report.get("unanswered") or report.get("issues") or rules or stale or invalid_structure:
                reasons.append("Completed report contains unresolved items, invalid records or deterministic rule findings")
        if invalid_structure:
            reasons.append("Saved report structure cannot be checked: " + invalid_structure)
        violations.extend({"report_version": version, "reason": reason} for reason in reasons)
        rows.append({"version": version, "episode_id": report.get("episode_id"), "status": report.get("status"),
                     "scope_version": report.get("scope_version"), "model_mode": report.get("model_mode", "unknown"),
                     "rule_findings": rules, "syntactic_required_coverage": len(required & supported) / len(required) if required else None,
                     "stale_claim_ids": stale,
                     "saved_payload_sha256": hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()})
    # Events indicate protocol behavior, not whether a chosen call was useful.
    events = store.events(session_id, exclude_types=["episode_checkpoint"])
    kinds = Counter(e.get("type", "unknown") for e in events)
    return {"protocol": "application-log-audit-v1", "session_id": session_id,
            "automatic_log_checks": "failed" if violations else "passed",
            "semantic_acceptance": "not_assessed", "real_model_behavior_verified": False,
            "model_modes": sorted(modes), "reports": rows, "violations": violations,
            "event_counts": dict(kinds), "usage": session_usage(episodes),
            "current_stale_records": {kind: [r["id"] for r in context[kind] if r.get("stale")]
                                      for kind in ("evidence", "calculations", "claims")},
            "human_checks_remaining": ["Referent and scope resolution", "Correctness and completeness against original sources",
                                       "Unsupported causal explanations and material conflicting evidence",
                                       "Legitimate reuse versus inappropriate stale reuse", "Unseen-issuer generalization"],
            "note": "Saved report snapshots are checked in their own historical scope. Passing these checks does not validate model reasoning or claim truth."}
