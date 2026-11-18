"""Offline planning for a quarterly consolidated operating-margin comparison.

Every source read and arithmetic step goes through the same public tools as the
model planner. Fiscal labels select a disclosure; dates on the original table
select the amounts within it.
"""
from collections import defaultdict
import re

from .margin_reconciliation import margin_reconciliation, quarter_side
from .metric_language import metric_mentions, QUALIFIED_BASIS
from .sales_comparison import QUARTER, ORDINALS
from .skill_calculations import previous_quarter, requested_fiscal_year, tool


MARGIN = re.compile(r"营业利润率|营运利润率|经营利润率|营业利益率|\boperating (?:profit |income )?margins?\b", re.I)
YOY = re.compile(r"同比|(?:去年|上年|上一年)(?:同[期季]|的?同一季度)|\by/?o/?y\b|year[- ]o(?:ver|n)[- ]year|"
                 r"year[- ]ago|(?:prior|previous|last) year|same quarter (?:a|one) year", re.I)
ATTRIBUTION = re.compile(r"原因|归因|为什么|解释|驱动|贡献|因素|说明到|拆解|分解|费用率|对平|抵消|拖累|拖了?后腿|拉低|"
                         r"\b(?:why|explain\w*|driver\w*|dr(?:ive|ove|iven)|attribut\w*|caus\w*|"
                         r"decompos\w*|reconcil\w*|contribut\w*|breakdown|expense ratios?)\b", re.I)
NONCONSOLIDATED = re.compile(r"调整后|扣非|分部|分地区|按地区|门店(?:层面|级)|各业务|"
                            r"\b(?:adjusted|non[- ]?gaap|segment(?:s|al)?|store[- ]level|regional)\b", re.I)
OTHER_PERIOD = re.compile(r"环比|年初至今|[前上]半年|全年.{0,5}(?:的)?(?:营业|经营)利润率|"
                          r"quarter[- ]o(?:ver|n)[- ]quarter|\b(?:ytd|qoq|annual operating margin)\b", re.I)
LATEST = re.compile(r"最新|最近|刚(?:刚)?(?:公布|披露)|\b(?:latest|most recent)\b", re.I)
SHORT_FY = re.compile(r"\bFY\s*(\d{2})\s*[- ]?Q([1-4])\b", re.I)
JOINT_TITLE = re.compile(r"\b(first|second|third|fourth) quarter and (?:full|fiscal)[- ]year (?:of )?(20\d\d)\b", re.I)
METRICS = ("operating_income", "revenue")
NAMES = {"operating_income": "营业利润", "revenue": "营业收入", "gross_profit": "毛利"}
AMOUNTS_QUERY = "consolidated income statements operations total revenue net sales gross profit income from operations operating income cost of sales"
DRIVERS_QUERY = "operating margin operating income gross margin percentage operating expenses increased decreased driven by due to attributable to impact"


def requested(text):
    return bool(MARGIN.search(text) and YOY.search(text))


def expense_followup(text, previous):
    """Resolve the unnamed measure from the immediately preceding question, never from incidental evidence."""
    if not (re.search(r"费用|成本|开支|哪[几些]项|\b(?:expenses?|costs?|line items?)\b", text, re.I)
            and re.search(r"抵[销消]|拖累|拖了?后腿|拉低|贡献|帮了?忙|拆解|分解|"
                          r"offset|drag|helped|hurt|contribut|breakdown", text, re.I)):
        return False
    if (metric_mentions(text) or QUALIFIED_BASIS.search(text) or OTHER_PERIOD.search(text)
            or re.search(r"管理层|电话会|公告怎么|management|earnings call", text, re.I)
            or re.search(r"全年|半年|累计|去年|明年|未来|annual|full.year|forecast|cash flow|现金流", text, re.I)):
        return False
    # A preamble may mention what was discussed before ("sales and net income grew.").
    # Read the actual question in each part. Multiple named measures leave the reference open.
    parts = (previous.get("request_parts") or {"request": previous["request"]}).values()
    questions = " ".join(re.split(r"[。！？!?]|(?<=\.)\s+", part.rstrip("。！？!?. "))[-1] for part in parts)
    prior_measures = metric_mentions(questions)
    if prior_measures:
        return (prior_measures == {"operating_income_margin"} and requested(questions)
                and not (NONCONSOLIDATED.search(questions) or OTHER_PERIOD.search(questions)))
    return (previous.get("followup_focus") or {}).get("metric") == "operating_income_margin"


def prepare(text, entity, quarter, scope, earlier):
    if not entity:
        return None, "需要明确本次营业利润率比较所属的单一发行人。"
    if NONCONSOLIDATED.search(text) or OTHER_PERIOD.search(text) or requested_fiscal_year(text):
        return None, "本问包含其他期间或核算口径，需分别确定其可比利润和收入。"
    end = scope.get("period_end")
    latest = LATEST.search(text) or (earlier and LATEST.search(earlier[-1]))
    if not (quarter or end or latest):
        return None, "需要明确目标财季，才能确定营业利润率同比比较的两期。"
    return {"method": "operating_margin", "entity": entity, "quarter": quarter,
            "end": end, "start": scope.get("period_start"), "latest": bool(latest),
            "explain": bool(ATTRIBUTION.search(text)), "steps": [], "operands": [], "calculations": []}, None


def headline_label(text, end):
    # Use the reporting headline, before later outlooks and comparative narratives.
    for line in text[:2600].splitlines():
        if not re.search(r"earnings|results|press release|\bFY\s*\d{2}\s*[- ]?Q[1-4]", line, re.I):
            continue
        header = re.split(r"outlook|forward[- ]looking|year[- ]over[- ]year", line, 1, flags=re.I)[0]
        labels = set()
        for m in QUARTER.finditer(header):
            ordinal = m["oa"] or m["ob"]
            number = ORDINALS.index(ordinal.lower()) + 1 if ordinal else m["qc"] or m["qd"]
            labels.add(f"{m['ya'] or m['yb'] or m['yc'] or m['yd']}Q{number}")
        for ordinal, year in JOINT_TITLE.findall(header):
            labels.add(f"{year}Q{ORDINALS.index(ordinal.lower()) + 1}")
        for short_year, number in SHORT_FY.findall(header):
            fiscal_years = [y for y in range(int(end[:4]) - 1, int(end[:4]) + 2) if y % 100 == int(short_year)]
            labels.update(f"{year}Q{number}" for year in fiscal_years)
        if labels:
            return next(iter(labels)) if len(labels) == 1 else None
    return None


def _document_action(state, ctx):
    last = ctx["episode"].get("last_tool_result", {})
    if "pending_heads" not in state:
        docs = [doc for c in last.get("data", {}).get("companies", []) if c["entity"] == state["entity"]
                for doc in c["replay_documents"] if doc.get("period_end")
                and not re.search(r"transcript|earnings call|presentation", doc.get("form") or "", re.I)]
        if state["end"]:
            docs = [d for d in docs if d["period_end"] == state["end"]]
        elif state["latest"] and not state["quarter"] and docs:
            latest = max(d["period_end"] for d in docs)
            docs = [d for d in docs if d["period_end"] == latest]
        state["pending_heads"] = sorted(docs, key=lambda d: (d["period_end"], d["document_id"]), reverse=True)
        state["documents"] = []

    if state["steps"][-1]["tool"] == "read_document":
        for row in last.get("evidence", []):
            if row.get("kind") != "document":
                continue
            end = row.get("document_report_date")
            label = headline_label(row["text"], end) if end else None
            if (state["end"] and state["end"] != end) or (label and state["quarter"] and label != state["quarter"]):
                continue
            if not end or (state["quarter"] and not label and not state["end"]):
                continue
            state["end"] = end
            state["quarter"] = state["quarter"] or label
            state["documents"].append(row["document_id"])
            if any(JOINT_TITLE.search(line) for line in row["text"][:2600].splitlines()):
                state.setdefault("mixed_period_documents", []).append(row["document_id"])

    while state["pending_heads"]:
        doc = state["pending_heads"].pop(0)
        if not state["end"] or doc["period_end"] == state["end"]:
            return tool("read_document", entity=state["entity"], document_id=doc["document_id"], limit_chars=2600)
    if not state["documents"]:
        target = state["quarter"] or state["end"] or "最近财季"
        state["gap"] = f"未能从截至研究时点的 {state['entity']} 公告核实 {target} 的报告期，无法选择两期营业利润和营业收入。"
        return None
    state["phase"] = "amounts"
    return _search(state, AMOUNTS_QUERY)


def _search(state, query):
    return tool("search_documents", entity=state["entity"], period_end=state["end"], document_ids=state["documents"],
                query=query, limit=8, max_context_chars=15000)


def _select(state, evidence):
    ids = set(state["operands"])
    tables = defaultdict(lambda: defaultdict(list))
    values = defaultdict(set)
    for cell in evidence:
        if (cell["id"] not in ids or cell.get("stale") or cell.get("cell_type") != "statement_amount_cell"
                or cell.get("document_id") not in state["documents"] or cell.get("entity") != state["entity"]
                or cell.get("business_scope") != "consolidated" or cell.get("basis") not in {"US-GAAP", "GAAP"}
                or cell.get("scale") != 1 or not cell.get("currency") or cell.get("unit") != cell["currency"]
                or not cell.get("table_id") or cell.get("metric") not in (*METRICS, "gross_profit")):
            continue
        side = quarter_side(cell, state["end"], state["start"])
        if side is None:
            continue
        key = (side, cell["metric"])
        tables[cell["document_id"], cell["table_id"], cell["basis"], cell["currency"]][key].append(cell)
        values[key].add((cell["period_start"], cell["period_end"], cell["value"], cell["currency"], cell["basis"]))
    required = [(side, metric) for side in (0, 1) for metric in METRICS]
    state["available_operands"] = list(dict.fromkeys(c["id"] for columns in tables.values()
                                        for key, cells in columns.items() if key in required for c in cells))
    periods = _period_names(state)
    missing = [f"{periods[side]}的{NAMES[m]}" for side, m in required if not values[side, m]]
    conflicts = [f"{periods[side]}的{NAMES[m]}" for side, m in required if len(values[side, m]) > 1]
    # One year can still support its own margin when the other year's amount is absent.
    partial = {}
    for side in (0, 1):
        keys = [(side, m) for m in METRICS]
        if not all(len(values[k]) == 1 for k in keys):
            continue
        for table in tables.values():
            if all(len(table.get(k, [])) == 1 for k in keys):
                income, revenue = (table[k][0] for k in keys)
                if all(income[k] == revenue[k] for k in ("period_start", "period_end")):
                    partial[side] = (income, revenue)
                    break
    state["partial_pairs"] = partial
    if missing or conflicts:
        return None, "；".join(filter(None, ("本次检索的合并利润表缺少可引用的" + "、".join(missing) if missing else "",
                               "存在相互冲突的" + "、".join(conflicts) if conflicts else ""))) + "，无法核算营业利润率同比变化。"
    for table in tables.values():
        if all(len(table.get(key, [])) == 1 for key in required):
            if all(table[side, METRICS[0]][0]["period_start"] == table[side, METRICS[1]][0]["period_start"]
                   and table[side, METRICS[0]][0]["period_end"] == table[side, METRICS[1]][0]["period_end"]
                   for side in (0, 1)):
                selected = {key: table[key][0] for key in required}
                for side in (0, 1):
                    key = (side, "gross_profit")
                    if (len(values[key]) == len(table.get(key, [])) == 1
                            and all(table[key][0][k] == selected[side, "revenue"][k] for k in ("period_start", "period_end"))):
                        selected[key] = table[key][0]
                return selected, None
    return None, "营业利润与营业收入未形成同一张合并利润表中期间一致、口径可比的两组季度金额。"


def _period_names(state):
    label = state["quarter"]
    return (label, previous_quarter(label, True)) if label else (f"截至 {state['end']} 的季度", "上年同期季度")


_DRIVER = re.compile(r"(?:driven by|(?:primarily |largely )?due to|attributable to|because of|"
                     r"reflect(?:ing|ed|s) |result(?:ed|ing) from|offset (?:in part )?by)", re.I)
_SUBJECT = re.compile(r"^(?:(?:our|consolidated|the company.s) )?(gross (?:margin(?: percentage)?|profit)|"
                      r"operating (?:income|profit|margin|expenses?))\s+(?:was |were |has |had |increased|decreased|"
                      r"declined|expanded|grew|contracted|improved|rose|fell)", re.I)
_SECTION = re.compile(r"^(?:fiscal\s+)?((?:(?:first|second|third|fourth) quarter)|(?:full[- ]year|fiscal year|annual))"
                      r"(?:\s+(?:of\s+)?20\d{2})?\s+(?:financial\s+)?(?:highlights|results|performance)\s*:?\s*$", re.I)


def _quarter_sentences(text, mixed):
    quarterly = not mixed
    for line in text.splitlines():
        line = re.sub(r"^\s*L\d+:\s*", "", line).strip()
        heading = _SECTION.match(line)
        if heading:
            quarterly = "quarter" in heading[1].lower()
            continue
        for part in re.split(r"(?<=[.!?])\s+|•+", line):
            sentence = part.strip(" \t•·")
            if quarterly or re.search(r"\b(?:for|during|in) (?:the |our )?(?:(?:first|second|third|fourth) )?quarter\b", sentence, re.I):
                yield sentence


def _drivers(state, evidence):
    refs = set(state.get("driver_spans", []))
    by_subject = {}
    for row in evidence:
        if row["id"] not in refs or row.get("stale") or row.get("kind") != "document":
            continue
        mixed = row.get("document_id") in state.get("mixed_period_documents", ())
        for sentence in _quarter_sentences(row["text"], mixed):
            match = _SUBJECT.match(sentence)
            if (not match or not _DRIVER.search(sentence) or not sentence.endswith((".", "!", "?"))
                    or len(sentence) > 450 or re.search(r"full[- ]year|year[- ]to[- ]date|six months|nine months|"
                                                        r"expected|guidance|outlook|projected", sentence, re.I)):
                continue
            by_subject.setdefault(match[1].lower(), (sentence, row["id"]))
    priority = ("operating margin", "gross margin percentage", "operating expenses", "operating income", "gross margin", "gross profit")
    return [by_subject[k] for k in priority if k in by_subject][:3]


def _finish(state, ctx):
    selected = state.get("selected")
    calculations = {c["id"]: c for c in ctx["calculations"] if not c.get("stale")}
    notes = []
    current, prior = _period_names(state)
    fact_refs = ([selected[side, metric]["id"] for side in (0, 1) for metric in METRICS] if selected
                 else state.get("available_operands", []))
    numeric = None
    if selected and len(state["calculations"]) >= 3 and all(i in calculations for i in state["calculations"][:3]):
        refs = state["calculations"][:3]
        now, then, delta = (calculations[i]["value"] for i in refs)
        amount_text = []
        for side, period, margin in ((0, current, now), (1, prior, then)):
            income, sales = (selected[side, m] for m in METRICS)
            amount_text.append(f"{period}（{sales['period_start']} 至 {sales['period_end']}）"
                               f"营业利润 {income['value']:,} ÷ 营业收入 {sales['value']:,}（{sales['currency']}）"
                               f" = {margin:.2f}%")
        direction = "上升" if delta > 0 else "下降" if delta < 0 else "变化"
        text = (f"{state['entity']} 合并营业利润率同比{direction} {abs(delta):.2f} 个百分点。" + "；".join(amount_text)
                + "。百分点差按两期未舍入的利润率计算。")
        numeric = {"text": text, "evidence_ids": fact_refs, "calculation_ids": refs}
        notes.append(numeric)

    attribution = None
    attribution_missing = None
    if state["explain"]:
        found = _drivers(state, ctx["evidence"])
        evidence_ids = list(fact_refs)
        calc_ids = []
        lines = []
        bridge = state.get("margin_reconciliation")
        if bridge:
            calc_ids = bridge["calculation_ids"]
            evidence_ids.extend(bridge["evidence_ids"])
            attribution_missing = bridge["missing_support"] if bridge["status"] == "partial" else None
            lines.append(bridge["summary"].removesuffix(attribution_missing or ""))
        if found:
            lines.append("公告对相关项目的解释为：" + " ".join(f"“{sentence}”" for sentence, _ in found))
            evidence_ids.extend(ref for _, ref in found)
        elif numeric and not lines:
            lines.append("利润表能核对营业利润相对收入的变化。")
        elif not numeric and not bridge:
            lines.append("两期收入和营业利润未能完整配对，无法从原表核定营业利润率同比差额及各原因的影响。")
        if not state.get("expense_followup") and not any(re.search(r"operating (?:profit )?margin.{0,130}(?:basis|percentage) points?.{0,130}(?:driven|due|attribut)", s, re.I)
                   for s, _ in found):
            lines.append("已检索的公告段落未把合并营业利润率的百分点变化逐项归因到具体经营因素。")
        if state.get("attribution_gap"):
            lines.append(state["attribution_gap"])
        attribution = {"text": "".join(lines), "evidence_ids": list(dict.fromkeys(evidence_ids)), "calculation_ids": calc_ids}
        if evidence_ids:
            notes.append(attribution)
    state["notes"] = notes
    partial_note = None
    if not numeric and state.get("partial_pairs"):
        texts, refs, calc_refs = [], [], []
        for side, (income, sales) in state["partial_pairs"].items():
            pair = [income["id"], sales["id"]]
            calc = next((calculations[i] for i in state["calculations"] if i in calculations
                         and calculations[i]["operation"] == "margin" and calculations[i]["operand_ids"] == pair), None)
            if calc:
                texts.append(f"可单独核实{(current, prior)[side]}（{sales['period_start']} 至 {sales['period_end']}）"
                             f"营业利润率 {calc['value']:.2f}%（营业利润 {income['value']:,} ÷ 营业收入 {sales['value']:,}，{sales['currency']}）。")
                refs.extend(pair)
                calc_refs.append(calc["id"])
        if texts:
            partial_note = {"text": "".join(texts), "evidence_ids": refs, "calculation_ids": calc_refs}
            notes.insert(0, partial_note)
    findings = {}
    for key, question in ctx.get("request_parts", {}).items():
        wants_result = bool(MARGIN.search(question))
        wants_causes = bool(ATTRIBUTION.search(question) or state.get("expense_followup") and len(ctx.get("request_parts", {})) == 1)
        if not (wants_result or wants_causes):
            continue
        if state.get("gap") and (wants_result or not attribution or not attribution["evidence_ids"]):
            findings[key] = {"status": "gap", "missing_support": (partial_note["text"] if partial_note else "") + state["gap"],
                             "quarter": state["quarter"], "evidence_ids": fact_refs,
                             "calculation_ids": partial_note["calculation_ids"] if partial_note else []}
            continue
        items = [item for wanted, item in ((wants_result, numeric), (wants_causes, attribution)) if wanted and item]
        if items and any(n["evidence_ids"] for n in items):
            findings[key] = {"status": "source_excerpt", "quarter": state["quarter"], "answer_sentence": " ".join(n["text"] for n in items),
                             "evidence_ids": list(dict.fromkeys(i for n in items for i in n["evidence_ids"])),
                             "calculation_ids": list(dict.fromkeys(i for n in items for i in n["calculation_ids"]))}
            if wants_causes and attribution_missing:
                findings[key].update(status="partial", missing_support=attribution_missing)
    state["part_findings"] = findings


def next_action(state, ctx):
    steps = state["steps"]
    if not steps:
        if state.get("expense_followup") and state["end"]:
            args = {"entity": state["entity"], "period_end": state["end"]}
            if state["start"]:
                args["period_start"] = state["start"]
            if margin_reconciliation(ctx["evidence"], **args):
                state["phase"] = "reconcile"
                return tool("reconcile_operating_margin", **args)
        return tool("discover_company", query=state["entity"], source="replay")
    phase = state.get("phase", "documents")
    if phase == "documents":
        step = _document_action(state, ctx)
        if step:
            return step
    elif phase == "amounts":
        state["selected"], state["gap"] = _select(state, ctx["evidence"])
        if state["explain"]:
            state["phase"] = "drivers"
            return _search(state, DRIVERS_QUERY)
    elif phase == "drivers":
        last = ctx["episode"].get("last_tool_result", {})
        state["driver_spans"] = [r["id"] for r in last.get("evidence", []) if r.get("kind") == "document"]
    elif phase == "reconcile":
        last = ctx["episode"].get("last_tool_result", {})
        state["margin_reconciliation"] = last.get("data", {}).get("margin_reconciliation")
        if not state["margin_reconciliation"]:
            state["attribution_gap"] = "本次取得的利润表单元格未能形成可核算的费用明细。"
        _finish(state, ctx)
        return None
    elif phase in {"calculate", "partial_calculate"} and not steps[-1]["ok"]:
        failure = "计算工具未能接受所选原表操作数。" + str(ctx["episode"].get("last_tool_result", {}).get("error") or "")
        state["gap"] = (state.get("gap") or "") + failure
        _finish(state, ctx)
        return None
    selected = state.get("selected")
    if selected and not state.get("gap"):
        state["phase"] = "calculate"
        done = len(state["calculations"])
        if done < 3:
            if done % 3 == 2:
                return tool("calculate", operation="percentage_point_change", operand_ids=state["calculations"][-2:])
            side = done % 3
            return tool("calculate", operation="margin", operand_ids=[selected[side, "operating_income"]["id"], selected[side, "revenue"]["id"]])
        if state["explain"]:
            state["phase"] = "reconcile"
            cell = selected[0, "revenue"]
            return tool("reconcile_operating_margin", entity=state["entity"], period_start=cell["period_start"],
                        period_end=cell["period_end"], document_id=cell["document_id"])
    elif state.get("partial_pairs"):
        pairs = list(state["partial_pairs"].values())
        done = len(state["calculations"])
        if done < len(pairs):
            state["phase"] = "partial_calculate"
            return tool("calculate", operation="margin", operand_ids=[c["id"] for c in pairs[done]])
    _finish(state, ctx)
    return None
