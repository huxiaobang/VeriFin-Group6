"""Decisions supported by the current turn's source records."""

from .cashflow_delivery import answer_parts


def margin_draft_check(context):
    """Locate a reconciliation for this question, issuer and fiscal quarter."""
    from . import operating_margin

    tool = "reconcile_operating_margin"
    if not any(t["name"] == tool for t in context["tools"]):
        return None
    episode = context["episode"]
    question = " ".join(context.get("request_parts", {}).values()) or episode["request"]
    entities = context["scope"].get("entities", [])
    focus = context.get("followup_focus", {})
    expense_followup = focus.get("aspect") == "expense_contribution" and entities == [focus.get("entity")]
    if (len(entities) != 1 or not (expense_followup or operating_margin.requested(question)
            and operating_margin.ATTRIBUTION.search(question))
            or operating_margin.NONCONSOLIDATED.search(question) or operating_margin.OTHER_PERIOD.search(question)):
        return None
    entity = entities[0]
    target = episode.get("skill_fiscal_quarter", {})
    if target and target.get("entity") != entity:
        return None
    scope = context["scope"]
    end = target.get("period_end") or scope.get("period_end")
    start = target.get("period_start") or scope.get("period_start")
    if any(target.get(k) and scope.get(k) and target[k] != scope[k] for k in ("period_end","period_start")):
        return None
    if target.get("quarter"):
        # A 13-week issuer can have its fiscal year end in another calendar year.
        # Bind its published fiscal label to the filing's report date.
        ends = {r["document_report_date"] for r in context["evidence"]
                if r.get("kind") == "document" and not r.get("stale") and r.get("entity") == entity
                and r.get("start") == 0 and r.get("document_report_date")
                and operating_margin.headline_label(r["text"], r["document_report_date"]) == target["quarter"]}
        if ends:
            if len(ends) != 1 or (end and end not in ends):
                return None
            end = next(iter(ends))
    if not end:
        return None
    args = {"entity":entity, "period_end":end}
    if start:
        args["period_start"] = start
    bridge = operating_margin.margin_reconciliation(context["evidence"], **args)
    if not bridge:
        return None
    result = {"tool":tool, "arguments":args}
    present = {c["id"] for c in context["calculations"] if not c.get("stale")}
    recent = episode.get("recent_tool_results", [])
    for i in reversed(range(len(recent))):
        receipt = recent[i]
        if receipt.get("tool") != tool or not receipt.get("ok"):
            continue
        note = receipt.get("data", {}).get("margin_reconciliation", {})
        if (note.get("status") == bridge["status"] and note.get("summary") == bridge["summary"]
                and note.get("evidence_ids") == bridge["evidence_ids"]
                and note.get("calculation_ids") == bridge["calculation_ids"]
                and present.issuperset(bridge["calculation_ids"])):
            result["receipt_index"] = i
            break
    return result


def cash_draft_instruction(context):
    """Hand off the first draft only for a dated, fully reconciled cash-flow question."""
    episode = context["episode"]
    if (episode.get("draft_attempted") or episode.get("reviewed")
            or "cash-flow-bridge" not in (episode.get("research_skills") or [])
            or episode.get("skill_selection_source") != "planner"):
        return None
    history = episode.get("recent_tool_results", [])
    last = history[-1] if history else {}
    scope = context["scope"]
    if (last.get("tool") != "analyze_disclosures" or not last.get("ok")
            or last.get("scope_version") != context["session"]["scope_version"]
            or scope.get("entities") != [last.get("arguments", {}).get("entity")]
            or not scope.get("period_start") or not scope.get("period_end")):
        return None
    data = last.get("data", {})
    skipped = {r["skill"] for r in data.get("skill_assessments", []) if r.get("status") == "not_applicable"}
    if set(episode["research_skills"]) - skipped != {"cash-flow-bridge"}:
        return None
    notes = data.get("skill_notes", [])
    if data.get("skill_gaps") or not any(n.get("kind") == "cash_reconciliation" and n.get("complete") for n in notes):
        return None
    evidence = set(last.get("evidence_ids", []))
    calculations = set(last.get("calculation_ids", []))
    for note in notes:
        if (note.get("period_start") != scope["period_start"] or note.get("period_end") != scope["period_end"]
                or not set(note.get("evidence_ids", [])).issubset(evidence)
                or not set(note.get("calculation_ids", [])).issubset(calculations)):
            return None
    parts = context.get("request_parts") or {"request": episode["request"]}
    requirements = {r["id"]: r["text"] for r in context["requirements"]}
    for questions in (parts, requirements):
        matched = answer_parts(questions, notes, scope, [])
        if (not questions or set(matched) != set(questions)
                or any(r["status"] != "source_excerpt" or not r["calculation_ids"] for r in matched.values())):
            return None
    reason = last.get("planner_instruction") or ""
    return (reason + "\nUse the current turn's reconciled cash-flow calculation and cited rows to draft "
            "the requested difference and adjustments. Keep the original checklist and evidence limits.").strip()


def covered_document_read(context, returned):
    """Does a successful exact read only repeat visible source text and unchanged cells?"""
    data = returned.get("data", {})
    if (not returned.get("ok") or data.get("numeric_operand_status") not in {"text_only", "attached"}
            or data.get("numeric_extraction_unavailable") or returned.get("calculations")):
        return False
    visible = [r for r in context["evidence"] if not r.get("stale")]
    known = {r["id"]: r for r in visible}
    spans, cells = [], set()
    for row in returned.get("evidence", []):
        if _has_span(row):
            spans.append(row)
        elif row.get("cell_type") and row.get("id") in known and "value" in row:
            if any(known[row["id"]].get(k) != v for k, v in row.items()
                   if k not in {"retrieved_at", "retrieval_score"}):
                return False
            cells.add(row["id"])
        else:
            return False
    if (not spans or cells != set(data.get("numeric_operand_ids", []))
            or bool(cells) != (data["numeric_operand_status"] == "attached")):
        return False
    intervals = {}
    for row in visible:
        if _has_span(row):
            intervals.setdefault(_source(row), []).append((row["start"], row["end"]))
    for row in spans:
        covered = row["start"]
        for start, end in sorted(intervals.get(_source(row), [])):
            if start > covered:
                break
            covered = max(covered, end)
        if covered < row["end"]:
            return False
    return True


def covered_statement_read_gap(context, returned):
    """A new text window may still be entirely inside passages already retrieved this turn."""
    if returned.get("data", {}).get("numeric_operand_status") != "text_only":
        return None
    recent = context["episode"].get("recent_tool_results", [])
    version = context["session"]["scope_version"]
    analysis = next((r for r in reversed(recent) if r.get("tool") == "analyze_disclosures"), {})
    gaps = analysis.get("data", {}).get("skill_gaps", [])
    assessed = {row["skill"] for row in analysis.get("data", {}).get("skill_assessments", []) if row.get("skill")}
    selected = set(context["episode"].get("research_skills", []))
    # Legacy cash receipts predate per-method assessments.
    same_methods = bool(assessed and assessed <= selected) or (not assessed and selected == {"cash-flow-bridge"})
    if not gaps or not analysis.get("ok") or analysis.get("scope_version") != version or not same_methods:
        return None
    prior = {ref for r in recent if r.get("tool") in {"read_document", "search_documents"}
             and r.get("ok") and r.get("scope_version") == version for ref in r.get("evidence_ids", [])}
    rows = [r for r in context["evidence"] if r["id"] in prior and r.get("entity") == analysis.get("arguments", {}).get("entity")]
    if not covered_document_read({"evidence": rows}, returned):
        return None
    return "本次阅读只返回了本轮已经取到的原文，没有新增金额记录。" + " ".join(gaps)


def repeated_statement_gap(context, arguments, returned):
    """Two completed inspections of the same disclosures and methods left exactly the same gaps."""
    data = returned.get("data", {})
    if not returned.get("ok") or not data.get("skill_gaps") or not data.get("skill_assessments"):
        return None
    version = context["session"]["scope_version"]
    fields = ("inspected_documents", "skill_assessments", "skill_notes", "skill_gaps")
    for receipt in reversed(context["episode"].get("recent_tool_results", [])):
        if receipt.get("tool") != "analyze_disclosures" or not receipt.get("ok") or receipt.get("scope_version") != version:
            continue
        if receipt.get("arguments", {}).get("entity") != arguments.get("entity"):
            continue
        if not all(receipt.get("data", {}).get(key) == data.get(key) for key in fields):
            return None
        for records, id_field in (("evidence", "evidence_ids"), ("calculations", "calculation_ids")):
            if {r["id"] for r in returned.get(records, [])} != set(receipt.get(id_field, [])):
                return None
            # Reusing an ID with an updated amount or provenance is genuine progress.
            current = {r["id"]:r for r in context.get(records, []) if not r.get("stale")}
            if any(any(current.get(r["id"], {}).get(k) != v for k, v in r.items()
                       if k not in {"retrieved_at", "source_observed_at"}) for r in returned.get(records, [])):
                return None
        return "重复核对同一批披露未增加证据或计算，仍缺：" + " ".join(data["skill_gaps"])
    return None


def _source(row):
    return (row["entity"], row["document_id"], row["snapshot_hash"], row["text_snapshot_hash"])


def _has_span(row):
    return (row.get("kind") == "document" and all(row.get(k) for k in
            ("entity", "document_id", "snapshot_hash", "text_snapshot_hash"))
            and type(row.get("start")) is int and type(row.get("end")) is int
            and 0 <= row["start"] < row["end"] and isinstance(row.get("text"), str)
            and len(row["text"]) == row["end"] - row["start"])
