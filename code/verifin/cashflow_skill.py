"""Source-addressed operating cash flow bridges for native English HTML disclosures."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re

from .cashflow_delivery import cash_amount, difference_text
from .revenue_bridge import dom, first_label, layout, month_start
from .statement_amount_cells import (CURRENCIES, CURRENCY, DOLLARS, DURATION, NUMBERS,
                                     SCALE, nearby, text)
from .structured_documents import _hidden


MONTH_NAMES = {name.lower(): i for i in range(1, 13)
               for name in (calendar.month_name[i], calendar.month_abbr[i])}
DATE_PART = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?(?:\s+(\d{4}))?\b")
CFO = re.compile(r"(?:gaap )?(?:net )?cash (?:flows? )?(?:(?:provided by|used in|used for)(?:\s*\([^)]*\))? |from )?(?:operating activities|operations)$", re.I)
START = re.compile(r"(?:consolidated )?net (?:income|earnings|loss)(?:\s*\((?:income|earnings|loss)\))?$", re.I)
# Issuer-specific scope qualifiers remain part of the group label. A pivot must also
# be a heading without amounts, as checked in _bridge_rows.
PIVOT = re.compile(r"changes? in (?:(?:current|operating) )?assets and (?:(?:current|operating) )?liabilities"
                   r"(?:,\s*[^:]+|\s*\([^:]+\))?\s*:?$", re.I)
SUBTOTAL = re.compile(r"total (?:adjustments|changes in (?:operating |current )?assets and liabilities)\b", re.I)
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
ACCOUNTS = [
    (r"应收|receivables?", r"(?:accounts?|trade) receivables?(?:,? net.*)?", "应收账款", True),
    (r"应付(?:账款|款)?|payables?", r"(?:accounts?|trade) payables?(?:,? net.*)?", "应付账款", False),
    (r"库存|存货|inventor(?:y|ies)", r"inventor(?:y|ies)(?:,? net.*)?", "存货", True),
]


@dataclass(frozen=True, order=True)
class Period:
    end: date
    months: int  # zero identifies a balance-sheet date

    start: date | None = field(default=None, compare=False)
    weeks: int | None = field(default=None, compare=False)

    def label(self):
        if self.weeks:
            return f"截至 {self.end} 的 {self.weeks} 周"
        return f"{self.end} 期末" if not self.months else f"截至 {self.end} 的 {self.months} 个月"


@dataclass
class Table:
    doc: dict
    rows: list
    labels: list
    columns: dict
    unit: str
    kind: str
    paths: dict
    title: str
    headings: dict
    unit_node: object
    element: object
    disputed: frozenset = frozenset()
    currency_reference: dict | None = None


class Missing(ValueError):
    """The requested comparison cannot be read unambiguously."""


def _dates(rows, instant):
    """Bind each date leaf to its own duration ancestor, including split year leaves."""
    durations = []
    columns, headings = {}, {}
    for row in rows:
        for span, value, node in row:
            if DURATION.search(value):
                durations.append((span, value, node))
        for span, value, node in row:
            year = re.fullmatch(r"(?:19|20)\d\d", value)
            md = DATE_PART.fullmatch(value.strip(", "))
            combined = DURATION.search(value)
            if combined:
                md = DATE_PART.fullmatch(value[combined.end():].strip(" ,"))
            if not (year or (md and md[3])):
                continue
            owners = [(v, n) for c, v, n in durations if c.start <= span.start and span.stop <= c.stop]
            if (not instant and len(owners) != 1) or (instant and owners):
                continue
            heading, owner = owners[0] if owners else ("", None)
            if year:
                md = DATE_PART.search(heading)
            if md is None or md[1].lower() not in MONTH_NAMES:
                continue
            end = date(int(value if year else md[3]), MONTH_NAMES[md[1].lower()], int(md[2]))
            weeks, start, months = None, None, 0
            if not instant:
                captures = list(DURATION.finditer(heading))
                if len(captures) != 1:
                    continue
                count, unit, named = captures[0].groups()
                if named:
                    months = 3 if named.lower().startswith("quarter") else 12
                else:
                    number = int(count) if count.isdigit() else NUMBERS[count.lower()]
                    if unit.lower().startswith("week"):
                        weeks = number
                        months = {13:3, 14:3, 26:6, 27:6, 39:9, 40:9, 52:12, 53:12}.get(weeks)
                    else:
                        months = number
                if months not in (3, 6, 9, 12):
                    continue
                if weeks:
                    start = end - timedelta(weeks=weeks) + timedelta(days=1)
                elif end.day == calendar.monthrange(end.year,end.month)[1]:
                    start = month_start(end, months)
            p = Period(end, months, start, weeks)
            if p in columns:
                raise Missing("同一表头出现多个相同期间")
            columns[p] = span
            headings[p] = [(heading, owner)] if owner is node else owners+[(value,node)]
        if columns:
            break
    return columns, headings


def _table(doc, element, paths, title, iso_units):
    rows = layout(element)
    labels = [first_label(r)[0] for r in rows]
    cf = [i for i, label in enumerate(labels) if CFO.fullmatch(label) and _has_amount(rows[i])]
    if len(cf) == 1:
        kind = "bridge" if any(START.fullmatch(s) for s in labels[:cf[0]]) else "total"
    elif any(s.lower() == "total assets" for s in labels) and any("liabilities" in s.lower() for s in labels):
        kind = "balances"
    else:
        return None
    header_end = next((i for i, label in enumerate(labels) if START.fullmatch(label)
                       or (cf and i == cf[0]) or (kind == "balances" and label.lower().rstrip(":") == "assets")),len(rows))
    headers = rows[:header_end]
    captions = nearby(element)
    unit_notes = [(text(n), n) for n in captions] + [(v,n) for r in headers for _,v,n in r]
    context = " ".join(v for v,_ in unit_notes)
    if re.search(r"\b(?:projected|forecast|segment|pro forma|non[- ]GAAP|unconsolidated|parent.only)\b", context, re.I):
        return None
    unit_sources = [(v,n) for v,n in unit_notes if re.search(r"\bin (thousands|millions|billions)\b",v,re.I)]
    scales = {s.lower() for v,_ in unit_sources for s in re.findall(r"\b(thousands|millions|billions)\b",v,re.I)}
    if (len(scales) != 1 or any(re.search(r"\bin (?:thousands|millions|billions) of (?:shares|units)\b",v,re.I)
                                  for v,_ in unit_sources)):
        return None
    scale = scales.pop()
    currencies = {CURRENCIES.get(s.upper(),s.upper()) for s in CURRENCY.findall(context)}
    if doc.get("currency"):
        currencies.add(doc["currency"])
    currencies.update(iso_units[r] for r in element.xpath(".//*[@unitref]/@unitref") if r in iso_units)
    signs = {s for row in rows for _,v,_ in row for s in re.findall(r"[$€£]",v)}
    signs.update(re.findall(r"[$€£]",context))
    if len(currencies) > 1 or len(signs) > 1:
        return None
    currency = next(iter(currencies or signs), None)
    compatible = {"$": DOLLARS | {"$"}, "€":{"EUR","€"}, "£":{"GBP","£"}}
    if not currency or any(currency not in compatible[s] for s in signs):
        return None
    columns, headings = _dates(headers, kind == "balances")
    if not columns:
        return None
    table = Table(doc, rows, labels, columns, f"{currency} {scale}", kind, paths, title, headings,
                  unit_sources[0][1], element)
    witnesses = doc.get("cash_currency_evidence", {})
    if kind == "bridge" and currency == "$" and witnesses:
        matched = {}
        for p in columns:
            if not p.start or str(p.end) >= doc.get("period_end", ""):
                continue
            corroborated = []
            for pattern, metric in ((START,"net_income"),(CFO,"operating_cash_flow")):
                try:
                    value, xpath = _cell(table,_index(table,pattern),p)
                    amount = value * SCALE[scale]
                except Missing:
                    break
                candidates = witnesses.get((metric,str(p.start),str(p.end),amount),{})
                corroborated.append({code: {**source, "matched_table_cell":xpath,
                                            "value_in_base_currency":str(amount)} for code,source in candidates.items()})
            if len(corroborated) == 2:
                for code in corroborated[0].keys() & corroborated[1].keys():
                    matched[code] = [entry[code] for entry in corroborated]
        if len(matched) > 1 or any(code not in DOLLARS for code in matched):
            return None
        if matched:
            currency, sources = next(iter(matched.items()))
            table.unit = f"{currency} {scale}"
            table.currency_reference = {"currency": currency, "matching_filed_operands": sources}
    return table


def _cell(table, idx, period):
    if idx in table.disputed:
        raise Missing(f"{table.labels[idx]} 在同期间的披露之间有冲突")
    row, span = table.rows[idx], table.columns[period]
    label, label_node = first_label(row)
    if (not re.search(r"[a-z]",label,re.I) or
            any(n is label_node and c.stop > span.start for c,_,n in row)):
        raise Missing(f"{period.label()} 的金额缺少独立的项目名称")
    if not row or row[-1][0].stop < max(c.stop for c in table.columns.values()):
        raise Missing(f"金额列不完整：{table.labels[idx]}，{period.label()}")
    cells = [(s,n) for c,s,n in row if s and c.start < span.stop and span.start < c.stop]
    if any((c.start < span.start or c.stop > span.stop or n.get("rowspan","1") != "1")
           for c,s,n in row if s and c.start < span.stop and span.start < c.stop):
        raise Missing(f"单元格跨越期间：{table.labels[idx]}")
    values = [(s,n) for s,n in cells if re.search(r"[\d—–−-]",s)]
    printed = "".join(s for s,_ in cells).strip()
    if re.search(r"\d\s+\d", printed):
        raise Missing(f"金额单元格有多段数值：{table.labels[idx]}，{period.label()}")
    printed = re.sub(r"[$€£\s]", "", printed)
    if printed in {"—", "–", "-"}:
        printed = "0"
    if len(values) != 1 or not re.fullmatch(rf"(?:[-+−]?{NUMBER}|\({NUMBER}\))",printed):
        raise Missing(f"金额缺失或列含义不明：{table.labels[idx]}，{period.label()}")
    val = Decimal(printed.replace(",", "").replace("−", "-").replace("(", "-").replace(")", ""))
    return val, table.paths[values[0][1]]


def _fact(table, idx, period, result):
    p = next(p for p in table.columns if p == period)
    val, xpath = _cell(table, idx, p)
    key = result.fact(table.doc, xpath, table.labels[idx], val, table.unit, p.label())
    row = table.rows[idx]
    result.evidence[key].update(
        row_label=table.labels[idx], row_label_locator=table.paths[first_label(row)[1]],
        table_xpath=table.paths[table.element], column_headers=[h for h,_ in table.headings[p]],
        column_header_locators=[table.paths[n] for _,n in table.headings[p]],
        period_start=str(p.start) if p.start else None, period_end=str(p.end),
        printed_unit=text(table.unit_node), unit_locator=table.paths[table.unit_node],
        printed_scale=SCALE[table.unit.split()[-1]], currency=table.unit.split()[0],
        printed_value=" ".join(s for c,s,_ in row if s and c.start >= table.columns[p].start
                               and c.stop <= table.columns[p].stop))
    if table.currency_reference:
        result.evidence[key]["currency_reference"] = table.currency_reference
    if table.kind in {"bridge", "total"} and p.months:
        label = table.labels[idx]
        series = ("operating_cash_flow" if CFO.fullmatch(label) else "net_income" if START.fullmatch(label)
                  else "row:" + " ".join(label.casefold().rstrip(":").split()))
        result.evidence[key]["cash_flow_series"] = series
    return key


def _has_amount(row):
    label_node = first_label(row)[1]
    return any(n is not label_node and re.search(r"\d|[—–]", value) for _,value,n in row)


def _index(table, pattern):
    hits = [i for i, label in enumerate(table.labels) if pattern.fullmatch(label)
            and (pattern is not CFO or _has_amount(table.rows[i]))]
    if len(hits) != 1:
        name = {START:"净利润", CFO:"经营现金流总额", PIVOT:"经营资产负债变动分组"}.get(pattern,"指定项目")
        raise Missing(f"原表未列出唯一的{name}行")
    return hits[0]


def _bridge_rows(table):
    first, mid, last = [_index(table, pattern) for pattern in (START, PIVOT, CFO)]
    if not first < mid < last:
        raise Missing("缺少从合并净利润到经营现金流的连续调节")
    aggregate = _has_amount(table.rows[mid])
    if aggregate:
        # Some statements print aggregate changes as signed rows, without a heading
        # or any breakdown. Refuse to combine an aggregate with its own components.
        following = [s for s in table.labels[mid+1:last] if s]
        other = r"changes? in (?:other )?(?:long[- ]term|noncurrent) (?:operating )?assets and (?:other )?(?:long[- ]term|noncurrent) (?:operating )?liabilities"
        if (not re.fullmatch(r"changes? in current assets and current liabilities", table.labels[mid], re.I)
                or len(following) > 1 or any(not re.fullmatch(other, s, re.I) for s in following)):
            raise Missing("经营资产负债变动行有金额，无法证明后续项目与该小计互不重叠")
    elif any(n is not first_label(table.rows[mid])[1] and value for _,value,n in table.rows[mid]):
        raise Missing("经营资产负债变动行列有金额，不能把它当作分组标题")
    groups = ([], [])
    for i in range(first+1, last):
        label = table.labels[i]
        if ((i == mid and not aggregate) or label.lower().startswith("adjustments to reconcile") or
                SUBTOTAL.match(label) or not label):
            continue
        groups[int(i >= mid)].append(i)
    if not all(groups):
        raise Missing("报表未列出两个可单独识别的调节组")
    return first, groups, last


def _bridge(table, period, result):
    p = next(p for p in table.columns if p == period)
    metadata = dict(period_end=str(p.end), period_start=str(p.start) if p.start else None)
    endpoints = {}
    for tag, pattern, display in (("net", START, "合并净利润"), ("cfo", CFO, "经营现金流")):
        try:
            idx = _index(table,pattern)
            key = _fact(table,idx,p,result)
            endpoints[tag] = key
        except Missing as exc:
            result.gap(f"{p.label()} 的{display}未核实，{exc}")
    if len(endpoints) == 2:
        net, cfo = (endpoints[tag] for tag in ("net","cfo"))
        metadata["cash_amount"] = cash_amount(result, cfo)
        gap = result.calc(f"{p.label()} 经营现金流减净利润", "difference", [cfo,net], table.unit)
        result.note(f"{p.label()} 合并净利润 {result.value(net):,}，经营现金流 {result.value(cfo):,}，"
                    f"经营现金流减净利润为 {result.value(gap):+,}（{table.unit}）。"
                    f"{difference_text(result.value(gap),table.unit)}。",
                    [net,cfo,gap], kind="cash_bridge", complete=True, **metadata)
    elif endpoints:
        key = next(iter(endpoints.values()))
        if "cfo" in endpoints:
            metadata["cash_amount"] = cash_amount(result, key)
        result.note(f"{p.label()} {result.record(key)['metric']} 为 {result.value(key):,}（{table.unit}）。",
                    [key], kind="cash_bridge", complete=False, **metadata)
    if table.kind == "total":
        result.gap(f"{p.label()} 只有经营现金流总额，缺少单季间接法明细")
        return
    try:
        _, groups, _ = _bridge_rows(table)
    except Missing as exc:
        result.gap(f"{p.label()} 的调节分组未核实，{exc}")
        return
    components = []
    complete = True
    for group in groups:
        refs = []
        for idx in group:
            try:
                refs.append(_fact(table,idx,p,result))
            except Missing as exc:
                complete = False
                result.gap(str(exc))
        components.append(refs)
    tied = (complete and len(endpoints) == 2 and
            sum(Decimal(str(result.value(k))) for refs in components for k in refs) == Decimal(str(result.value(gap))))
    if not tied:
        result.gap(f"{p.label()} 调节明细未能完整对到经营现金流，分组合计及完整归因待核实。")
    group_ids = []
    group_heading = table.labels[_index(table,PIVOT)].rstrip(":")
    for group, refs in enumerate(components):
        if not refs:
            continue
        if group:
            description = f"经营资产负债变动（{group_heading}）"
        else:
            noncash = all(re.search(r"depreciation|amorti[sz]ation|(?:stock|share)[- ]based compensation|"
                                    r"deferred (?:income )?tax|impairment|non[- ]?cash",table.labels[i],re.I)
                          for i in groups[0])
            description = "非现金调节" if noncash else "经营资产负债变动前的调节"
        sorted_refs = sorted(refs, key=lambda k: abs(result.value(k)), reverse=True)
        items = "；".join(f"{result.record(k)['row_label']} {result.value(k):+,}" for k in sorted_refs)
        prefix = f"{description}已核实原行"
        if tied:
            subtotal = result.calc(f"{p.label()} {description}", "sum", refs, table.unit)
            group_ids.append(subtotal)
            prefix = f"{description}合计 {result.value(subtotal):+,}"
        result.note(f"{p.label()} {prefix}（{table.unit}）。正数调增经营现金流，负数调减。按绝对影响排序，{items}。",
                    sorted_refs + ([subtotal] if tied else []), kind="cash_drivers", complete=tied, **metadata)
    if tied:
        computed = result.calc(f"{p.label()} 净利至经营现金流", "sum", [net,*group_ids], table.unit)
        residual = result.calc(f"{p.label()} 桥接对账残差", "difference", [cfo,computed], table.unit)
        result.note(f"本期以现金流量表的净利润加上两组调节，复算经营现金流为 {result.value(computed):,}，"
                    f"与披露额的差为 {result.value(residual):,}（{table.unit}）。", [net,*group_ids,cfo,computed,residual],
                    kind="cash_reconciliation", complete=True, **metadata)


def _read_documents(documents):
    tables = []
    if len({d.get("entity") for d in documents}) != 1:
        raise Missing("需要同一发行人的报表")
    for doc in documents:
        raw = doc["raw"]
        if doc.get("snapshot_hash") and sha256(raw).hexdigest() != doc["snapshot_hash"]:
            raise Missing("原件与登记的快照哈希不符")
        if b"<table" not in raw.lower():
            continue
        root = dom(raw)
        tree = root.getroottree()
        paths = {node: tree.getpath(node) for node in root.iter() if node.tag in {
            "td","th","table","div","span","p","h1","h2","h3","h4","caption"}}
        iso_units = {}
        for unit in root.xpath("//*[local-name()='xbrli:unit']"):
            measures = list(unit)
            if len(measures) == 1 and measures[0].tag == "xbrli:measure" and unit.get("id"):
                code = re.fullmatch(r"iso4217:([A-Z]{3})", text(measures[0]), re.I)
                if code:
                    iso_units[unit.get("id")] = code[1].upper()
        fiscal = {name: {text(n) for n in root.xpath("//*[@name=$name]", name="dei:"+name)}
                  for name in ("DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus", "DocumentPeriodEndDate")}
        title = " ".join(" ".join(n.itertext()) for n in root.xpath("//title|//h1"))
        if all(len(v) == 1 for v in fiscal.values()) and doc.get("period_end"):
            printed = next(iter(fiscal["DocumentPeriodEndDate"]))
            parsed = DATE_PART.fullmatch(printed)
            if parsed and parsed[3] and parsed[1].lower() in MONTH_NAMES:
                printed = str(date(int(parsed[3]),MONTH_NAMES[parsed[1].lower()],int(parsed[2])))
            if printed == doc["period_end"]:
                title += " FY" + next(iter(fiscal["DocumentFiscalYearFocus"])) + " " + next(iter(fiscal["DocumentFiscalPeriodFocus"]))
        for node in list(root.iterdescendants()):
            if isinstance(node.tag, str) and _hidden(node) and node.getparent() is not None:
                node.drop_tree()
        # HTML line breaks separate visible words even when adjacent text nodes
        # have no whitespace. Preserve real break boundaries before reading the grid.
        for br in root.iter("br"):
            br.tail = " " + (br.tail or "")
        for element in root.xpath("//table[not(.//table)]"):
            try:
                candidate = _table(doc, element, paths, title, iso_units)
            except (Missing, ValueError, OverflowError):
                continue
            if candidate:
                tables.append(candidate)
    return tables


def _same_start(closing, opening, months):
    # Calendar month-end or 52/53-week reporting year (26-week half).
    gap = (closing - opening).days
    m = (closing.year*12 + closing.month) - (opening.year*12 + opening.month)
    calendar_end = all(d.day == calendar.monthrange(d.year, d.month)[1] for d in (opening, closing))
    return (m == months and calendar_end) or (months == 12 and gap in (364, 371)) or (months == 6 and gap in (182, 189))


def _one(tables, period, kinds):
    candidates = [t for t in tables if t.kind in kinds and period in t.columns]
    if not candidates:
        raise Missing(f"{period.label()} 缺少可核验的{'/'.join(kinds)}表")
    if len(candidates) == 1:
        return candidates[0]
    periods = [next(p for p in t.columns if p == period) for t in candidates]
    units = {t.unit.split()[0] for t in candidates}
    known = units - {"$","€","£"}
    symbols = units-known
    compatible = {"$":DOLLARS, "€":{"EUR"}, "£":{"GBP"}}
    if (len({p.start for p in periods}) != 1 or len({t.unit.split()[-1] for t in candidates}) != 1
            or len(known) > 1 or len(symbols) > 1 or
            any(not known.issubset(compatible[symbol]) for symbol in symbols)):
        raise Missing(f"{period.label()} 多份披露的期间或单位冲突")
    def source_rank(t):
        return (bool(re.fullmatch(r"10-[KQ](?:/A)?", t.doc.get("form", ""), re.I)),
                t.unit.split()[0] in known, t.doc["published_at"], t.doc["url"])
    selected = max(candidates, key=source_rank)
    disputed = set()
    for other in candidates:
        if other is selected:
            continue
        for i, label in enumerate(selected.labels):
            if not label:
                continue
            if START.fullmatch(label) or CFO.fullmatch(label):
                pattern = START if START.fullmatch(label) else CFO
                matches = [j for j,s in enumerate(other.labels) if pattern.fullmatch(s)]
            else:
                matches = [j for j,s in enumerate(other.labels) if s.casefold()==label.casefold()]
            if len(matches) > 1:
                disputed.add(i)
            elif matches:
                try:
                    if _cell(selected,i,period)[0] != _cell(other,matches[0],period)[0]:
                        disputed.add(i)
                except Missing:
                    continue
    return replace(selected,disputed=frozenset(disputed))


def _account(table, p, item, result):
    _, pattern, zh, asset = item
    pivot = _index(table, PIVOT)
    matches = [i for i in range(pivot + 1, _index(table,CFO)) if re.fullmatch(pattern, table.labels[i], re.I)]
    if len(matches) != 1:
        raise Missing(f"{p.label()} 未找到单独的{zh}现金调节行")
    i = matches[0]
    v, _ = _cell(table,i,p)
    ref = _fact(table,i,p,result)
    effect = "释放" if v > 0 else "占用" if v < 0 else "净影响为零"
    result.note(f"{p.label()} {zh}对经营现金流的调节为 {v:+,}（{table.unit}，{effect}）。"
                "该行是净影响，不能推出实际收付款总额。",[ref])
    return v, ref


def _balances(tables, table, p, item, amount, ref, result):
    _, pattern, zh, asset = item
    possible = []
    for t in tables:
        if t.kind != "balances" or t.doc is not table.doc or t.unit != table.unit:
            continue
        for closing in t.columns:
            for opening in t.columns:
                if closing.end == p.end and _same_start(closing.end, opening.end, p.months):
                    idx = [i for i, s in enumerate(t.labels) if re.fullmatch(pattern, s, re.I)]
                    if len(idx) == 1:
                        possible.append((t,idx[0],opening,closing))
    if len(possible) != 1:
        raise Missing(f"{p.label()} 缺少同单位且覆盖同一期间的{zh}期初、期末余额")
    t, i, opening, closing = possible[0]
    a, b = _cell(t,i,opening)[0], _cell(t,i,closing)[0]
    x, y = _fact(t,i,opening,result), _fact(t,i,closing,result)
    proxy = a-b if asset else b-a
    key = result.calc(f"{p.label()} {zh}带现金方向的余额差", "difference", [x,y] if asset else [y,x], t.unit)
    diff = result.calc(f"{p.label()} {zh}现金调节减余额代理", "difference", [ref,key], t.unit)
    result.note(f"{zh}期初 {a:,}、期末 {b:,}，按现金方向的余额代理 {proxy:+,}，"
                f"现金流原行与代理的差额 {amount-proxy:+,}（{t.unit}）。两表没有拆分该差额的单一原因。",
                [x,y,ref,key,diff])


def _months(text):
    found = []
    for months, pattern in ((12,r"全年|这一年|年度|年度内|full.year|annual"),
                            (6,r"半年|六个月|上半财年|half.year|six months"),
                            (3,r"[季]|quarter|\bQ[1-4]\b")):
        if re.search(pattern,text,re.I):
            found.append(months)
    return found


def _check_explicit_period(text, tables, end):
    """Bind a named quarter to a release heading. Never reinterpret a named historical period as the latest."""
    quarters = [int(n) for n in re.findall(r"\bQ([1-4])\b", text, re.I)]
    quarters.extend("一二三四".index(n)+1 if n in "一二三四" else int(n)
                    for n in re.findall(r"(?<![这那上前同每另])第?([一二三四1-4])(?:财季|季)", text))
    title_quarters, fiscal_years = set(), set()
    for t in tables:
        if t.doc.get("period_end", str(end)) != str(end) or not any(p.end == end for p in t.columns):
            continue
        for n in re.findall(r"\bQ([1-4])\b", t.title, re.I):
            title_quarters.add(int(n))
        for i, word in enumerate(("first", "second", "third", "fourth"), 1):
            if re.search(r"\b"+word+r"[- ]quarter\b", t.title, re.I):
                title_quarters.add(i)
        for n in re.findall(r"\bFY\s*(\d{2}|\d{4})\b", t.title, re.I):
            fiscal_years.add(2000+int(n) if len(n)==2 else int(n))
    if quarters and (len(set(quarters)) != 1 or set(quarters) != title_quarters):
        raise Missing("明确指定的财季与本次披露标题无法唯一对应")
    fiscal_pattern = r"(?:(?:FY|fiscal year)\s*(20\d\d)|(20\d\d)\s*财年)"
    requested_fy = {int(a or b) for a,b in re.findall(fiscal_pattern,text,re.I)}
    if requested_fy and requested_fy != fiscal_years:
        raise Missing("明确指定的财年与披露无法唯一对应")
    rest = re.sub(fiscal_pattern, "", text,flags=re.I)
    years = {int(s) for s in re.findall(r"(?<!\d)(20\d\d)(?!\d)", rest)}
    if years and years != {end.year}:
        raise Missing("明确指定的历史年份与当前披露不符")
    for y,m,d in re.findall(r"(20\d\d)[-年](\d{1,2})[-月](\d{1,2})(?:日)?", text):
        if date(int(y),int(m),int(d)) != end:
            raise Missing("明确指定的日期与报表期末不符")


def analyze(question, history, documents, result, *, quarter_end=None):
    """Analyze only supplied, already cutoff-filtered documents. Emit gaps for missing evidence."""
    try:
        tables = _read_documents(documents)
        detailed = [t for t in tables if t.kind == "bridge"]
        dated = [date.fromisoformat(d["period_end"]) for d in documents if d.get("period_end") and
                 re.search(r"10-[KQ]|8-K|release|issuer", d.get("source_form",""),re.I)]
        dates = {p.end for t in tables if t.kind in {"bridge","total"} for p in t.columns}
        if not dates:
            raise Missing("没有带期间表头的现金流量表")
        end = date.fromisoformat(quarter_end) if quarter_end else max(dated or dates)
        # A dated request must never silently fall back to a later available filing.
        _check_explicit_period(question, detailed, end)
        item = next((a for a in ACCOUNTS if re.search(a[0],question,re.I)),None)
        horizons = [3] if quarter_end else _months(question)
        prior = next((_months(s) for s in reversed(history) if _months(s)), [])
        if not horizons:
            horizons = prior
        elif item and 6 in horizons and 3 in prior and 3 not in horizons:
            horizons.append(3)
        if not horizons:
            raise Missing("需确定按单季、半年还是全年回答")
        if not item:
            if len(horizons) != 1:
                raise Missing("请区分目标期间及比较期间")
            periods = [Period(end,horizons[0])]
            if re.search(r"上年同|同比|去年同|year.over.year|prior.year", question,re.I):
                candidates = {p for t in detailed for p in t.columns if p.months == horizons[0] and
                              p.end.year == end.year-1 and _same_start(end,p.end,12)}
                if len(candidates) == 1:
                    periods.extend(candidates)
                else:
                    result.gap("缺少同长度、同财务季的上年比较列")
            for p in periods:
                if any(p in t.columns for t in detailed):
                    _bridge(_one(detailed,p,{"bridge"}),p,result)
                elif p.months == 3:
                    from .cashflow_period_bridge import derive_quarter
                    try:
                        derive_quarter(tables, p.end, result)
                    except Missing as exc:
                        if any(t.kind == "total" and p in t.columns for t in tables):
                            _bridge(_one(tables,p,{"total"}),p,result)
                        result.gap(str(exc))
                else:
                    _bridge(_one(tables,p,{"total"}),p,result)
            return
        seen = {}
        for months in horizons:
            p = Period(end,months)
            try:
                if not any(p in t.columns for t in detailed):
                    total = _one(tables,p,{"total"})
                    i = _index(total,CFO)
                    value = _cell(total,i,p)[0]
                    result.note(f"{p.label()} 经营现金流总额 {value:,}（{total.unit}）。",
                                [_fact(total,i,p,result)])
                    result.gap(f"{p.label()} 缺少{item[2]}单项经营现金调节，无法从其他期间或总额分摊")
                    continue
                t = _one(detailed,p,{"bridge"})
                value, ref = _account(t,p,item,result)
                seen[months] = (value,ref,t)
                if months in (6,12):
                    _balances(tables,t,p,item,value,ref,result)
            except Missing as exc:
                result.gap(str(exc))
        if 3 in seen and 6 in seen and seen[3][2] is seen[6][2]:
            half, quarter = seen[6], seen[3]
            q1 = result.calc("半年累计减同一报告期末季度，推算前一季度现金调节", "statement_quarter_residual",
                             [half[1], quarter[1]], half[2].unit)
            result.note(f"半年累计减第二季，可推算前一季{item[2]}现金影响 {half[0]-quarter[0]:+,}（{half[2].unit}）。",
                        [q1, half[1], quarter[1]])
    except (Missing, ValueError, InvalidOperation) as exc:
        result.gap(str(exc))
