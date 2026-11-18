"""Source-addressed restaurant/store profit reconciliations in issuer earnings releases."""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO


MONTH = r"(?:" + "|".join(calendar.month_name[1:]) + ")"
DURATION = re.compile(r"^(Three months|Quarter|(?:Fiscal )?Year|Twelve months) ended (" + MONTH + r" \d{1,2}),?$", re.I)
SCALE = re.compile(r"\bin (thousands|millions|billions)\b", re.I)
BASE = re.compile(r"(?:GAAP )?(?:(?:consolidated )?operating (?:income|profit)|income from operations)", re.I)
STORE = re.compile(r"(?:restaurant|store|unit)[ -](?:level[ -])?(?:operating |contribution )?(?:profit|margin|contribution)", re.I)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
PCT_DENOM = re.compile(r"(?:percentof|%of)(total(?:net)?revenues?|restaurantsales|companyoperatedstorerevenues?)", re.I)


def _period(question, history):
    from .skill_calculations import quarter_label
    prior = None
    for text in [*history, question]:
        q = quarter_label(text)
        if not q:
            reversed_q = re.search(r"(?:第\s*)?([一二三四1-4])\s*(?:季|财季)(?:度)?\s*(20\d\d)", text)
            if reversed_q:
                n = reversed_q[1]
                q = reversed_q[2] + "Q" + (str("一二三四".index(n) + 1) if n in "一二三四" else n)
        annual = re.search(r"(20\d\d)\s*年?\s*(?:全年|年度|财年)|(?:FY\s*|full [Ff]iscal [Yy]ear\s*|全年\s*)(20\d\d)", text, re.I)
        if q:
            prior = (int(q[:4]), int(q[-1]))
        elif annual:
            prior = (int(annual[1] or annual[2]), 0)
        elif prior and re.search(r"全年|年度|full year|annual", text, re.I):
            prior = (prior[0], 0)
    return prior


@dataclass
class Cell:
    label: str
    value: Decimal
    locator: str


def _number(text, *, percent=False):
    t = re.sub(r"[\s,]", "", text).replace("−", "-")
    if percent:
        t = t.removesuffix("%")
    else:
        t = re.sub(r"^(?:\$|USD)", "", t)
    negative = t.startswith("(") and t.endswith(")")
    if negative:
        t = t[1:-1]
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", t):
        return None
    return (-1 if negative else 1) * Decimal(t)


def _cell(row, column, *, percent=False):
    hits = [(text, node) for span, text, node in row if column in span and text]
    if len(hits) == 1:
        text, node = hits[0]
        value = _number(text, percent=percent)
        if value is not None:
            return Cell(row[0][1], value, node.getroottree().getpath(node))


def _quarter_in_title(text):
    matches = {int(n) for n in re.findall(r"\bQ([1-4])\b", text, re.I)}
    matches.update(ORDINALS[m.lower()] for m in re.findall(r"\b(first|second|third|fourth) (?:fiscal )?quarter\b", text, re.I))
    return matches.pop() if len(matches) == 1 else None


def _blocks(table):
    """A row for duration/date, then explicit year columns; stop before the next duration."""
    from .revenue_bridge import layout
    rows = layout(table)
    headers = []
    for i, row in enumerate(rows):
        matches = [m for _, text, _ in row if (m := DURATION.fullmatch(text))]
        if len(matches) == 1:
            headers.append((i, matches[0]))
    for j, (i, m) in enumerate(headers):
        end = headers[j + 1][0] if j + 1 < len(headers) else len(rows)
        if end <= i + 2:
            continue
        head = rows[i + 1]
        cols = [(span.start, int(text)) for span, text, _ in head if re.fullmatch(r"(?:19|20)\d\d", text)]
        if not cols or len(cols) != len({year for _, year in cols}):
            continue
        for k, (col, year) in enumerate(cols):
            stop = cols[k + 1][0] if k + 1 < len(cols) else max(s.stop for s, _, _ in head)
            perc = [(s.start, match.group(1).lower()) for s, text, _ in head if col < s.start < stop
                    and (match := PCT_DENOM.fullmatch(re.sub(r"[\s-]+", "", text)))]
            yield ("quarter" if re.match(r"three|quarter", m[1], re.I) else "annual", m[2].lower(),
                   year, col, perc[0] if len(perc) == 1 else None, rows[i + 2:end])


def _money_unit(title, rows):
    scales = {s.lower() for s in SCALE.findall(title)}
    if len(scales) != 1:
        return None
    currencies = set(re.findall(r"\b(?:USD|CAD|AUD|EUR|GBP)\b", title))
    symbol = "$" if any("$" in text for row in rows for _, text, _ in row[1:]) else None
    if len(currencies) == 1:
        currency = currencies.pop()
    elif not currencies and symbol:
        currency = symbol
    else:
        return None
    return f"{currency} {scales.pop()}"


def _bridge(rows, col, percent):
    gaaps = [i for i,row in enumerate(rows) if row and BASE.fullmatch(row[0][1])]
    stores = [i for i,row in enumerate(rows) if row and STORE.fullmatch(row[0][1])]
    if len(gaaps) != 1 or len(stores) != 1:
        return None
    first, last = sorted([gaaps[0], stores[0]])
    base, store = _cell(rows[gaaps[0]], col), _cell(rows[stores[0]], col)
    if not base or not store:
        return None
    items, totals = [], []
    for row in rows[first + 1:last]:
        if not row or not any(text for _, text, _ in row):
            continue
        label = row[0][1]
        if re.fullmatch(r"(?:non[ -]GAAP )?adjustments:?|(?:add|less):?", label, re.I) and not any(t for _,t,_ in row[1:]):
            continue
        cell = _cell(row, col)
        if not cell or not label:
            return None
        if re.match(r"^(?:sub[ -]?total|total)\b", label, re.I):
            totals.append(cell)
        else:
            items.append(cell)
    if not items or len(totals) > 1:
        return None
    subtotal = sum(c.value for c in items)
    if totals and totals[0].value != subtotal:
        return None
    forward = first == gaaps[0]
    if (base.value + subtotal != store.value) if forward else (store.value + subtotal != base.value):
        return None
    rates = [_cell(rows[i], percent[0], percent=True) for i in [gaaps[0], stores[0]]] if percent else []
    return dict(base=base, store=store, items=items, totals=totals, forward=forward, rates=rates,
                denominator=percent[1] if percent else None)


def _html(doc):
    from .revenue_bridge import dom, words
    tree = dom(doc["raw"])
    title = " ".join(words(n) for n in tree.xpath("//title|//h1"))
    quarter = _quarter_in_title(title)
    statements, bridges = defaultdict(list), defaultdict(list)
    for table in tree.xpath("//table[not(.//table)]"):
        first = table.xpath("./caption | (./tr|./thead/tr|./tbody/tr)[1]")
        caption = " ".join(words(n) for n in first)
        is_bridge = bool(re.search(r"reconcil", caption, re.I) and STORE.search(caption))
        is_statement = bool(re.search(r"consolidated statements? of (?:income|operations|earnings)", caption, re.I))
        if not (is_bridge or is_statement):
            continue
        blocks = list(_blocks(table))
        year_ends = {day for kind, day, *_ in blocks if kind == "annual"}
        for kind, day, year, col, percent, rows in blocks:
            # A quarter ending on the same day as the fiscal year is Q4.
            q = 0 if kind == "annual" else quarter or (4 if day in year_ends else None)
            if q is None:
                continue
            unit = _money_unit(caption, rows)
            if not unit:
                continue
            key = (year, q, day, unit)
            if is_bridge:
                bridge = _bridge(rows, col, percent)
                if bridge:
                    bridges[key].append(bridge)
            else:
                for row in rows:
                    if not row:
                        continue
                    name = re.sub(r"[\s-]", "", row[0][1].lower())
                    if PCT_DENOM.fullmatch("percentof" + name) and (cell := _cell(row, col)):
                        statements[key, name].append(cell)
    return bridges, statements


def _put(result, doc, period, cell, metric, unit):
    return result.fact(doc, cell.locator, metric, float(cell.value), unit, period, label=cell.label)


def _report_period(result, doc, key, bridge, statements):
    year, q, day, unit = key
    period = f"FY{year} Q{q}" if q else f"FY{year}"
    def fact(cell, metric, measure=unit):
        return _put(result, doc, period, cell, metric, measure)
    base = fact(bridge["base"], "group_operating_profit")
    store = fact(bridge["store"], "store_operating_profit")
    items = [fact(c, "unit_profit_adjustment") for c in bridge["items"]]
    ids = [base, store, *items]
    pref = period + " "
    summed = result.calc(pref + "调节明细合计", "sum", items, unit)
    start, end = (base, store) if bridge["forward"] else (store, base)
    checked = result.calc(pref + "依发行人调节方向复算终点", "sum", [start, *items], unit)
    ids.extend([summed, checked])
    if bridge["totals"]:
        ids.append(fact(bridge["totals"][0], "reported_total_adjustments"))
    result.note(f"{period}（{day} 止；{unit}）集团营业利润 {bridge['base'].value:,}，门店层利润 "
                f"{bridge['store'].value:,}。调节明细与两端金额闭合，小计只计一次。"
                + "、".join(f"{c.label} {c.value:,}" for c in bridge["items"]) + "。", ids)
    out = {"base":base,"store":store}
    rates = bridge["rates"]
    denominator = statements.get((key, bridge["denominator"]), [])
    if len(rates) != 2 or not all(rates) or len(denominator) != 1 or denominator[0].value <= 0:
        result.gap(f"{period} 暂无能同表头口径对上的唯一收入分母与两项利润率，已交付金额调节。")
        return out
    denom = denominator[0]
    amounts = [bridge["base"].value, bridge["store"].value]
    for amount, rate in zip(amounts, rates):
        half_tick = Decimal(1).scaleb(rate.value.as_tuple().exponent) / 2
        if abs(100 * amount / denom.value - rate.value) > half_tick:
            result.gap(f"{period} 所选收入分母不能在披露精度内复现两个利润率，已交付金额调节。")
            return out
    den = fact(denom, "unit_profit_denominator")
    pct_ids = []
    for name, amount, rate, code in zip(["集团营业","门店层面"],[base,store], rates, ["base_rate","store_rate"]):
        printed = fact(rate, code, "%")
        computed = result.calc(pref + name + "利润率复算", "margin", [amount, den], "%")
        out[code] = printed
        pct_ids.extend([printed, computed])
    result.note(f"两层利润率均以 {denom.label} {denom.value:,}（{unit}）为分母。"
                f"按原表精度，集团 {rates[0].value}%，门店 {rates[1].value}%。", [den, *pct_ids])
    return out


def _pdf_partial(doc, target, result):
    """Read only a consolidated table with dated amount columns and explicit store expense ratio."""
    import pdfplumber
    with pdfplumber.open(BytesIO(doc["raw"])) as pdf:
        cover = (pdf.pages[0].extract_text() or "")[:1000]
        q = _quarter_in_title(cover)
        if not target[1] or q != target[1]:
            return False
        for index,page in enumerate(pdf.pages[1:], 2):
            text = page.extract_text() or ""
            if not re.search(r"CONSOLIDATED STATEMENTS? OF (?:EARNINGS|OPERATIONS|INCOME)", text, re.I):
                continue
            header = re.search(r"Quarter Ended Quarter Ended\s+([A-Za-z]{3} \d{1,2},) [A-Za-z]{3} \d{1,2},[^\n]*\n"
                               r"(20\d\d) (20\d\d) Change \2 \3", text)
            scales = SCALE.findall(text[:350])
            if not header or int(header[2]) != target[0] or len(scales) != 1:
                continue
            labels = ("Company-operated stores", "Store operating expenses", "Operating income", "Total net revenues")
            cells = []
            period = f"FY{target[0]} Q{q}"
            for label in labels:
                match = re.search("^" + re.escape(label) + r"\s+(?:\$\s*)?([-\d,.()]+)(?=\s)", text, re.M|re.I)
                if match and (value := _number(match[1])) is not None:
                    cells.append(Cell(label, value, f"PDF p.{index} | {label} | {header[1]} {header[2]} amount"))
            ratio = re.search(r"^Store operating expenses as a % of company-operated store revenues\s+([\d.]+)\s*%", text, re.M|re.I)
            if len(cells) != 4 or not ratio or cells[0].value <= 0 or cells[3].value <= 0 or "$" not in text:
                continue
            printed = Decimal(ratio[1])
            if abs(100*cells[1].value/cells[0].value - printed) > Decimal(1).scaleb(printed.as_tuple().exponent)/2:
                continue
            unit = "$ " + scales[0].lower()
            ids = [_put(result, doc, period, c, metric, unit) for c,metric in zip(
                cells, ["company_operated_store_revenue","store_operating_expense","group_operating_profit","total_net_revenue"])]
            expense_rate = result.calc(period + "门店经营费用 / 直营收入", "margin", [ids[1], ids[0]], "%")
            group_rate = result.calc(period + "集团营业利润率", "margin", [ids[2], ids[3]], "%")
            result.note(f"{period} 直营收入 {cells[0].value:,}、门店经营费用 {cells[1].value:,}、集团营业利润 "
                        f"{cells[2].value:,}、集团收入 {cells[3].value:,}（{unit}）。该项费用占直营收入 "
                        f"{printed}%。", [*ids, expense_rate, group_rate])
            return True
    return False


def analyze(question, history, documents, result):
    """Append only results supported by matched, dated issuer tables."""
    target = _period(question, history)
    if (not target or re.search(r"环比|上(?:一|个)?季|前(?:一|个)?季|上半年|前三季|前九个月|前六个月|year.to.date|sequential|prior quarter", question, re.I)):
        result.gap("需要明确的财年及单季或全年。当前口径无法匹配到可复算的同期间表列。")
        return
    if re.search(r"净利|税后|毛利|EBITDA|net (?:income|profit)|gross (?:income|profit|margin)", question, re.I):
        result.gap("该调节的终点为集团营业利润。净利、毛利或 EBITDA 需另外的口径与调节行。")
        return
    if re.search(r"北美|美国本土|欧洲|中国区|加盟店|分部|同店利润|单店平均|per.store|segment|franchised stores?", question, re.I):
        result.gap("请求限定了门店子集、经营方式或单店平均。当前整组调节不足以支持该范围。")
        return
    if len({d.get("entity") for d in documents}) > 1:
        result.gap("需要同一发行人的原表。")
        return
    matched = []
    pdfs = []
    for doc in documents:
        raw = doc["raw"]
        if raw.startswith(b"%PDF"):
            pdfs.append(doc)
            continue
        if b"<table" not in raw.lower() or not re.search(rb"(?:restaurant|store|unit)[ -](?:level|operating|contribution|profit|margin)", raw, re.I):
            continue
        try:
            bridges, statements = _html(doc)
        except ImportError:
            result.gap("读取原始 HTML 表格需要 disclosures 可选依赖 lxml。")
            continue
        for key, rows in bridges.items():
            if key[:2] == target:
                matched.extend((doc, key, row, bridges, statements) for row in rows)
    if len(matched) > 1:
        result.gap("同一期间有多个可用调节表，未确定同一业务范围，不能合并。")
        return
    if not matched:
        for doc in pdfs:
            try:
                if _pdf_partial(doc, target, result):
                    break
            except ImportError:
                result.gap("读取原生 PDF 表格需要 disclosures 可选依赖 pdfplumber。")
        result.gap("当前可核对原件中未取得该期间同时列明门店利润、集团营业利润及逐项闭合的调节表。门店经营费用和分部利润均不足以推算门店利润。")
        return
    doc, key, row, bridges, statements = matched[0]
    current = _report_period(result, doc, key, row, statements)
    prior_key = (key[0]-1, *key[1:])
    prior_rows = bridges.get(prior_key, [])
    if len(prior_rows) == 1:
        previous = _report_period(result, doc, prior_key, prior_rows[0], statements)
        for code, label in (("store","门店利润额"),("base","集团营业利润额"),("store_rate","门店利润率"),("base_rate","集团营业利润率")):
            if code in current and code in previous:
                is_rate = code.endswith("rate")
                unit = "percentage_points" if is_rate else key[3]
                operation = "percentage_point_change" if is_rate else "difference"
                diff = result.calc(label+"同比变化", operation, [current[code], previous[code]], unit)
                result.note(f"{label}同比变化 {result.value(diff):+,.2f} {'个百分点' if is_rate else unit}。", [diff])
    elif re.search(r"同比|上年|去年|year.over.year|last year", question, re.I):
        result.gap("同一份原件缺少能同口径闭合的上年同期调节。")
    if re.search(r"提价|涨价|价格|流量|客流|交易|price|pricing|traffic|transactions", question, re.I):
        result.gap("利润调节表本身不能把变动定量归给价格、客流或交易量；需要相应因素的独立披露。")
