"""Identify a quarter's operating-to-net income comparison and bind its tool receipt."""
import re

from .data import available_by
from .financial import duration_class
from .request_review import is_driver_restatement


_OPERATING = re.compile(r"营业(?:利润|盈利|收益|亏损)|经营(?:利润|盈利|收益|亏损)|\boperating (?:earnings|income|profit|loss)", re.I)
_NET = re.compile(r"净(?:利(?:润)?(?!息|差)|盈利|收益|亏损)|税后(?:赚|利|收|盈)|最后.{0,4}赚.{0,4}(?:钱|少|多)|\b(?:net (?:income|earnings|profit|loss)|bottom line)\b", re.I)
_CHANGE = re.compile(r"同比|同期|去年|涨|增|降|跌|下滑|多了|少了|反而|拖|抵消|变化|上升|改善|恶化|year.over.year|prior.year|\b(?:rise|rose|increas\w*|fall|fell|declin\w*|chang\w*)\b", re.I)
_ASK = re.compile(r"为什么|怎么|为何|差[异额在了]|哪些|解释|拆解|影响|原因|驱动|拖累|抵消|对.?账|\b(?:why|how|explain|dr[io]ve\w*|offset\w*|reconcil\w*)\b", re.I)
_OTHER = re.compile(r"现金流|经营现金|每股|利润率|环比|全年|年度|累计|半年|今年以来|剔除|调整后|非.?GAAP|分部|单店|segment|margin|per share|cash flow|adjusted|annual|sequential|year.to.date|forecast|预测",re.I)
_SPECIFIC = re.compile(r"上一季|上个季|前一季|前一财季|上上季|\b(?:previous|prior|last) (?:fiscal )?quarter\b|\bQ[1-4]\b|第[一二三四1234]季|\d{4}[-/]\d",re.I)
EXPLANATION_TOOL = "read_income_explanation"
_WHY = re.compile(r"怎么回事|怎么会|为什么|为何|原因|成因|归因|怎么解释|如何解释|怎么说|如何说明|背后|"
                  r"\b(?:why|how come|explain(?:s|ed|ing)?|reason[s]?|what (?:caused|drove|explains))\b", re.I)
_ONLY_AMOUNTS = re.compile(r"(?:只|仅)(?:要|问|列|算|看|给|报|核对).{0,4}(?:数字|金额|算式|计算)|"
                           r"(?:不用|无需|不必|不要)(?:再)?(?:解释|分析原因)|(?:numbers|amounts) only", re.I)


def wants_explanation(request, parts=None, part_ids=None):
    comparison = " ".join(parts[k] for k in part_ids if k in parts) if parts and part_ids else request
    return bool(_WHY.search(comparison) and not _ONLY_AMOUNTS.search(request))


def parts_for(request, parts):
    """No methods, filenames or operand labels are accepted from the caller."""
    parts = parts or {"part-1":request}
    selected = []
    for part, text in parts.items():
        if (_OPERATING.search(text) and _NET.search(text) and not _OTHER.search(text)
                and _CHANGE.search(request) and _ASK.search(request)):
            selected.append(part)
        elif selected and is_driver_restatement(text):
            selected.append(part)
    return selected


def prior_source_window(report, current, issuer, cutoff):
    """A pronoun may refer to a dated, actually read quarter even if no FY label was saved."""
    if not report or report.get("scope",{}).get("entities") != [issuer]:
        return {}
    if report.get("verification_available"):
        used = {ref for c in report.get("claims",[]) if c.get("status")=="Supported" and not c.get("stale")
                for ref in c.get("evidence_ids",[])}
    elif report.get("model_mode") == "offline_tools_only":
        used = {r["id"] for r in report.get("evidence",[]) if r.get("kind")=="document"}
    else:
        return {}
    windows = set()
    for r in report.get("evidence",[]):
        now = current.get(r.get("id"),{})
        if (r.get("id") not in used or r.get("kind")!="document" or r.get("entity")!=issuer
                or not now or now.get("stale") or r.get("stale") or now.get("version")!=r.get("version")
                or not r.get("published_at") or not available_by(r["published_at"],cutoff)):
            continue
        start,end=r.get("document_period_start"),r.get("document_report_date")
        if start and end and duration_class(start,end)=="quarter_length":
            windows.add((start,end))
    if len(windows)!=1:
        return {}
    start,end=windows.pop()
    return {"period_start":start,"period_end":end}


def plan(request, parts, scope, prior_bounds, *, referring=False, resolved_quarter=None):
    ids = parts_for(request,parts)
    issuers = scope.get("entities") or []
    if len(issuers)!=1 or not scope.get("cutoff") or not ids:
        return None
    bounds = {k:scope[k] for k in ("period_start","period_end") if scope.get(k)}
    specific=_SPECIFIC.search(request)
    if specific and resolved_quarter and resolved_quarter.get("period_end"):
        bounds={k:resolved_quarter[k] for k in ("period_start","period_end") if resolved_quarter.get(k)}
    if not bounds and not specific:
        bounds=prior_bounds
    if (specific and not (resolved_quarter or {}).get("period_end")) or (
            referring and not bounds and not re.search(r"最近|最新|latest",request,re.I)):
        return {"part_ids":ids, "gap":"需先确定这次追问指的是哪个单季；没有用最新财报替代它。"}
    if bounds.get("period_start") and (not bounds.get("period_end") or
            duration_class(bounds["period_start"],bounds["period_end"])!="quarter_length"):
        return {"part_ids":ids,"gap":"当前期间不是可确定的单季，未将全年或累计列当作季度。"}
    return {"part_ids":ids,"arguments":{"entity":issuers[0],**bounds}}


def _current_receipt(ctx, tool):
    ep=ctx["episode"]
    for row in reversed(ep.get("recent_tool_results",[])):
        if row.get("tool")!=tool:
            continue
        if (row.get("income_request")!=ep["request"] or not row.get("ok")
                or row.get("record_status") or row.get("scope_version")!=ctx["session"]["scope_version"]
                or ctx["scope"].get("entities") != [row.get("arguments",{}).get("entity")]):
            return None
        return row
    return None


def current_bridge(ctx):
    """Only a committed, current-turn receipt can supply reportable source arithmetic."""
    receipt = _current_receipt(ctx, "reconcile_net_income")
    value = (receipt or {}).get("data", {}).get("income_bridge")
    ep = ctx["episode"]
    parts = parts_for(ep["request"], ctx.get("request_parts"))
    if parts and isinstance(value, dict):
        return {**value, "episode_id":ep["id"], "scope_version":receipt["scope_version"], "part_ids":parts}


def explanation_context(ctx):
    """Reuse audited text first. A successful targeted search, including one with gaps, is terminal."""
    bridge = ctx["income_bridge"]
    if not wants_explanation(ctx["episode"]["request"], ctx.get("request_parts"), bridge["part_ids"]):
        return None
    receipt = _current_receipt(ctx, EXPLANATION_TOOL)
    saved = (receipt or {}).get("data", {}).get("income_explanation")
    if (saved and saved.get("current_period") == bridge.get("current_period")
            and saved.get("prior_period") == bridge.get("prior_period")):
        return {**saved, "source_review":"targeted_read"}
    from .income_explanation import inspect
    entity, = ctx["scope"]["entities"]
    outcome = inspect(None, entity, ctx["scope"]["cutoff"], bridge, ctx["calculations"],
                      existing_evidence=ctx["evidence"], search=False)
    preview = outcome["data"]["income_explanation"]
    if preview.get("targets"):
        return {**preview, "source_review":"existing_evidence"}


def reason_action(ctx):
    explanation = ctx.get("income_explanation") or {}
    if explanation.get("gaps") and explanation.get("source_review") == "existing_evidence":
        entity, = ctx["scope"]["entities"]
        return {"action":"tool", "tool":EXPLANATION_TOOL, "arguments":{"entity":entity},
                "reason":"已核对同比金额；核读同季公告，查清关键变动中尚无管理层解释的部分。"}


def offline_answers(report):
    from .answer_sections import income_table
    bridge=report["income_bridge"]
    table=income_table(report,bridge)
    missing=" ".join(bridge["gaps"]) or "现有原表和计算记录无法逐项核对。"
    item={"status":"gap","missing_support":missing}
    if table:
        rows=bridge["rows"]
        evidence=[r[k] for r in rows for k in ("current_id","prior_id") if r.get(k)]
        calcs=[r["contribution_id"] for r in rows if r.get("contribution_id")]
        if bridge.get("sum_id"):
            calcs.append(bridge["sum_id"])
        item={"status":"source_excerpt" if table["complete"] else "partial",
              "answer_sentence":table["summary"] or "已取得部分原表金额。",
              "evidence_ids":evidence,"calculation_ids":calcs,
              "quarter":(report.get("skill_fiscal_quarter") or {}).get("quarter")}
        if not table["complete"]:
            item["missing_support"]=missing
    return {part:dict(item) for part in bridge["part_ids"]}
