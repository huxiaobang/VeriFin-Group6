"""Check delivery of explicitly requested market-platform share inputs."""
import re

from .data import DataError
from .display_numbers import _matches, _quantity_spans
from .margin_formula import DIVISION, _reproduces
from .platform_revenue import CELL_TYPE, validate_share
from .request_review import report_coverage
from .skill_calculations import quarter_mentions
from .skill_routing import named_issuers


REVENUE = r"(?:\b(?:revenues?|(?:net )?sales)\b|[营營]收|收入|[销銷]售[额額])"
TOTAL = r"(?:\b(?:consolidated|total|overall|company[- ]wide)\b(?:\s+GAAP)?\s+|合[并併]|[總总]|全集团|全公司)" + REVENUE
SHARE = re.compile(r"\b(?:share|proportion|portion|percentage|percent)\s+of\b|占比|佔比|占.{0,12}(?:多少|幾成|几成)", re.I)
VALUE = re.compile(r"\b(?:what\s+(?:were|was|are|is)|how much|report|list|provide|state|give)\b|多少|列出|[给給]出", re.I)
INPUTS = re.compile(
    r"\b(?:show|list|give|provide|state|include|report)\s+(?:(?:the|your|both|two|numerical|underlying)\s+)*"
    r"(?:inputs|operands|(?:revenue )?(?:amounts|figures))\b|(?:列出|[给給]出|提供|展示).{0,12}(?:[输輸]入|分子.{0,5}分母)", re.I)
LINK = r"\s*(?:(?:was|were|is|of|amounted to|came to|stood at|reached|about|approximately|为|為|是|达|達)\s*){0,3}[:：]?\s*\(?\s*$"


def _platform(label):
    return r"(?<![A-Za-z0-9])" + re.escape(label) + r"(?![A-Za-z0-9])"


def _requested_inputs(question, platform):
    if INPUTS.search(question):
        return {"platform", "total"}
    asked = SHARE.split(question, maxsplit=1)[0]
    if not VALUE.search(asked) or re.search(r"\bas\s+(?:a|the)\s*$", asked, re.I):
        return set()
    return {name for name, metric in (("platform", platform + r"\s*(?:'s\s*)?" + REVENUE), ("total", TOTAL))
            if re.search(metric, asked, re.I)}


def _shown_amounts(text, platform, facts):
    expressions = {"total": TOTAL, "platform": platform + r"(?:\s+(?:GAAP\s+)?" + REVENUE + r")?"}
    anchors = {key: re.compile(label + LINK, re.I) for key, label in expressions.items()}
    matched, conflicts = set(), set()
    cursor = 0
    for start, end, (_, unit, value, tolerance) in _quantity_spans(text):
        prefix = text[cursor:start]
        cursor = end
        if unit in {"%", "percentage_points", "shares"} or "/" in unit:
            continue
        for key, fact in facts.items():
            if anchors[key].search(prefix):
                (matched if _matches(unit, value, tolerance, fact) else conflicts).add(key)
    if len(facts) == 2 and not conflicts and any(
            _reproduces(eq, facts["platform"], facts["total"]) for eq in DIVISION.finditer(text)):
        matched.update(facts)
    return matched, conflicts


def check_coverage(context, rows, verdicts, companies):
    parts = context.get("request_parts", {})
    request = context["episode"]["request"]
    scope = context["scope"]
    if not parts or not SHARE.search(request) or not re.search(TOTAL, request, re.I) or len(scope.get("entities", [])) != 1:
        return rows
    entity, = scope["entities"]
    bad = {i["target_id"] for i in context["rule_findings"]}
    supported = {c["id"]: {**c, "status": "Supported"} for c in context["claims"]
                 if c["id"] not in bad and not c.get("stale") and verdicts.get(c["id"], {}).get("status") == "Supported"}
    if not supported:
        return rows
    records = {r["id"]: r for r in context["evidence"] + context["calculations"] if not r.get("stale")}
    linked = {cid: set(claim.get("evidence_ids", [])) | {
        ref for calc in claim.get("calculation_ids", []) for ref in records.get(calc, {}).get("operand_ids", [])
    } for cid, claim in supported.items()}
    used = set().union(*linked.values())
    quarters = {quarter for _, quarter in quarter_mentions(request)}
    cells = [r for ref, r in records.items() if ref in used and r.get("cell_type") == CELL_TYPE and r.get("entity") == entity
             and all(not scope.get(k) or scope[k] == r.get(k) for k in ("period_start", "period_end"))
             and (not quarters or r.get("fiscal_quarter") in quarters)]
    labels = {r["market_platform"] for r in cells if r.get("market_platform") and re.search(_platform(r["market_platform"]), request, re.I)}
    if not cells or len({(r.get("period_start"), r.get("period_end")) for r in cells}) != 1 or len(labels) > 1:
        return rows
    # A located total can exist even when the requested source row was absent.
    label = next(iter(labels), None)
    platform = _platform(label) if label else r"(?!)"
    inputs = {part: _requested_inputs(question, platform) for part, question in parts.items()}
    public = report_coverage(parts, rows, list(supported.values()), reviewed=True, requirements=context["requirements"],
                             unanswered_ids=context["episode"].get("unanswered_requirement_ids", []))
    names = {"platform": (label + " " if label else "所问业务平台的") + "本季营收", "total": "本季合并营收"}
    shown = set()
    rejected = {}
    for row in public:
        if row.get("status") != "answered":
            continue
        text = row.get("answer_sentence", "")
        if set(named_issuers(text, companies)) - {entity}:
            if inputs[row["part"]] or SHARE.search(parts[row["part"]]):
                rejected[row["part"]] = "答句提到其他发行人，无法对应所问公司的本季营收。"
            continue
        options = [r for r in cells if r["id"] in linked[row["claim_id"]] and r.get("market_platform") in {label, None}]
        sides = {"platform" if r.get("market_platform") else "total": r for r in options}
        if len(sides) != len(options):
            continue
        if len(sides) == 2:
            try:
                validate_share(sides["platform"], sides["total"])
            except DataError:
                continue
        matches, conflicts = _shown_amounts(text, platform, sides)
        if conflicts:
            rejected[row["part"]] = "答句列出的金额或币种与相应的季度营收记录不符。"
        else:
            shown.update(matches)
    for row in rows:
        if row.get("status") != "answered":
            continue
        part = row["part"]
        if part in rejected:
            row.update(status="gap", missing_support=rejected[part])
        elif absent := inputs.get(part, set()) - shown:
            missing = "、".join(value for key, value in names.items() if key in absent)
            row.update(status="partial", missing_support=f"已核验的答复仍缺少以下金额：{missing}。")
    return rows
