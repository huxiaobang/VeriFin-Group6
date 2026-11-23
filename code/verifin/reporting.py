"""Inspectable report exports. Source content is never interpreted as code."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from urllib.parse import urlparse


def md_text(value):
    text = str(value if value is not None else "")
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")


def source_link(value):
    value = str(value or "")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return md_text(value or "source unavailable")
    # Angle-bracket links avoid parenthesis problems; strip control delimiters.
    return "[source](<" + value.replace("<", "%3C").replace(">", "%3E").replace("\n", "") + ">)"


_STAGE_LABELS = {
    "no_draft": "尚未形成草稿",
    "draft_unverified": "草稿尚未经核验",
    "critic_receipt_invalid": "核验回执无法采用",
    "unknown": "中断阶段未确认",
}


def _delivery_stage(report):
    if report.get("verification_available"):
        return "reviewed"
    if report.get("model_mode") == "offline_tools_only":
        return "offline_tools_only"
    stage = report.get("delivery_stage")
    return stage if stage in _STAGE_LABELS else "unknown"


def _stage_message(report, stage):
    if report.get("stop_reason") == "unknown_external_outcome":
        return ("An external call was started before the interruption. Its outcome is unknown. "
                "The request stopped without repeating the call. Any completed work is available below.")
    if report.get("stop_reason") == "local_context_limit" and report.get("context_limit"):
        detail = report["context_limit"]
        return (f"{detail['role']} 的待发送材料去重后仍有 {detail['characters']:,} 个字符，"
                f"超过本地上限 {detail['limit']:,}。这次请求没有发出，已取得的证据仍可查阅。"
                "可在请求设置中提高字符上限，再提交原问题。发送更多材料可能增加输入费用。")
    if report.get("stop_reason") == "spend_budget_exhausted":
        if stage == "no_draft":
            return "剩余 API 预算不足以预留下一次请求的费用，本轮在起草前停止。"
        if stage == "draft_unverified":
            return "草稿已保存。剩余 API 预算不足以预留下一次请求的费用，当前草稿尚未完成核验。"
    if stage == "no_draft":
        if report.get("stop_reason") == "latest_filing_unavailable":
            return report.get("skill_gap", "最新 SEC 申报尚未核实。")
        error = report.get("researcher_output_error")
        if error == "max_output_tokens":
            return "供应商报告 Researcher 达到输出上限，尚未形成完整草稿。"
        if error in {"invalid_researcher_json", "empty_researcher_output"}:
            return "Researcher 返回空内容。" if error == "empty_researcher_output" else "Researcher 返回的草稿无法解析为 JSON。"
        if report.get("stop_reason") == "researcher_no_valid_references":
            return "Researcher 已返回草稿。每条主张都含有无法对应到本轮记录的引用，系统已保留草稿原值和错误记录，本版没有可送审的主张。"
        if report.get("stop_reason") == "repeated_retrieval_without_new_evidence":
            return "重复检索未取得新证据，研究停在取证阶段，尚未形成草稿。"
        fault = report.get("tool_fault") or {}
        if fault.get("kind") == "market_window":
            message = fault["reason"]
            if report.get("evidence"):
                message += "已取得的来源仍可查阅。"
        elif (fault.get("tool") == "reconcile_operating_margin"
              and str(fault.get("reason","")).startswith("A dated analysis cutoff is required")):
            message = ("本轮尚未形成草稿。同比比较需要明确的信息截止日。请设置研究范围的截止日，"
                       "或提供原问所用的截止日后继续。财季期末和公告发布日期各有不同含义。")
        elif report.get("source_coverage_gap"):
            message = report["source_coverage_gap"]
        elif fault.get("reason"):
            message = f"本轮尚未形成草稿。最后一次取证遇到的问题（{fault['tool']}）：{fault['reason']}"
        else:
            message = "本轮在形成草稿前停止。"
        if planner := report.get("planner_error"):
            code = planner["diagnostic_code"]
            detail = "无可进一步分类的错误信息" if code == "model_error_unclassified" else code
            message += f" Planner 调用失败（Activity #{planner['event_sequence']}，{planner['error_type']}，{detail}）。"
        return message
    if stage == "draft_unverified":
        if report.get("critic_output_error") == "max_output_tokens":
            limit = report.get("critic_max_output_tokens")
            detail = f"（{limit:,} token）" if type(limit) is int else ""
            return f"供应商报告 Critic 达到输出上限{detail}。草稿已保存，尚未取得完整核验结论。"
        if report.get("stop_reason") == "time_budget_exhausted":
            return "本轮已保存草稿。时间预算已用完，尚未完成 Critic 核验。"
        if report.get("stop_reason") == "model_budget_exhausted":
            return "本轮已保存草稿。模型调用预算已用完，尚未完成 Critic 核验。"
        if report.get("stop_reason") == "deterministic_check_requires_inspection":
            return "本轮已保存草稿。本地规则检查要求复核，流程在把这份草稿交给 Critic 前停止。"
        return "本轮已形成草稿，尚无可用于本版的 Critic 核验结论。"
    if stage == "critic_receipt_invalid":
        reason = {
            "invalid_critic_json": "回执文本无法解析为有效的 JSON。",
            "unknown_reference": "回执引用了无法对应到本轮材料的记录。",
            "unknown_issue_target": "回执中的问题未能对应到本轮主张或要求。",
            "unknown_issue_category": "回执中的问题类别无法识别。",
            "review_coverage_mismatch": "回执覆盖的主张或要求与本轮送审内容不一致。",
            "missing_or_invalid_reviewed_claim_ids": "回执缺少有效的已审主张清单。",
            "missing_or_invalid_reviewed_requirement_ids": "回执缺少有效的已审要求清单。",
            "missing_or_invalid_critic_issues": "回执的问题清单缺失或格式有误。",
        }.get(report.get("critic_receipt_error"), "回执未通过格式或内容校验。")
        return "Critic 已返回核验回执。" + reason + "本版无法采用该回执作出结论。"
    if stage == "unknown":
        if report.get("claims"):
            return "本版保留了草稿主张，现有记录不足以确认 Critic 的回执情况。"
        return "本版缺少足以确定中断阶段的记录，无法确认本轮最后完成了哪一步。"
    return ""


def _pending_question(report, part, question, stage):
    row = {"part": part, "status": stage if stage in _STAGE_LABELS and stage != "unknown" else "pending",
           "delivery_stage": stage}
    if stage == "no_draft":
        message = "本题没有已保存的草稿可供核验。"
    elif stage in {"draft_unverified", "critic_receipt_invalid"} or (stage == "unknown" and report.get("claims")):
        from .request_review import same_research_question
        requirement_ids = {r["id"] for r in report.get("scope", {}).get("requirements", [])
                           if same_research_question(r["text"], question)}
        drafts = [c["id"] for c in report.get("claims", []) if not c.get("stale")
                  and requirement_ids.intersection(c.get("requirement_ids", []))]
        if drafts:
            row["draft_claim_ids"] = drafts
            message = "草稿关联了本题，尚无可交付的核验答案。"
        elif report.get("claims") or stage == "draft_unverified":
            message = "本轮已有草稿，记录中没有本题与具体草稿的对应关系。"
        else:
            message = "本题没有可交付的核验结论。"
    elif stage == "offline_tools_only":
        message = "本题尚无可引用的离线结果。"
    else:
        message = "现有记录不足以确认本题是否已起草或送审。"
    return {**row, "status_label": _STAGE_LABELS.get(stage, "尚无离线结果"), "stage_message": message}


def question_answers(report):
    """Join this report's questions to answers the saved review actually supports."""
    from .request_review import report_coverage, request_parts
    stage = _delivery_stage(report)
    parts = report.get("request_parts", {})
    single_offline_answer = (stage == "offline_tools_only"
                             and "part-1" in (report.get("offline_part_findings") or {}))
    if not parts and (stage in _STAGE_LABELS or single_offline_answer) and report.get("request"):
        parts = request_parts(report["request"]) or {"part-1": report["request"]}
    coverage = report_coverage(parts, report.get("request_coverage", []), report.get("claims", []),
                               reviewed=report.get("verification_available", False),
                               requirements=report.get("scope",{}).get("requirements",[]),
                               unanswered_ids={r["id"] for r in report.get("unanswered",[])}, findings=report.get("issues", []))
    by_part = {row["part"]: row for row in coverage}
    # Offline lookups can locate passages and describe gaps. They establish
    # neither a Critic verdict nor a licence to reuse evidence from another report.
    if report.get("model_mode") == "offline_tools_only" and not report.get("verification_available"):
        target = report.get("skill_fiscal_quarter") or {}
        def valid_records(refs, group, kind):
            if not isinstance(refs, list):
                return False
            records = {r["id"]: r for r in report.get(group, []) if not r.get("stale")}
            versions = report.get("record_versions", {}).get(kind)
            return all(isinstance(ref, str) and ref in records
                       and (not target.get("entity") or records[ref].get("entity") == target["entity"])
                       and (versions is None or (ref in versions and records[ref].get("version") == versions[ref]))
                       for ref in refs)
        for key, item in (report.get("offline_part_findings") or {}).items():
            if key not in parts or not isinstance(item, dict):
                continue
            status = item.get("status")
            field = {"gap": "missing_support", "source_excerpt": "answer_sentence", "partial": "answer_sentence"}.get(status)
            refs = item.get("evidence_ids", [])
            calculations = item.get("calculation_ids", [])
            if (not field or not isinstance(item.get(field), str) or not item[field].strip()
                    or (target.get("quarter") and item.get("quarter") != target["quarter"])
                    or (status in {"source_excerpt", "partial"} and not refs)
                    or (status == "partial" and not (isinstance(item.get("missing_support"), str) and item["missing_support"].strip()))
                    or not valid_records(refs, "evidence", "evidence")
                    or not valid_records(calculations, "calculations", "calculation")):
                continue
            by_part[key] = {"part": key, "status": status, field: item[field],
                            "evidence_ids": refs, "quarter": item.get("quarter")}
            if status == "partial":
                by_part[key]["missing_support"] = item["missing_support"]
            if calculations:
                by_part[key]["calculation_ids"] = calculations
                if status == "source_excerpt":
                    by_part[key]["status"] = "calculated"
        if report.get("income_explanation"):
            from .income_reason_delivery import attach_offline_reasons
            attach_offline_reasons(report, by_part)
    from .answer_delivery import expand_answers
    if (lookup := report.get("archived_source")) and by_part.get("part-1", {}).get("status") == "source_excerpt":
        by_part["part-1"].update(status_label="已保存的原始出处", archived_source_version=lookup["report_version"],
                                archived_source_claim_ids=lookup["claim_ids"])
    return expand_answers(report, [dict(by_part.get(key) or _pending_question(report, key, question, stage), question=question)
                                   for key, question in parts.items()])


def background_claim_ids(report, answers=None):
    """Preserve verified context separately when it does not answer any reviewed question."""
    if not report.get("verification_available") or not report.get("request_coverage"):
        return []
    answers = question_answers(report) if answers is None else answers
    if not any(row["status"] == "gap" for row in answers):
        return []
    answered = {row["claim_id"] for row in answers if row["status"] == "answered"}
    answered.update(cid for row in answers for fragment in row.get("answer_fragments", []) for cid in fragment["claim_ids"])
    return [c["id"] for c in report.get("claims", [])
            if c.get("status") == "Supported" and not c.get("stale") and c["id"] not in answered]


def report_view(report):
    from .request_review import coverage_status
    from .answer_sections import answer_sections
    stage = _delivery_stage(report)
    answers = question_answers(report)
    # Rebuild presentation fields even when a caller passes an earlier view.
    derived = {"stage_label", "stage_message", "question_answers", "answer_sections", "background_claim_ids"}
    view = {k: v for k, v in report.items() if k not in derived}
    view["delivery_stage"] = stage
    if stage in _STAGE_LABELS:
        view.update(stage_label=_STAGE_LABELS[stage], stage_message=_stage_message(report, stage))
        if stage == "no_draft" and report.get("stop_reason") == "researcher_no_valid_references":
            view["stage_label"] = "草稿引用未通过检查"
    elif report.get("stop_reason") == "local_context_limit" and report.get("context_limit"):
        view.update(stage_label="后续请求超过字符上限", stage_message=_stage_message(report, stage))
    if answers:
        view.update(coverage_status(report, answers), question_answers=answers, answer_sections=answer_sections(report, answers),
                    background_claim_ids=background_claim_ids(report, answers))
    if report.get("followup_limits") and report.get("verification_available"):
        supported = {c["id"]: c for c in report.get("claims", [])
                     if c.get("status") == "Supported" and not c.get("stale")}
        view["limit_statements"] = [supported[item["claim_id"]]["text"] for item in report["followup_limits"]
                                    if item.get("claim_id") in supported]
    if failure := report.get("codex_failure"):
        view.setdefault("stage_label", "Codex")
        hint = f"{failure['role'].title()}: {failure['hint']}"
        view["stage_message"] = (view.get("stage_message", "") + " " + hint).strip()
    if report.get("issues"):
        issue_labels = {"unknown_issue_category": "本条核验意见的类别无法识别",
                        "unknown_verdict_status": "本条主张的核验标签无法识别",
                        "invalid_claim_receipt": "本条主张的核验回执无效，其余主张分别保留核验结果"}
        view["issues"] = []
        for issue in report["issues"]:
            diagnostic = issue.get("contract_diagnostic") or {}
            label = issue_labels.get(diagnostic.get("code"))
            if label and issue.get("issue_type") == "invalid_record":
                issue = {**issue, "display_type": label}
            view["issues"].append(issue)
    if report.get("model_mode") == "offline_tools_only" and not report.get("claims"):
        # No draft is expected in this mode. Display genuine gaps and preserve
        # the underlying issue log in the saved snapshot.
        provided = {a["question"] for a in answers if a["status"] in {"calculated", "source_excerpt"}}
        provided_ids = {r["id"] for r in report.get("scope", {}).get("requirements", []) if r["text"] in provided}
        if answers and all(a["status"] in {"calculated", "source_excerpt"} for a in answers):
            parent = "request:" + report.get("episode_id", "")
            provided_ids.update(r["id"] for r in report.get("scope", {}).get("requirements", [])
                                if r["id"] == parent and r["text"] == report.get("request"))
        if provided_ids:
            view["unanswered"] = [r for r in report.get("unanswered", []) if r["id"] not in provided_ids]
            view["issues"] = [i for i in view.get("issues", []) if not
                             (i.get("target_id") in provided_ids and i.get("issue_type") == "omission"
                              and i.get("missing_support") == "No candidate claim addresses this required-answer item.")]
    return view


def _markdown_answers(answers):
    if not answers:
        return []
    lines = ["## Answers to your questions", ""]
    for row in answers:
        answer = " ".join(x for x in (row.get("answer_text") or row.get("answer_sentence"), row.get("missing_support"),
                                      row.get("stage_message")) if x)
        ids = ([row["claim_id"]] if row.get("claim_id") else []) + [
            cid for fragment in row.get("answer_fragments",[]) for cid in fragment["claim_ids"]]
        source = " ["+", ".join(map(md_text,dict.fromkeys(ids)))+"]" if ids else ""
        refs = row.get("evidence_ids", []) + row.get("calculation_ids", [])
        if refs and not row.get("source_table"):
            source += " [" + ", ".join(map(md_text, refs)) + "]"
        if row.get("draft_claim_ids"):
            source += " 草稿编号 " + ", ".join(map(md_text, row["draft_claim_ids"]))
        body = "\n\n".join(md_text(p) for p in answer.split("\n\n")) if row.get("answer_fragments") else md_text(answer)
        if "archived_source_version" in row:
            body = "<" + answer + ">"
        lines.extend([f"**{md_text(row['question'])}**", "",
                      f"{md_text(row.get('status_label', row['status']))} — {body}{source}", ""])
        if "archived_source_version" in row:
            lines.extend([f"原报告 v{row['archived_source_version']} 的已核验主张 " +
                          ", ".join(map(md_text, row["archived_source_claim_ids"])), ""])
        if table := row.get("source_table"):
            if table["summary"] not in answer:
                lines.extend([md_text(table["summary"]),""])
            lines.extend([f"原表逐项核算（{md_text(table['unit'])}，小计与净利润不重复相加）", "",
                          "| " + " | ".join(map(md_text,table["headers"])) + " |",
                          "| --- | ---: | ---: | ---: |"])
            lines.extend("| " + " | ".join(map(md_text,values)) + " |" for values in table["rows"])
            lines.extend(["",source_link(table["source_url"])+" · "+md_text(table["locator"]),""])
            lines.extend(["<details><summary>逐项证据与计算编号</summary>",""])
            for item in table["trace"]:
                ids = [item[k] for k in ("current_id","prior_id","contribution_id") if item.get(k)]
                lines.append("- "+md_text(item["label"])+"："+", ".join(map(md_text,ids)))
            for note in row.get("attribution_sources", []):
                lines.append("- "+md_text(note["subject_label"])+"（管理层解释） · "+source_link(note["source_url"])
                             +" · "+md_text(note["evidence_id"])+" · "+md_text(note["locator"]))
            lines.extend(["","</details>",""])
            gaps = [gap for gap in table["gaps"] if gap not in answer]
            if gaps:
                lines.extend([md_text(" ".join(gaps)),""])
    return lines


def _markdown_claims(claims, reviewed):
    lines = []
    for claim in claims:
        status = claim.get("status", "Pending Verification") if reviewed and not claim.get("stale") else "未核验草稿"
        verdict = claim.get("critic_verdict") if reviewed and not claim.get("stale") else None
        delivered = verdict["supported_text"] if status == "Partially Supported" and verdict else claim.get("text", "")
        lines.extend([f"### {md_text(claim['id'])} — {md_text(status)}", "", md_text(delivered), "",
                      "Evidence: " + ", ".join(md_text(x) for x in claim.get("evidence_ids", [])),
                      "Calculations: " + ", ".join(md_text(x) for x in claim.get("calculation_ids", [])), ""])
        if claim.get("display_check"):
            lines.extend([md_text(claim["display_check"]["label"]), ""])
        if verdict:
            lines.extend([md_text(verdict["basis"]), " ".join(md_text(ref) for ref in verdict["evidence_refs"]), ""])
    return lines


def render_markdown(report):
    report = report_view(report)
    answers = report.get("question_answers",[])
    sections = report.get("answer_sections",answers)
    usage = report.get("usage", {})
    lines = ["# VeriFin research report", "", f"**Status:** {md_text(report.get('status', 'partial'))}",
             f"**Stop reason:** {md_text(report.get('stop_reason', 'unknown'))}",
             f"**Request:** {md_text(report.get('request', ''))}",
             f"**Model mode:** {md_text(report.get('model_mode', 'unknown'))}",
             f"**Scope version:** {report.get('scope_version', '?')}", "",
             "Model verification is an assessment against the available evidence, not an independent guarantee.", ""]
    if report.get("request_id"):
        lines.insert(5, f"**Request ID:** {md_text(report['request_id'])}")
    if report.get("stage_message"):
        lines.extend([f"**{md_text(report['stage_label'])}** — {md_text(report['stage_message'])}", ""])
        material = report.get("episode_material_ids") or {}
        if material.get("evidence") or material.get("calculations"):
            lines.extend([f"本轮留存 {len(material.get('evidence', []))} 条证据、"
                          f"{len(material.get('calculations', []))} 项计算。记录编号列于下方。", ""])
    lines.extend(["## Scope", "", "```json", json.dumps(report.get("scope", {}), indent=2, ensure_ascii=False), "```", ""])
    claims = report.get("claims", [])
    if report.get("issuer_conflict"):
        conflict = report["issuer_conflict"]
        proposed = conflict["proposed"] if isinstance(conflict["proposed"], list) else [conflict["proposed"]]
        lines.extend(["The requested issuer scope was " + ", ".join(map(md_text,conflict["user_entities"])) +
                      ". The planner proposed " + ", ".join(map(md_text,proposed)) +
                      " and the controller stopped before applying the decision.", ""])
    if report.get("stop_reason") == "answer_mapping_needs_review":
        lines.extend(["The saved version was marked complete. Its answer links now show an unresolved question.", ""])
    background = set(report.get("background_claim_ids",[]))
    answers_first = bool(background or report.get("stage_message") or any(r.get("source_table") or len(r["parts"])>1 for r in sections))
    if answers_first:
        lines.extend(_markdown_answers(sections))
    primary = [c for c in claims if c["id"] not in background]
    if primary or not answers_first or report.get("research_skills") or report.get("skill_notes") or report.get("skill_gap"):
        lines.extend(["## Findings", ""])
    if report.get("research_skills"):
        lines.extend(["Research skills: " + ", ".join(map(md_text, report["research_skills"])), ""])
    for note in report.get("skill_notes", []):
        refs = note["evidence_ids"] + note["calculation_ids"]
        lines.extend([md_text(note["text"]), "Records: " + ", ".join(map(md_text, refs)), ""])
    if report.get("skill_gap"):
        lines.extend([md_text(report["skill_gap"]), ""])
    if str(report.get("model_mode", "")).startswith("scripted_"):
        lines[2:2] = ["**SCRIPTED FIXTURE DEMONSTRATION — no live model or semantic-effectiveness result.**", ""]
    if not claims and any(a.get("status") in ("calculated", "source_excerpt", "partial", "gap") for a in answers) and report.get("model_mode") == "offline_tools_only":
        lines.extend(["Offline source check; no model verification was run.", ""])
    elif not claims and not report.get("stage_message"):
        lines.append("No verified findings were produced.")
    lines.extend(_markdown_claims(primary, report.get("verification_available")))
    if background:
        lines.extend(["## Supporting background", ""])
        lines.extend(_markdown_claims([c for c in claims if c["id"] in background], True))
    if not answers_first:
        lines.extend(_markdown_answers(sections))
    if report.get("limit_statements"):
        lines.extend(["## 当前资料能确认到哪里", ""])
        lines.extend(md_text(text) for text in report["limit_statements"])
        lines.append("")
    lines.extend(["## Unanswered items and issues", ""])
    gaps = report.get("unanswered", [])
    issues = report.get("issues", [])
    if report.get("tool_fault"):
        fault = report["tool_fault"]
        lines.append(f"- {md_text(fault['tool'])}: {md_text(fault['reason'])}")
    if not gaps and not issues and not report.get("tool_fault"):
        if report.get("model_mode") == "offline_tools_only" and answers and all(
                a["status"] in {"source_excerpt", "calculated"} for a in answers):
            lines.append("Each question has an offline source result. No model review was run.")
        else:
            lines.append("None identified." if report.get("status") == "complete" else "Research ended before completeness was established.")
    for gap in gaps:
        lines.append(f"- {md_text(gap.get('id'))}: {md_text(gap.get('text'))}")
    for issue in issues:
        lines.append(f"- {md_text(issue.get('target_id'))}: {md_text(issue.get('display_type') or issue.get('issue_type'))}. {md_text(issue.get('missing_support'))} Suggested action: {md_text(issue.get('proposed_action'))}")
    lines.extend(["", "## Evidence and source locations", ""])
    cited_here = {ref for row in answers for ref in row.get("evidence_ids", [])}
    evidence = report.get("evidence", [])
    material = report.get("episode_material_ids") or {}
    episode_evidence = set(material.get("evidence", []))
    episode_calculations = set(material.get("calculations", []))
    if cited_here:
        evidence = sorted(evidence, key=lambda r: r["id"] not in cited_here)
        lines.extend(["Source passages cited in this turn:", ""])
    for i, record in enumerate(evidence):
        if cited_here and record["id"] not in cited_here and (i == 0 or evidence[i-1]["id"] in cited_here):
            lines.extend(["", "Other evidence saved in the session (not cited for these answers):", ""])
        origin = "（本轮留存）" if record["id"] in episode_evidence else ""
        lines.extend([f"- **{md_text(record['id'])}**{origin} ({md_text(record.get('entity'))}; {md_text(record.get('period_start', ''))} to {md_text(record.get('period_end', ''))}): {source_link(record.get('source_url'))}",
                      f"  Locator: {md_text(record.get('locator'))}; snapshot SHA-256: {md_text(record.get('snapshot_hash'))}"])
        if record.get("filing_link_provenance"):
            lines.append("  Associated SEC filing " + source_link(record["filing_url"]))
    lines.extend(["", "## Reproducible calculations", ""])
    for record in report.get("calculations", []):
        origin = "（本轮留存）" if record["id"] in episode_calculations else ""
        lines.append(f"- **{md_text(record['id'])}**{origin}: {md_text(record.get('formula'))} = {md_text(record.get('value'))} {md_text(record.get('unit'))}; operands {md_text(', '.join(record.get('operand_ids', [])))}; stale={bool(record.get('stale'))}.")
    lines.extend(["", "## Resource use", "", f"Model attempts: {usage.get('model_calls', 0)}; tool attempts: {usage.get('tool_calls', 0)}; active seconds: {usage.get('active_seconds', 0):.2f}.",
                  "Token usage (when returned by provider): " + md_text(json.dumps({k: v for k, v in usage.items() if k not in {"model_calls", "tool_calls", "active_seconds"}}, ensure_ascii=False)), ""])
    return "\n".join(lines)


def _csv_cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    # Avoid formula execution when a CSV is opened in a spreadsheet.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(records):
    buffer = io.StringIO(newline="")
    fields = sorted({k for record in records for k in record}) or ["id"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for record in records:
        writer.writerow({key: _csv_cell(value) for key, value in record.items()})
    return buffer.getvalue()


def export_report(report, destination, *, display_report=None):
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    markdown = render_markdown(report if display_report is None else display_report)
    for filename, content in [("report.md", markdown), ("report.json", json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"),
                              ("evidence.csv", render_csv(report.get("evidence", []))), ("calculations.csv", render_csv(report.get("calculations", [])))]:
        path = folder / filename
        path.write_text(content, encoding="utf-8")
        paths.append(str(path.resolve()))
    return paths
