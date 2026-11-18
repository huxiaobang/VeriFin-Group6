"""Source-addressed monetary operands from historical issuer income statements."""
import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
import re

from .data import stable_id
from .financial import METRICS as US_GAAP_TAGS
from .revenue_bridge import layout, month_start
from .structured_documents import _hidden

METRICS = {"net sales": "revenue", "total net sales": "revenue", "revenue": "revenue",
           "revenues": "revenue", "total revenue": "revenue", "total revenues": "revenue",
           "gross profit": "gross_profit", "gross margin": "gross_profit",
           "operating income": "operating_income", "income from operations": "operating_income",
           "operating income (loss)": "operating_income", "operating profit": "operating_income"}
TITLE = re.compile(r"(?:(?:(?:condensed|consolidated)\s+)*(?:statements? of (?:operations|income|earnings)|"
                   r"income statements?)|(?:condensed )?consolidated results of operations)(?:\s*\(unaudited\))?", re.I)
NUMBERS = {"three":3, "six":6, "nine":9, "twelve":12, "thirteen":13, "fourteen":14,
           "twenty-six":26, "twenty-seven":27, "thirty-nine":39, "forty":40,
           "fifty-two":52, "fifty-three":53}
DURATION = re.compile(r"\b(?:(\d{1,2}|" + "|".join(NUMBERS) + r")[ -](months?|weeks?)|((?:fiscal )?years?|quarters?))\s+ended\b", re.I)
MONTHS = {name.casefold():i for i, name in enumerate(calendar.month_name) if name}
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
SCALE = {"thousands":1000, "millions":10**6, "billions":10**9}
SCALE_NOTE = re.compile(r"\bin (thousands|millions|billions)\b", re.I)
CURRENCIES = {"U.S. DOLLARS":"USD", "US DOLLARS":"USD", "CANADIAN DOLLARS":"CAD",
              "AUSTRALIAN DOLLARS":"AUD", "EUROS":"EUR", "POUNDS STERLING":"GBP"}
CURRENCY = re.compile(r"\b(?:USD|CAD|AUD|NZD|HKD|SGD|EUR|GBP|JPY|CNY|CHF|U\.S\. dollars|US dollars|Canadian dollars|Australian dollars|euros|pounds sterling)\b", re.I)
DOMESTIC_FORM = re.compile(r"(?:8-K|10-[QK])\b", re.I)
BAD_CONTEXT = re.compile(r"\b(?:adjusted|non[- ]?GAAP|segment|pro forma|projected|forecast|parent.only|unconsolidated|IFRS)\b", re.I)
AMOUNT = re.compile(r"(?P<symbol>[$£€])?\s*(?P<amount>[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\))")
DOLLARS = {"USD", "CAD", "AUD", "NZD", "HKD", "SGD"}
EXPENSE_TOTAL = re.compile(r"(?:total )?operating (?:expenses|costs and expenses)", re.I)
EXPENSE_NOUN = re.compile(r"\b(?:expenses?|costs?|charges?|depreciation|amorti[zs]ation|impairment|"
                          r"restructuring|research and development|sales and marketing|general and administrative)\b", re.I)
AMBIGUOUS_EXPENSE = re.compile(r"\b(?:total|subtotal|income|gains?|benefits?|recoveries|revenue|"
                               r"including|thereof|of which|tax(?:es)?|adjusted|non[- ]?GAAP)\b", re.I)
RATE_LABEL = re.compile(r"%|\b(?:percent(?:age)?|basis points?|per share|expense ratio)\b", re.I)


def text(node):
    return " ".join(" ".join(node.itertext()).split())


def period(headings):
    header = " ".join(headings).replace("–", "-").replace("‑", "-")
    durations = list(DURATION.finditer(header))
    dates = list(DATE.finditer(header))
    if len(durations) != 1 or len(dates) != 1:
        return None
    # Reject unrecognized column qualifiers (such as budget, change or percent of sales).
    if re.sub(r"[\s,()/]+", "", DATE.sub("", DURATION.sub("", header))).casefold() not in {"", "unaudited", "audited"}:
        return None
    count, unit, named = durations[0].groups()
    month, day, year = dates[0].groups()
    try:
        end = date(int(year), MONTHS[month.casefold()], int(day))
        if named:
            n = 3 if named.casefold().startswith("quarter") else 12
        else:
            n = int(count) if count.isdigit() else NUMBERS[count.casefold()]
        if unit and unit.casefold().startswith("week"):
            if n not in (13, 14, 26, 27, 39, 40, 52, 53):
                return None
            start = end - timedelta(weeks=n) + timedelta(days=1)
        else:
            if n not in (3, 6, 9, 12):
                return None
            start = month_start(end, n)
        return start.isoformat(), end.isoformat()
    except (ValueError, OverflowError):
        return None


def nearby(table):
    """A caption, or immediately preceding heading/unit lines up to the previous table."""
    nodes = []
    for caption in table.xpath("./caption"):
        nodes.extend(caption.xpath("./p|./div") or [caption])
    cursor = table
    while cursor.getparent() is not None and cursor.getparent().tag in ("div","section"):
        previous = cursor.getprevious()
        while (previous is not None and previous.tag in ("span", "br") and len(previous) == 0
               and not text(previous) and not (previous.tail or "").strip()):
            previous = previous.getprevious()
        if previous is not None or (cursor.getparent().text or "").strip():
            break
        cursor = cursor.getparent()
    for node in cursor.itersiblings(preceding=True):
        if node.tag not in {"p","div","span","h1","h2","h3","h4"} or node.xpath(".//table"):
            break
        value = text(node)
        if not value:
            continue
        if len(value) > 160:
            break
        nodes.append(node)
        if TITLE.fullmatch(value) or len(nodes) >= 5:
            break
    return nodes


def row_span(parts, source_text):
    # SEC exhibits can use line, whitespace or pipe delimiters. Require a unique
    # complete original row, including all comparison columns, in the text snapshot.
    sep = r"(?:\s+|\s*(?:\|\s*)+)"
    escaped = [r"\s+".join(re.escape(w) for w in part.split()) for part in parts if part]
    matches = re.finditer(r"(?<!\w)" + sep.join(escaped) + r"(?![\w.,]\d|\w)", source_text)
    first = next(matches, None)
    if first and not next(matches, None):
        return first.span()
    return None


def prior_currency_evidence(data, doc, metrics=("revenue", "gross_profit", "operating_income")):
    """Currency witnesses for the same measure, value and dates, filed before this release."""
    company = next((c for c in data.manifest["companies"] if c["ticker"] == doc["entity"]), {})
    path = company.get("facts")
    if not path:
        return {}
    facts = data.json(path)
    if str(facts.get("cik","")).lstrip("0") != str(company.get("cik","")).lstrip("0"):
        return {}
    taxonomy = facts.get("facts",{}).get("us-gaap",{})
    by_value = defaultdict(dict)
    for metric in metrics:
        for tag in US_GAAP_TAGS[metric]:
            for currency, records in taxonomy.get(tag,{}).get("units",{}).items():
                if not re.fullmatch("[A-Z]{3}", currency):
                    continue
                for r in records:
                    if (r.get("form") in ("10-K", "10-Q") and isinstance(r.get("filed"), str)
                            and r["filed"] < doc["published_at"][:10] and r.get("start") and r.get("end")
                            and isinstance(r.get("val"), (int,float)) and r.get("accn")):
                        key = (metric, r["start"], r["end"], Decimal(str(r["val"])))
                        by_value[key][currency] = {"snapshot_path":path, "snapshot_hash":data.info(path)["sha256"],
                                                   "accession":r["accn"], "filed":r["filed"], "period_start":r["start"],
                                                   "period_end":r["end"], "concept":"us-gaap:"+tag}
    return by_value


def row_amounts(row, label_node, header_nodes, scale):
    values = {}
    if not header_nodes or row[-1][0].stop < max(span.stop for span, _, _ in header_nodes):
        return {}  # A physically short row can shift every following value into the wrong period.
    for span, printed, node in row:
        if node is label_node or printed.casefold() in {"", "$", "£", "€", "—", "–", "-", "n/a", "nm"}:
            continue
        amount = AMOUNT.fullmatch(printed)
        if not amount or node.get("rowspan","1") != "1":
            return {}
        stacks = {tuple((value,n) for h,value,n in header_nodes if x in h) for x in span}
        if len(stacks) != 1:
            return {}
        stack = next(iter(stacks))
        dates = period([v for v,_ in stack])
        if not dates or dates in values:
            return {}
        number = amount["amount"].replace("−","-").replace(",","").replace("(","-").replace(")","")
        values[dates] = (printed, Decimal(number)*scale, node, span, stack)
    return values


def expense_rows(rows, targets, paths):
    """Inventory the bounded statement section, retaining unknown and blank items as gaps."""
    if not {"gross_profit", "operating_income"} <= targets.keys():
        return {}
    first, _, gross_label = targets["gross_profit"]
    last = targets["operating_income"][0]
    label_column = next(span.start for span, _, node in rows[first] if node is gross_label)
    inventory = {}
    for index in range(first + 1, last):
        visible = [(span, value, node) for span, value, node in rows[index] if value]
        if not visible:
            continue
        span, label, node = visible[0]
        has_label = span.start == label_column and bool(re.search(r"[a-z]", label, re.I))
        if has_label and (RATE_LABEL.search(label) or
                re.search(r"\bmargin\b", label, re.I) and any("%" in value for _, value, _ in visible[1:])):
            continue
        if has_label and len(visible) == 1 and label.endswith(":") and EXPENSE_TOTAL.fullmatch(label[:-1]):
            continue  # Plain section heading.
        role = "unsupported"
        if has_label:
            if EXPENSE_TOTAL.fullmatch(label):
                role = "total"
            elif label.casefold() in {"other income", "other operating income"}:
                role = "income"
            elif EXPENSE_NOUN.search(label) and not AMBIGUOUS_EXPENSE.search(label):
                role = "component"
        else:
            label = "(unlabeled statement row)"
        key = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        inventory[index] = {"key":key, "label":label, "row_index":index, "role":role,
                            "row_locator":paths[node.getparent()],
                            "row_label_locator":paths[node] if has_label else None}
    # A repeated label or multiple expense totals cannot uniquely identify a component.
    keys = Counter(entry["key"] for entry in inventory.values())
    total_count = sum(entry["role"] == "total" for entry in inventory.values())
    for entry in inventory.values():
        if keys[entry["key"]] > 1 or (entry["role"] == "total" and total_count > 1):
            entry["role"] = "unsupported"
    return inventory


def statement_table(table, paths, doc, info, document_id, source_text, prior_units):
    rows = [[(span, text(td), td) for span, _, td in row] for row in layout(table)]
    candidate_rows = defaultdict(list)
    for i, row in enumerate(rows):
        nonempty = [(span, label, node) for span, label, node in row if label]
        if len(nonempty) < 2:
            continue
        _, label, node = nonempty[0]
        metric = METRICS.get(label.casefold())
        if metric and not any(RATE_LABEL.search(value) for _, value, _ in nonempty[1:]):
            candidate_rows[metric].append((i, label, node))
    targets = {k: rows[0] for k, rows in candidate_rows.items() if len(rows) == 1}
    if not targets:
        return []
    first = min(i for i, _, _ in targets.values())
    # The body may start with component rows well before the first total.
    head = []
    for row in rows[:first]:
        visible = [(span, value, node) for span, value, node in row if value]
        if visible and visible[0][0].start == 0 and (len(visible) > 1 or visible[0][1].endswith(":")):
            if not any(DURATION.search(value) or re.fullmatch(r"\d{4}",value) or DATE.search(value)
                       for _, value, _ in visible):
                break
        head.append(visible)
    notes = nearby(table) + [row[0][2] for row in head if len(row) == 1]
    titles = [node for node in notes if TITLE.fullmatch(text(node))]
    if len(titles) != 1 or any(BAD_CONTEXT.search(text(node)) for node in notes):
        return []
    title = titles[0]
    header_nodes = [(span, value, node) for row in head for span,value,node in row
                    if node not in notes or DURATION.search(value) or DATE.search(value)]
    unit_notes = [(node, m.group(1).lower()) for node in notes for m in SCALE_NOTE.finditer(text(node))]
    if len({s for _,s in unit_notes}) != 1:
        return []
    for note, _ in unit_notes:
        main_unit = re.split(r"\bexcept\b", text(note), flags=re.I)[0]
        scale_words = {word.casefold() for word in re.findall(r"\b(?:thousands|millions|billions)\b", text(note), re.I)}
        if len(scale_words) != 1 or re.search(r"\b(?:shares?|percent(?:age)?s?|basis points|units)\b|%", main_unit, re.I):
            return []
    unit_node, scale_name = unit_notes[0]
    scale = SCALE[scale_name]
    columns = {}
    for metric, (i,label,label_node) in targets.items():
        columns[metric] = row_amounts(rows[i], label_node, header_nodes, scale)
    explicit = {CURRENCIES.get(m.upper(),m.upper()) for n in notes for m in CURRENCY.findall(text(n))}
    symbols = {s for row in rows for _,v,_ in row for s in re.findall(r"[$£€]",v)}
    matched = {}
    for metric, values in columns.items():
        for dates, (_,v,*_) in values.items():
            matched.update(prior_units.get((metric,*dates,v),{}))
    domestic = DOMESTIC_FORM.match(doc.get("form") or "")
    if explicit:
        currencies = explicit
        currency_basis = "statement unit note"
    elif matched:
        currencies = set(matched)
        currency_basis = "matching dated comparative amount and measure in filed US-GAAP company facts"
    elif domestic and symbols == {"$"} and re.search(r"\bdollar amounts are (?:stated|expressed|presented) in (?:millions|thousands|billions)\b", source_text, re.I) and not CURRENCY.search(source_text):
        currencies = {"USD"}
        currency_basis = "domestic SEC filing with a disclosure-wide dollar reporting unit"
    else:
        return []
    if len(currencies) != 1 or (matched and currencies != set(matched)):
        return []
    currency = next(iter(currencies))
    if (("$" in symbols and currency not in DOLLARS) or ("£" in symbols and currency != "GBP")
            or ("€" in symbols and currency != "EUR")):
        return []
    if not (domestic or matched or re.search(r"\bU\.?S\.?[- ]GAAP\b",source_text,re.I)):
        return []
    # Unqualified issuer-level titles also require a comprehensive income statement body.
    if "consolidated" not in text(title).casefold() and not any(
            row and row[0][1].casefold() in {"net income", "net earnings", "net income (loss)"} for row in rows):
        return []
    table_path = paths[table]
    table_id = stable_id("table_", [document_id, info["sha256"], table_path])
    inventory = expense_rows(rows, targets, paths)
    expense_offsets = {}
    for i, entry in inventory.items():
        offsets = row_span([v for _, v, _ in rows[i]], source_text)
        expense_offsets[i] = offsets
        if offsets:
            entry["source_row_start"], entry["source_row_end"] = offsets
        if entry["role"] == "unsupported":
            continue
        if entry["role"] == "total":
            metric = "operating_expenses"
        elif entry["role"] == "income":
            metric = entry["key"]
        else:
            metric = "operating_expense_" + entry["key"]
        label_node = next(node for _, value, node in rows[i] if value)
        targets[metric] = (i, entry["label"], label_node)
        columns[metric] = row_amounts(rows[i], label_node, header_nodes, scale)
    out = []
    expense_ids = {}
    for metric, (i,label,label_node) in targets.items():
        offsets = expense_offsets[i] if i in inventory else row_span([v for _,v,_ in rows[i]], source_text)
        if offsets is None:
            continue
        for (start,end), (printed,v,node,span,stack) in columns[metric].items():
            headings = [value for value,_ in stack]
            out.append({
                "kind":"financial_fact", "cell_type":"statement_amount_cell", "mode":"replay",
                "id":stable_id("cell_",[document_id,info["sha256"],paths[node]]),
                "document_id":document_id,"entity":doc["entity"],"published_at":doc["published_at"],
                "form":doc.get("form"), "snapshot_hash":info["sha256"],"snapshot_path":doc["source"],
                "source_url":info["url"],"text_snapshot_path":doc["text"],
                "source_row_start":offsets[0],"source_row_end":offsets[1],"table_id":table_id,
                "table_xpath":table_path,"table_title":text(title),"table_title_locator":paths[title],
                "row_label":label,"row_label_locator":paths[label_node],"row_index":i,
                "locator":paths[node],"column_range_one_based":[span.start+1,span.stop],
                "column_heading":" / ".join(headings),"column_headers":headings,
                "column_header_locators":[paths[n] for _,n in stack],
                "printed_value":printed,"printed_scale":scale,"printed_unit":text(unit_node),
                "unit_locator":paths[unit_node],"currency_basis":currency_basis,
                "currency_reference":matched.get(currency),"value":int(v) if v==v.to_integral_value() else float(v),
                "currency":currency,"unit":currency,"scale":1,"basis":"US-GAAP","business_scope":"consolidated",
                "metric":metric,"concept":"issuer:"+metric,"period_start":start,"period_end":end})
            if i in inventory:
                out[-1].update(expense_key=inventory[i]["key"], expense_role=inventory[i]["role"])
                expense_ids[i, start, end] = out[-1]["id"]
    if {"gross_profit", "operating_income"} <= targets.keys():
        for cell in out:
            if cell["metric"] not in {"gross_profit", "operating_income"}:
                continue
            section = []
            for i, entry in inventory.items():
                cell_id = expense_ids.get((i, cell["period_start"], cell["period_end"]))
                status = "unsupported" if entry["role"] == "unsupported" else "present" if cell_id else "missing"
                section.append(dict(entry, status=status, cell_id=cell_id))
            # This covers the visible rows only. An omitted entire row is detectable
            # when the consumer reconciles the sum to both statement endpoints.
            cell["expense_section"] = {"rows":section, "complete":bool(section) and
                                       all(item["status"] == "present" for item in section)}
    return out


def statement_cells(raw, doc, info, document_id, source_text, prior_units):
    from lxml import html
    root = html.document_fromstring(raw, parser=html.HTMLParser(encoding="utf-8", no_network=True, remove_comments=True))
    tree = root.getroottree()
    paths = {node:tree.getpath(node) for node in root.iter() if node.tag in
             {"table","caption","tr","td","th","p","div","span","h1","h2","h3","h4"}}
    for node in list(root.iter()):
        if _hidden(node) and node.getparent() is not None:
            node.drop_tree()
    cells = []
    for table in root.xpath("//table[not(.//table)]"):
        try:
            cells.extend(statement_table(table, paths, doc, info, document_id, source_text, prior_units))
        except (ValueError, OverflowError):
            continue
    return cells
