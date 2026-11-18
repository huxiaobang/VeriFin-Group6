"""Bind management's explanation to the saved quarter, financial row and source quote."""
from .data import DataError, available_by


def _source_quote(finding, record, scope, period):
    if not record or record.get("kind") != "document" or record.get("stale"):
        return None
    if (record.get("entity") not in scope.get("entities", [])
            or record.get("document_report_date") != period["end"]
            or not all(record.get(key) for key in ("locator", "source_url", "snapshot_hash"))):
        return None
    try:
        if not available_by(record.get("published_at"), scope.get("cutoff")):
            return None
    except DataError:
        return None
    quote = finding.get("quote")
    start, end, origin = finding.get("quote_start"), finding.get("quote_end"), record.get("start")
    if not isinstance(quote, str) or not quote.strip() or not all(type(i) is int for i in (start, end, origin)):
        return None
    text = record.get("text")
    if not isinstance(text, str) or not 0 <= start-origin < end-origin <= len(text) or text[start-origin:end-origin] != quote:
        return None
    locator = record["locator"]
    if record.get("text_snapshot_path"):
        locator = f"{record['text_snapshot_path']}:characters[{start}:{end}]"
    return {"quote": quote, "evidence_id": record["id"], "source_url": record["source_url"],
            "locator": locator, "quote_start": start, "quote_end": end}


def income_reason_view(report, bridge):
    """Return findings/gaps for this verified table, independent of model verdicts."""
    receipt = report.get("income_explanation") or {}
    if not receipt or not bridge:
        return None
    if (receipt.get("episode_id") != report.get("episode_id")
            or receipt.get("scope_version") != report.get("scope_version")
            or any(receipt.get(k) != bridge.get(k) for k in ("current_period", "prior_period"))):
        return None
    rows = {row["contribution_id"]: row for row in bridge["rows"] if row.get("contribution_id")}
    evidence = {row["id"]: row for row in report.get("evidence", [])}
    pins = report.get("record_versions", {}).get("evidence")
    findings, gaps, seen = [], [], set()
    for target in receipt.get("targets", []):
        ref = target.get("contribution_id")
        row = rows.get(ref)
        if not row or row["metric"] != target.get("metric") or ref in seen:
            continue
        seen.add(ref)
        label = row.get("display_label") or row["label"]
        matched = []
        for finding in receipt.get("findings", []):
            if finding.get("metric") != row["metric"] or finding.get("contribution_id") != ref:
                continue
            source = evidence.get(finding.get("evidence_id"))
            if not source or (pins is not None and pins.get(source["id"]) != source.get("version", 1)):
                continue
            excerpt = _source_quote(finding, source, report["scope"], bridge["current_period"])
            kind = finding.get("support_kind", "direct")
            if kind not in {"direct", "component_context"}:
                continue
            if kind == "component_context" and not finding.get("subject_label"):
                continue
            if excerpt:
                excerpt.update(support_kind=kind, subject_label=finding.get("subject_label") or label)
            if excerpt and excerpt not in matched:
                matched.append(excerpt)
        if matched:
            findings.extend({**s, "display_label": label} for s in matched)
            if not any(s["support_kind"] == "direct" for s in matched):
                gaps.append(f"这些披露只覆盖相关项目，仍缺少管理层对本季{label}整体同比变化的直接解释。")
        else:
            gaps.append(f"尚缺少管理层对本季{label}同比变化原因的可核验说明。")
    if not seen:
        return None
    return {"findings": findings, "gaps": gaps,
            "evidence_ids": list(dict.fromkeys(f["evidence_id"] for f in findings))}


def attach_offline_reasons(report, answers):
    bridge = report.get("income_bridge") or {}
    if not report.get("income_explanation") or not bridge:
        return
    from .answer_sections import income_table
    if not income_table(report, bridge):
        return
    reasons = income_reason_view(report, bridge)
    if not reasons:
        return
    quotations = []
    for item in reasons["findings"]:
        if item["support_kind"] == "component_context":
            attribution = f"关于与{item['display_label']}相关的{item['subject_label']}，管理层写道"
        else:
            attribution = f"管理层在{item['display_label']}的同比讨论中写道"
        quotations.append(f"{attribution}：“{item['quote']}”")
    if quotations:
        quotations.append("各行的同比金额来自损益表。把其中多少金额归于某个因素，需要公司另行量化披露。")
    for part in bridge.get("part_ids", []):
        answer = answers.get(part)
        if not answer or answer["status"] not in {"calculated", "source_excerpt", "partial"}:
            continue
        if quotations:
            answer["answer_sentence"] += " " + " ".join(quotations)
            answer["evidence_ids"] = list(dict.fromkeys([*answer["evidence_ids"], *reasons["evidence_ids"]]))
        if reasons["gaps"]:
            answer["status"] = "partial"
            answer["missing_support"] = " ".join(filter(None, [answer.get("missing_support"), *reasons["gaps"]]))
