"""Lay out related answers together while keeping each coverage judgement intact."""
from decimal import Decimal
import re
from .request_review import is_driver_restatement
from .income_reason_delivery import income_reason_view


_EXPLANATION = re.compile(
    r"原因|怎么回事|为什么|为何|怎么会|解释|差在|差异|怎么对|怎么从|如何从|拆(?:开|解)|"
    r"哪些.{0,10}(?:影响|贡献|抵消|驱动)|\b(?:why|explain|reconcile|bridge|diverg\w*|"
    r"drivers?|what (?:drove|caused|explains|happened))\b", re.I)


def _fragments(answer):
    if answer.get("answer_fragments"):
        return answer["answer_fragments"]
    text = answer.get("answer_sentence")
    if answer.get("claim_id") and text:
        return [{"claim_ids":[answer["claim_id"]], "text":text}]
    return []


def _share_answer(first, second, bridge_parts):
    """A reference to the preceding explanation needs the same verified material."""
    if first["status"] != second["status"] or first.get("missing_support") != second.get("missing_support"):
        return False
    if not is_driver_restatement(second["question"]) or not _EXPLANATION.search(first["question"]):
        return False
    if first["part"] in bridge_parts and second["part"] in bridge_parts:
        return first["status"] in {"calculated", "answered", "partial"}
    if first["status"] != "answered":
        return False
    def referenced_claims(answer):
        return {cid for fragment in _fragments(answer) for cid in fragment["claim_ids"]}
    a, b = referenced_claims(first), referenced_claims(second)
    return bool(a and b and (a == b or len(a & b) >= 2))


def _join_answers(group):
    section = dict(group[0])
    section["parts"] = [row["part"] for row in group]
    if len(group) == 1:
        return section
    section["question"] = " ".join(row["question"] for row in group)
    combined = []
    for row in group:
        for piece in _fragments(row):
            existing = next((i for i,p in enumerate(combined) if p["claim_ids"] == piece["claim_ids"]
                             and (p["text"] in piece["text"] or piece["text"] in p["text"])), None)
            if existing is None:
                combined.append(piece)
            elif len(piece["text"]) > len(combined[existing]["text"]):
                combined[existing] = piece
    if combined:
        section["answer_fragments"] = combined
        section["answer_text"] = " ".join(p["text"] for p in combined)
    for key in ("evidence_ids","calculation_ids"):
        refs = list(dict.fromkeys(ref for row in group for ref in row.get(key,[])))
        if refs:
            section[key] = refs
    return section


def _omit_repeated_support(section, primary_ids, shown, supported_ids):
    """Keep each direct answer. Print identical verified supplementary text once."""
    fragments = _fragments(section)
    kept = []
    for fragment in fragments:
        ids = fragment["claim_ids"]
        key = (tuple(ids), fragment["text"])
        if key in shown and len(ids) == 1 and ids[0] in supported_ids and ids[0] not in primary_ids:
            continue
        kept.append(fragment)
    if kept and len(kept) != len(fragments):
        section["answer_fragments"] = kept
        section["answer_text"] = "".join(("" if i == 0 else "\n\n" if p.get("new_paragraph") else " ")
                                         + p["text"] for i,p in enumerate(kept))
    shown.update((tuple(p["claim_ids"]), p["text"]) for p in kept)


def _number(value):
    if type(value) not in (int, float):
        return None
    result = Decimal(str(value))
    return result if result.is_finite() else None


def _fmt(value, scale, signed=False):
    if value is None:
        return "—"
    txt = format(value / scale, ",f")
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return "+" + txt if signed and value > 0 else txt


def income_table(report, bridge):
    """Resolve amounts and signed differences from this report's pinned records."""
    scope = report.get("scope") or {}
    entities = scope.get("entities", [])
    currency = bridge.get("currency")
    url, table = bridge.get("source_url"), bridge.get("table_locator")
    if (len(entities) != 1 or not isinstance(currency,str) or not currency or not url or not table
            or bridge.get("scope_version",report.get("scope_version")) != report.get("scope_version")
            or bridge.get("episode_id",report.get("episode_id")) != report.get("episode_id")):
        return None
    periods = [bridge.get(k) for k in ("current_period", "prior_period")]
    if not all(isinstance(period, dict) and period.get("start") and period.get("end") for period in periods):
        return None
    if periods[0]["end"] <= periods[1]["end"]:
        return None
    if any(scope.get(field) and scope[field]!=periods[0][key]
           for field,key in (("period_start","start"),("period_end","end"))):
        return None
    target = report.get("skill_fiscal_quarter") or {}
    if any(target.get(field) and target[field]!=periods[0][key]
           for field,key in (("period_start","start"),("period_end","end"))):
        return None
    def current(kind, group):
        pinned = report.get("record_versions",{}).get(kind)
        return {r["id"]:r for r in report.get(group,[]) if r.get("entity")==entities[0]
                and not r.get("stale") and (pinned is None or pinned.get(r["id"])==r.get("version",1))}
    evidence = current("evidence","evidence")
    calculations = current("calculation","calculations")
    unit = "万美元" if currency=="USD" else currency
    scale = Decimal(10000) if currency=="USD" else Decimal(1)
    lines, traces, contributions, effects, net, net_id = [], [], [], [], None, None
    parts_total = Decimal(0)
    for row in bridge.get("rows",[]):
        if not isinstance(row,dict) or row.get("role") not in {"operating","component","subtotal","net"}:
            return None
        curr, prior = row.get("current_id"), row.get("prior_id")
        values = []
        for ref, period in zip((curr,prior),periods):
            if ref is None:
                values.append(None)
                continue
            cell = evidence.get(ref,{})
            if (cell.get("source_url")!=url or cell.get("table_xpath")!=table or cell.get("metric")!=row.get("metric")
                    or cell.get("currency")!=currency or cell.get("unit")!=currency
                    or cell.get("period_start")!=period["start"] or cell.get("period_end")!=period["end"]
                    or not cell.get("locator") or not cell.get("snapshot_hash")
                    or bridge.get("snapshot_hash",cell["snapshot_hash"])!=cell["snapshot_hash"]
                    or _number(cell.get("value")) is None):
                return None
            values.append(_number(cell["value"]))
        diff = None
        calc_ref = row.get("contribution_id")
        if calc_ref is not None:
            calc = calculations.get(calc_ref,{})
            sign = row.get("sign")
            expected = [curr,prior] if sign==1 else [prior,curr]
            if (type(sign) is not int or sign not in (-1,1) or None in values
                    or calc.get("operation")!="difference" or calc.get("operand_ids")!=expected
                    or calc.get("unit")!=currency):
                return None
            versions = calc.get("operand_versions") or {}
            if any(ref in versions and evidence[ref].get("version",1)!=versions[ref] for ref in expected):
                return None
            diff = (values[0]-values[1])*sign
            if _number(calc.get("value"))!=diff:
                return None
            if row["role"] in {"operating","component"}:
                contributions.append(calc_ref)
                parts_total += diff
            elif row["role"]=="net":
                net, net_id = diff, calc_ref
        label = row.get("display_label") or row.get("label")
        if not isinstance(label,str) or not label:
            return None
        if diff is not None and row["role"] in {"component","operating"}:
            effects.append((label,diff))
        role_label = {"subtotal":"（小计）","net":"（合计）"}.get(row["role"],"")
        lines.append([label+role_label,_fmt(values[1],scale),_fmt(values[0],scale),_fmt(diff,scale,True)])
        traces.append({"label":label,"current_id":curr,"prior_id":prior,"contribution_id":calc_ref})
    if not lines:
        return None
    if bridge.get("complete"):
        total = calculations.get(bridge.get("sum_id"),{})
        if (not contributions or _number(total.get("value"))!=net or net is None or net!=parts_total
                or total.get("operation")!="sum" or sorted(total.get("operand_ids",[]))!=sorted(contributions)
                or total.get("unit")!=currency or net_id!=bridge.get("total_id")
                or any(r[3]=="—" for r in lines)):
            return None
        if any(ref in total.get("operand_versions",{}) and total["operand_versions"][ref]!=calculations[ref].get("version",1)
               for ref in contributions):
            return None
    headers = ["项目",periods[1]["end"]+" 上年同期",periods[0]["end"]+" 本季","同比影响"]
    summary = ""
    if net is not None:
        if net:
            summary = f"净利润同比{'增加' if net>0 else '减少'} {_fmt(abs(net),scale)} {unit}。"
        else:
            summary = "净利润与上年同期持平。"
    # One concise explanation for both periods. Smaller rows remain visible in the table.
    if effects:
        ranked = sorted(effects, key=lambda e:-abs(e[1]))
        effects_text = "，".join(f"{label}变动使净利润{'增加' if diff>0 else '减少'} {_fmt(abs(diff),scale)} {unit}"
                               for label,diff in ranked[:4] if diff)
        if effects_text:
            summary += effects_text+"。"
    if not bridge.get("complete"):
        summary += "现有明细只能解释部分变动。"
    return {"headers":headers, "rows":lines, "trace":traces, "unit":unit, "source_url":url, "locator":table,
            "summary":summary, "complete":bool(bridge.get("complete")), "gaps":list(bridge.get("gaps") or [])}


def answer_sections(report, answers):
    """Presentation only. The report's individual questions retain their verdicts."""
    bridge = report.get("income_bridge") or {}
    table = income_table(report,bridge) if isinstance(bridge,dict) and bridge else None
    bridge_parts = set(bridge.get("part_ids") or []) if table else set()
    if table and len(answers)==1 and not bridge_parts:
        bridge_parts.add(answers[0]["part"])
    groups = []
    for answer in answers:
        if groups and _share_answer(groups[-1][-1], answer, bridge_parts):
            groups[-1].append(answer)
        else:
            groups.append([answer])
    sections = []
    shown = set()
    supported = {c["id"] for c in report.get("claims",[]) if c.get("status") == "Supported"}
    for group in groups:
        section = _join_answers(group)
        if table and bridge_parts.intersection(section["parts"]):
            section["source_table"] = table
            reasons = income_reason_view(report, bridge)
            if reasons and reasons["findings"]:
                if report.get("verification_available"):
                    claim_ids = {cid for row in group for frag in _fragments(row) for cid in frag["claim_ids"]}
                    claim_ids.update(row.get("claim_id") for row in group)
                    cited = {ref for c in report.get("claims", []) if c["id"] in claim_ids and not c.get("stale")
                             and c["status"] == "Supported" for ref in c.get("evidence_ids", [])}
                else:
                    cited = set(section.get("evidence_ids", []))
                section["attribution_sources"] = [f for f in reasons["findings"] if f["evidence_id"] in cited]
            if all(row["status"]=="answered" for row in group):
                # Keep the Critic's chosen explanations, including qualifications
                # from management. The table already supplies the two-period amounts.
                texts = [r["answer_sentence"] for r in group if r.get("answer_sentence")]
                for fragment in section.get("answer_fragments",[]):
                    text = fragment["text"]
                    if (re.search(r"管理层|公司(?:称|解释|表示)|management|the company (?:said|attributed)",text,re.I)
                            and not any(text in old for old in texts)):
                        texts.append(text)
                if texts:
                    section["answer_text"] = " ".join(dict.fromkeys(texts))
            table = None
        elif report.get("verification_available") and section["status"] == "answered":
            _omit_repeated_support(section, {r.get("claim_id") for r in group}, shown, supported)
        sections.append(section)
    return sections
