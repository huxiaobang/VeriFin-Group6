"""Explicit financial measure names shared by sentence and question verification."""
import re
from datetime import date

from .financial import METRICS


ALIASES = {
    "revenue": ("revenues", "net sales", "total revenue", "total revenues", "营业收入", "净销售额", "销售收入", "营收"),
    "operating_income": ("operating profit", "营业利润", "经营利润"),
    "net_income": ("net profit", "net earnings", "净利润", "净收益"),
    "gross_profit": ("毛利润", "毛利额"),
    "operating_income_margin": ("operating margin", "operating profit margin", "营业利润率", "经营利润率"),
    "gross_profit_margin": ("gross margin", "毛利率"),
    "net_income_margin": ("net margin", "net profit margin", "净利率", "净利润率"),
    "revenue_growth": ("sales growth", "revenue growth", "营收增速", "营收增长率", "收入增速", "销售额增速"),
    "operating_cash_flow": ("cash flow from operations", "cash flow from operating activities", "cash flows from operating activities",
                            "net cash provided by operating activities", "经营现金流", "经营活动现金流量净额",
                            "经营活动产生的现金流量净额", "经营活动现金流", "经营性现金流"),
    "investing_cash_flow": ("cash flows from investing activities", "投资现金流", "投资活动现金流"),
    "financing_cash_flow": ("cash flows from financing activities", "融资现金流", "筹资活动现金流"),
    "free_cash_flow": ("free cash flow", "自由现金流"),
    "cost_of_revenue": ("cost of goods sold", "cost of sales", "营业成本", "销售成本"),
    "capital_expenditures": ("capex", "资本开支", "资本支出"),
    "eps_basic": ("basic eps", "basic earnings per share", "基本每股收益"),
    "eps_diluted": ("diluted eps", "diluted earnings per share", "稀释每股收益", "摊薄每股收益"),
    "current_assets": ("流动资产",),
    "current_liabilities": ("流动负债",),
    "inventory": ("inventories", "存货", "库存"),
    # Consume this generic phrase whole; by itself it does not designate
    # 'cash and cash equivalents' or any one type of cash flow.
    "": ("cash flow", "cash flows", "现金流"),
}
NAMES = {metric.replace("_", " "): metric for metric in (*METRICS,*ALIASES) if metric}
NAMES.update({name: metric for metric, names in ALIASES.items() for name in names})
NAME = re.compile("|".join(
    (r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])") if name.isascii() else re.escape(name)
    for name in sorted(NAMES, key=len, reverse=True)), re.I)


def metric_mentions(text: str) -> set[str]:
    """Return named measures; a shared noun inside a longer measure is not a second measure."""
    return {metric for match in NAME.finditer(text) if (metric := NAMES[match.group().casefold()])}


LINK = re.compile(r"(?:为|约为|是|达|was|were|of|amounted to|stood at|totalled|totaled|approximately|about|\s)*",re.I)
CURRENT = {"","本季","本期","本季度","本年","本财年","本年度","this quarter","this year","this period"}
DATE_PREFIX = re.compile(r"(?:截至\s*|(?:for the )?(?:quarter|year) ended\s+)(\d{4}-\d{2}-\d{2})(?:的)?(?:季度|全年|年度)?",re.I)
QUALIFIED_BASIS = re.compile(r"分部|子公司|地区|国内业务|海外业务|剔除|调整后|同店|固定汇率|预测|预期|预计|指引|假设|"
                             r"\b(?:segment|division|adjusted|non[- ]GAAP|constant currency|guidance|forecast|projected)\b",re.I)


def plain_amount_metric(text):
    """Identify the subject of a simple amount statement even without model-supplied fields."""
    names = list(NAME.finditer(text))
    if len(names) != 1 or QUALIFIED_BASIS.search(text):
        return None
    marker = names[0]
    if text[:marker.start()].strip().casefold() not in CURRENT:
        return None
    tail = text[marker.end():]
    number = re.search(r"(?:USD\s*|[$€£]\s*)?[+−-]?\d",tail,re.I)
    if number and LINK.fullmatch(tail[:number.start()]):
        return NAMES[marker.group().casefold()]
    return None


def direct_amount_anchors(prefix, constraints, records):
    """Bind a plain, named amount, declining qualifiers with unknown scope or multiple possible facts."""
    # The prefix starts immediately after the previous display quantity. Keep
    # the local clause, so an unrelated measure elsewhere cannot qualify it.
    local = re.split(r"[，；;。!?！？]|,(?!\d)|\.\s+(?=[A-Za-z])",prefix)[-1].strip()
    local = re.sub(r"^(?:and\b|而|及)\s*", "", local, flags=re.I)
    names = list(NAME.finditer(local))
    if len(names) != 1 or not LINK.fullmatch(local[names[0].end():]):
        return []
    metric = NAMES[names[0].group().casefold()]
    qualifier = local[:names[0].start()].strip().casefold()
    if not metric or not isinstance(constraints,dict):
        return []
    entity = constraints.get("entity")
    start, end = constraints.get("period_start"), constraints.get("period_end")
    if qualifier in CURRENT and (not entity or not end):
        # A draft may omit metadata for a sentence listing several amounts.
        # Bind to the cited context only if all numeric records agree. Never
        # choose a company, period or basis from competing records.
        fields = ("entity", "period_start", "period_end", "basis", "business_scope", "currency")
        contexts = {tuple(r.get(k) for k in fields) for r in records}
        if len(contexts) == 1:
            source_entity, source_start, source_end, *_ = contexts.pop()
            entity = entity or source_entity
            if not end:
                start, end = start or source_start, source_end
    dated = DATE_PREFIX.fullmatch(qualifier)
    if dated:
        end = dated.group(1)
        if end != constraints.get("period_end"):
            start = None
    elif qualifier in ("上年同期","去年同期","prior-year quarter","prior-year period"):
        try:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
            start, end = s.replace(year=s.year-1).isoformat(), e.replace(year=e.year-1).isoformat()
        except (TypeError,ValueError):
            return []
    elif qualifier not in CURRENT:
        # E.g. a segment, adjusted result or a management forecast may have a different base.
        return []
    if not entity or not end:
        return []
    candidates = [r for r in records if r.get("metric") == metric and r.get("entity") == entity
                  and r.get("period_end") == end and (not start or r.get("period_start") == start)
                  and all(not constraints.get(k) or r.get(k) == constraints[k] for k in ("business_scope","basis"))]
    # An unqualified date can represent both the quarter and year ending on
    # that date. Do not resolve competing periods, currencies or accounting bases.
    distinct = {tuple(r.get(k) for k in ("period_start","period_end","currency","unit","basis","business_scope","value"))
                for r in candidates}
    if len(distinct) != 1:
        return []
    if dated and not start:
        try:
            duration = (date.fromisoformat(end)-date.fromisoformat(candidates[0]["period_start"])).days+1
        except (KeyError,TypeError,ValueError):
            return []
        if ("quarter" in qualifier or "季度" in qualifier) and not 75 <= duration <= 105:
            return []
        if ("year" in qualifier or "年" in qualifier) and not 345 <= duration <= 385:
            return []
    return candidates
