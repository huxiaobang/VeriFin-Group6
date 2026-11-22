"""Trace a quarterly incremental profit margin to four original statement cells."""
from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
import re

from .data import stable_id
from .skill_routing import TOPICS
from .margin_reconciliation import quarter_side
from .operating_margin import headline_label
from .skill_calculations import requested_quarter
from .statement_amount_cells import statement_cells
from .tagged_profit_cells import tagged_cells


PROFIT = {
    "gross_profit": (r"毛利|gross profit", "毛利"),
    "operating_income": (r"(?:营业|经营|营运)(?:利润|收益|所得)|operating (?:income|profit|earnings)", "营业利润"),
}
OTHER_BASIS = re.compile(r"调整后|扣非|非\s*GAAP|分部|分地区|事业部|部门|EBITDA|\bEBIT\b|"
                         r"\badjusted|non[- ]?GAAP|\bsegment|regional|净利润|净利[润额]|net (?:profit|income)", re.I)
OTHER_PERIOD = re.compile(r"预[期测]|超预期|环比|连续两季|年初至今|累计|本年度|全年|减去上一季|"
                          r"forecast|estimate|consensus|sequential|previous quarter|quarter.over.quarter|"
                          r"\bqoq\b|year.to.date|\bytd\b|full.year|annual comparison", re.I)
SAME_QUARTER_LAST_YEAR = re.compile(r"同比|去年|上年|year.over.year|year.ago|last year|prior.year|\byoy\b", re.I)
INCREMENTAL = re.compile(TOPICS["incremental-profit-margin"] + "|" + r"增量(?:利润率|毛利率|营业利润率)|经营杠杆|(?:每(?:增加|减少|新增|少卖|多卖|多做)|多卖|少卖|增收).{0,14}(?:块|元|美元|dollar)|"
                         r"每[一1](?:块钱|美元|元)(?:的)?(?:增收|新增收入)|"
                         r"(?:(?:多|少)(?:卖|做)|收入.{0,4}多).{0,14}(?:留|赚|带来)|"
                         r"incremental (?:gross |operating |profit )?margin|operating leverage|(?:additional|extra|incremental) dollar", re.I)
AVERAGE = re.compile(r"平均利润率|当季.{0,5}利润率|本季.{0,5}利润率|当季.{0,6}毛利率|current.quarter margin|average (?:profit )?margin", re.I)


def _metric(question, history):
    match = [key for key, (pat, _) in PROFIT.items() if re.search(pat, question, re.I)]
    if not match and history and re.search(r"按刚才|刚才说的|同一口径|用上面|same (?:measure|basis)", question, re.I):
        if OTHER_BASIS.search(history[-1]):
            return None
        match = [key for key, (pat, _) in PROFIT.items() if re.search(pat, history[-1], re.I)]
    return match[0] if len(match) == 1 else None


def _quarter_docs(question, history, docs, quarter_end):
    native = [doc for doc in docs if doc.get("period_end") and doc.get("source_text") and
              doc["source"].lower().endswith((".html", ".htm")) and
              re.match(r"^(?:8-K|10-[KQ])\b", doc.get("form", ""), re.I)]
    label = requested_quarter([*history, question])
    if label:
        ends = {d["period_end"] for d in native if headline_label(d["source_text"], d["period_end"]) == label}
        if len(ends) != 1 or (quarter_end and quarter_end not in ends):
            return None, []
        quarter_end = ends.pop()
    if not quarter_end and re.search(r"这[一]?(?:季|季度)|当季|本季|最近|最新|this quarter|latest|most recent",
                                     " ".join([*history[-1:], question]), re.I):
        quarter_end = max((doc["period_end"] for doc in native), default=None)
    return quarter_end, [d for d in native if d["period_end"] == quarter_end]


def _groups(docs, metric, end):
    tables = defaultdict(lambda: defaultdict(list))
    witnesses = defaultdict(set)
    for doc in docs:
        source_text = doc["source_text"]
        info = {"url": doc["url"], "sha256": doc["snapshot_hash"]}
        source_id = stable_id("source_doc_", doc["url"])
        rows = statement_cells(doc["raw"], doc, info, source_id, source_text, doc.get("prior_units", {}),
                               doc.get("filed_boundaries"))
        if re.fullmatch(r"10-[QK]", doc.get("form", ""), re.I):
            rows.extend(tagged_cells(doc))
        text_hash = sha256(source_text.encode("utf-8")).hexdigest()
        for cell in rows:
            side = quarter_side(cell, end)
            if side is None or cell["metric"] not in {"revenue", metric}:
                continue
            cell["text_snapshot_hash"] = text_hash
            key = (side, cell["metric"])
            tables[(source_id, cell["table_id"])][key].append(cell)
            witnesses[key].add((cell["period_start"], cell["period_end"], cell["basis"],
                                cell["business_scope"], cell["currency"], cell["value"]))
    # Use a single original table. A competing filing with a different value or basis
    # for a purportedly identical period is a substantive conflict, even if incomplete.
    usable = []
    for table in tables.values():
        selected = {key: cells[0] for key, cells in table.items()
                    if len(cells) == 1 and len(witnesses[key]) == 1}
        if not selected:
            continue
        same_units = {(c["entity"], c["currency"], c["unit"], c["basis"], c["business_scope"]) for c in selected.values()}
        if len(same_units) != 1:
            continue
        if any(len({(c["period_start"], c["period_end"]) for (i, _), c in selected.items() if i == side}) > 1
               for side in (0, 1)):
            continue
        usable.append(selected)
    # If two equally complete tables support different metrics without a single
    # eligible common table, never silently fill missing slots across tables.
    return max(usable, key=len, default={})


def _quantum(cell):
    printed = cell["printed_value"]
    fraction = re.search(r"\.(\d+)", printed)
    return Decimal(str(cell["printed_scale"])) / (10 ** (len(fraction[1]) if fraction else 0))


def _explain(findings, endpoints, metric, average, end):
    name = PROFIT[metric][1]
    evidence = [c["id"] for c in endpoints.values()]
    findings.evidence.update((cell["id"], cell) for cell in endpoints.values())
    changes, missing = {}, []
    descriptions = []
    for key, label in (("revenue", "营业收入"), (metric, name)):
        cells = [endpoints.get((i, key)) for i in (0, 1)]
        missing.extend(f"{period}{label}" for period, cell in zip(("本季", "上年同期"), cells) if cell is None)
        if all(cells):
            now, prior = cells
            changes[key] = findings.calc(label + "季度同比差额", "difference", [now["id"], prior["id"]], now["unit"])
            delta = findings.value(changes[key])
            descriptions.append(f"{label}由 {prior['value']:,} 至 {now['value']:,}，差额 {delta:+,} {now['currency']}")
    current = next((cell for (side, _), cell in endpoints.items() if side == 0), None)
    prior = next((cell for (side, _), cell in endpoints.items() if side == 1), None)
    periods = "、".join(c["period_end"] for c in (current, prior) if c)
    prefix = f"合并 GAAP 单季（本次对照的期末日 {periods}）。" if periods else ""
    pieces = [prefix + "；".join(descriptions) + "。"] if descriptions else []
    calculations = list(changes.values())
    if missing:
        findings.gap("未取得可比原表中的" + "、".join(missing) + "；本次无法核实增量利润率。")
    elif len(changes) == 2:
        revenue_delta = findings.value(changes["revenue"])
        profit_delta = findings.value(changes[metric])
        uncertainty = (_quantum(endpoints[0, "revenue"]) + _quantum(endpoints[1, "revenue"])) / 2
        if revenue_delta == 0:
            pieces.append("原表两期收入差为零，增量利润率无定义。")
        elif abs(Decimal(str(revenue_delta))) <= uncertainty:
            pieces.append("收入差与两端原表金额的合计舍入误差相当，分母接近零，不能稳健估算每元收入对应的利润变化。")
        else:
            ratio = findings.calc(name + "增量利润率", "margin", [changes[metric], changes["revenue"]], "%")
            calculations.append(ratio)
            result = findings.value(ratio)
            action = "多卖" if revenue_delta > 0 else "少卖"
            effect = "增加" if profit_delta > 0 else "减少" if profit_delta < 0 else "变化"
            currency = endpoints[0, "revenue"]["currency"]
            denomination = {"USD":"美元", "CAD":"加元", "EUR":"欧元", "GBP":"英镑"}.get(currency, currency)
            pieces.append(f"两项差额相除约为 {result:.2f}%，即每{action} 1 {denomination}，{name}{effect}约 {abs(result)/100:.3f} {denomination}。")
    if evidence:
        if not pieces:
            pieces = [f"原表只找到 {cell['period_end']} 的 {cell['row_label']} {cell['value']:,} {cell['unit']}。"
                      for cell in endpoints.values()]
        findings.note("".join(pieces), evidence + calculations, kind="incremental_margin",
                      period_end=end, period_start=current["period_start"] if current else None,
                      complete=not missing, requested_metric=metric)
    if average and (0, metric) in endpoints and (0, "revenue") in endpoints and endpoints[0, "revenue"]["value"]:
        pair = [endpoints[0, metric]["id"], endpoints[0, "revenue"]["id"]]
        ratio = findings.calc("本季"+name+"率", "margin", pair, "%")
        findings.note(f"截至 {end} 的单季全部收入对应的{name}率约为 {findings.value(ratio):.2f}%。",
                      pair + [ratio], kind="incremental_current_margin", period_end=end,
                      period_start=endpoints[0, metric]["period_start"], complete=True)


def analyze(question, history, docs, findings, *, quarter_end=None):
    if not INCREMENTAL.search(question):
        findings.skip("本问没有明确要求比较利润变化与收入变化。请按用户实际问题比较整季利润率或原表金额。")
        return
    if OTHER_BASIS.search(question) or OTHER_PERIOD.search(question):
        findings.gap("所问分部、利润定义或比较基准超出本工具的合并 GAAP 单季同比范围；需查所问口径的原表，不可代用集团营业利润。")
        return
    metric = _metric(question, history)
    if not metric or not SAME_QUARTER_LAST_YEAR.search(question):
        findings.gap("需要明确利润行和同比比较基准，才能选择四个同口径金额。")
        return
    end, documents = _quarter_docs(question, history, docs, quarter_end)
    if not documents:
        findings.gap("没有能确认目标财季的发行人原表和可核对的表头；先核实季度及其原始披露。")
        return
    endpoints = _groups(documents, metric, end)
    _explain(findings, endpoints, metric, bool(AVERAGE.search(question)), end)


def answer_parts(parts, notes, gaps, fiscal_target=None):
    """Map only this method's observed profit and sales comparison to matching subquestions."""
    answers = {}
    own = [n for n in notes if n.get("kind") in {"incremental_margin", "incremental_current_margin"}]
    fiscal_target = fiscal_target or {}
    for field in ("period_start", "period_end"):
        if fiscal_target.get(field) and any(n.get(field) != fiscal_target[field] for n in own):
            return answers
    if len({n.get("period_end") for n in own}) > 1:
        return answers
    for key, question in parts.items():
        wants_delta, wants_average = bool(INCREMENTAL.search(question)), bool(AVERAGE.search(question))
        if not (wants_delta or wants_average):
            continue
        relevant = [n for n in own if wants_delta and n["kind"] == "incremental_margin"
                    or wants_average and n["kind"] == "incremental_current_margin"]
        missing = list(gaps)
        if wants_average and not any(n["kind"] == "incremental_current_margin" for n in relevant):
            missing.append("未核实本季全部收入和指定利润对应的整季利润率。")
        if relevant and re.search(r"成因|原因|归因|为何|为什么|\bwhy\b|drivers?", question, re.I):
            missing.append("两期金额支持增量比率，具体经营原因仍需独立披露。")
        if not relevant and not missing:
            continue
        item = {"evidence_ids":list(dict.fromkeys(ref for n in relevant for ref in n["evidence_ids"])),
                "calculation_ids":list(dict.fromkeys(ref for n in relevant for ref in n["calculation_ids"]))}
        if relevant:
            item.update(period_end=relevant[0]["period_end"], answer_sentence=" ".join(n["text"] for n in relevant))
            if fiscal_target.get("period_end") == relevant[0]["period_end"]:
                item["quarter"] = fiscal_target.get("quarter")
        if missing:
            item.update(status="partial" if relevant else "gap", missing_support=" ".join(dict.fromkeys(missing)))
        else:
            item["status"] = "source_excerpt"
        answers[key] = item
    return answers
