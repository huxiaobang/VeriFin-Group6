"""Present dated cash-flow results for the questions their source calculations address."""
from decimal import Decimal
import re
from .metric_language import NAME, NAMES


def cash_amount_question(text):
    matches = list(NAME.finditer(text))
    if len(matches) != 1:
        return False
    match = matches[0]
    return (NAMES[match[0].casefold()] == "operating_cash_flow"
            and re.fullmatch(r"[\s，,]*(?:(?:那|那么|这一季|这季|本季|同一季度|and|then|what about|how about|what was|the)\s*)*",
                             text[:match.start()], re.I) is not None
            and re.fullmatch(r"(?:呢|是多少|有多少|多少|怎么样|如何|for (?:the|that) quarter)?[？?。.!\s]*",
                             text[match.end():].strip(), re.I) is not None)


def cash_amount(findings, ref):
    row = findings.record(ref)
    operands = row.get("operand_ids")
    return {"text":f"经营现金流为 {row['value']:,}（{row['unit']}）。",
            "evidence_ids":list(operands or [ref]), "calculation_ids":[ref] if operands else []}


def difference_text(delta, unit):
    value = Decimal(str(delta))
    if not value:
        return "经营现金流与净利润相等"
    direction = "多" if value > 0 else "少"
    currency, _, scale = unit.partition(" ")
    multiplier = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}.get(scale)
    if currency == "USD" and multiplier is not None:
        dollars = abs(value) * multiplier
        amount = (f"{(dollars / 10_000).normalize():,f} 万美元" if dollars >= 10_000
                  else f"{dollars.normalize():,f} 美元")
    else:
        amount = f"{abs(value):,f}（{unit}）"
    return f"经营现金流比净利润{direction} {amount}"


_CASH = re.compile(r"现金流|经营现金|现金与利润|cash flow|operating cash", re.I)
_PROFIT = re.compile(r"净利|净亏|盈利|利润|net (?:income|loss|earnings)|profit", re.I)
_DIFFERENCE = re.compile(r"差[了额距异]|相差|差多少|多[多少]|多还是少|高了|低了|多少|拉开|"
                         r"differ|higher|lower|how much|gap", re.I)
_DRIVERS = re.compile(r"(?:哪[几些]|主要).{0,15}(?:项|变动|调节|因素|来源)|拉开|调节项|构成|"
                      r"(?:解释|造成|形成|导致|为什么).{0,12}(?:差[额异距]|两者|现金流.{0,6}净利|净利.{0,6}现金流)|"
                      r"reconcil|drivers?|accounts? for|explain.{0,12}(?:gap|difference)", re.I)
_OTHER = re.compile(r"自由现金流|投资现金流|融资现金流|筹资现金流|现金流量?比率|转化率|转换率|"
                    r"可持续|预测|预计|指引|将来|明年|股价|自由现金|比率|增速|增长率|"
                    r"free cash flow|forecast|sustainab|ratio|growth rate", re.I)


def answer_parts(parts, notes, fiscal_target, gaps):
    """Map a single-period bridge or its line items to matching questions in the offline report."""
    relevant = [n for n in notes if n.get("kind") in {"cash_bridge","cash_drivers"} and n.get("period_end")]
    dates = {n["period_end"] for n in relevant}
    start_dates = {n.get("period_start") for n in relevant}
    if len(dates) != 1 or len(start_dates) != 1:
        return {}
    end = next(iter(dates))
    if fiscal_target.get("period_end") not in (None,end):
        return {}
    start = next(iter(start_dates))
    if start and fiscal_target.get("period_start") not in (None,start):
        return {}
    money = [n for n in relevant if n["kind"]=="cash_bridge"]
    drivers = [n for n in relevant if n["kind"]=="cash_drivers"]
    answers = {}
    has_comparison = False
    for part, question in parts.items():
        if cash_amount_question(question) and len(money) == 1 and money[0].get("cash_amount"):
            amount = money[0]["cash_amount"]
            answers[part] = {"quarter": fiscal_target.get("quarter"), "period_end": end, "status":"source_excerpt",
                             **{k:amount[k] for k in ("evidence_ids","calculation_ids")},
                             "answer_sentence":f"{start} 至 {end}，{amount['text']}" if start else f"截至 {end} 的期间，{amount['text']}"}
            continue
        if _OTHER.search(question):
            continue
        compares = bool(_CASH.search(question) and _PROFIT.search(question))
        asks_amount = compares and bool(_DIFFERENCE.search(question) or re.search(r"相比|比较|compare", question, re.I))
        asks_drivers = bool(_DRIVERS.search(question) and (compares or has_comparison or
                            (_CASH.search(question) and re.search(r"差额|difference|gap",question,re.I))))
        if not (asks_amount or asks_drivers):
            continue
        has_comparison |= compares
        rows = (money if asks_amount else []) + (drivers if asks_drivers else [])
        complete = ((not asks_amount or (money and all(x["calculation_ids"] for x in money))) and
                    (not asks_drivers or drivers) and all(x.get("complete",True) for x in rows))
        if not rows and not gaps:
            continue
        item = {"quarter":fiscal_target.get("quarter"), "period_end":end,
                "evidence_ids":list(dict.fromkeys(i for n in rows for i in n["evidence_ids"])),
                "calculation_ids":list(dict.fromkeys(i for n in rows for i in n["calculation_ids"]))}
        text = " ".join(n["text"] for n in rows)
        if complete:
            answers[part] = {**item,"status":"source_excerpt","answer_sentence":text}
        else:
            gap = " ".join(gaps) or "缺少能够完整对账的现金流量表金额或调节项。"
            answers[part] = {**item,"status":"gap","missing_support":(text+" "+gap).strip()}
    return answers
