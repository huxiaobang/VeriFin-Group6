"""Read native quarterly financial summaries whose rows identify reported GAAP."""
from collections import Counter, defaultdict
from decimal import Decimal
import re

from .data import stable_id
from .revenue_bridge import layout
from .statement_amount_cells import AMOUNT, CURRENCIES, CURRENCY, DOLLARS, METRICS, SCALE, nearby, period, row_span, text


TITLE = re.compile(r"financial performance constant[- ]currency reconciliation|consolidated (?:financial "
                   r"(?:performance|results)|results of operations)(?: constant[- ]currency reconciliation)?", re.I)
QUARTER = re.compile(r"(?:three|3) months ended [A-Za-z]+ \d{1,2},?", re.I)
REPORTED = re.compile(r"(?P<year>20\d\d) (?:as )?reported \((?P<basis>(?:U\.?S\.?[- ]+)?GAAP)\)", re.I)
UNIT = re.compile(r"\((?P<currency>" + CURRENCY.pattern + r"|\$) in (?P<scale>thousands|millions|billions)"
                  r"(?:, except per share (?:amounts|data))?\)", re.I)
MEASURES = ("revenue", "operating_income")


def _amount(printed, scale):
    parsed = AMOUNT.fullmatch(printed)
    if not parsed:
        return None
    number = parsed["amount"].replace(",", "").replace("−", "-").replace("(", "-").replace(")", "")
    value = Decimal(number) * scale
    return int(value) if value == value.to_integral_value() else None


def reported_gaap_cells(table, paths, doc, info, document_id, source_text, prior_units, companies):
    """Bind both measures to the same physical reported row and dated header."""
    report_end = doc.get("period_end")
    if not report_end or report_end > doc["published_at"][:10]:
        return []
    context = nearby(table)
    headings = [node for node in context if TITLE.fullmatch(text(node))]
    if len(headings) != 1:
        return []
    for node in context:
        label = text(node).casefold()
        issuers = set()
        for company in companies:
            ticker = company["ticker"]
            name = company.get("name") or ""
            if label and label in (ticker.casefold(), name.casefold(), f"{name} ({ticker})".casefold()):
                issuers.add(ticker)
        if issuers and doc["entity"] not in issuers:
            return []
    rows = [[(span, text(node), node) for span, _, node in row] for row in layout(table)]
    if len(rows) < 3 or not rows[0] or not rows[1]:
        return []
    periods = [(cols, label, node) for cols, label, node in rows[0]
               if node.tag == "th" and node.get("rowspan", "1") == "1" and QUARTER.fullmatch(label)]
    monetary = UNIT.fullmatch(rows[1][0][1])
    if len(periods) != 1 or not monetary or rows[1][0][0].start != 0 or rows[1][0][2].tag != "th":
        return []
    period_columns, ending, date_node = periods[0]
    unit_cols, unit_label, unit_node = rows[1][0]
    if (period_columns.start != unit_cols.stop or period_columns.stop != rows[1][-1][0].stop
            or any(label and node is not date_node for _, label, node in rows[0])):
        return []
    metrics = defaultdict(list)
    for cols, label, node in rows[1][1:]:
        metric = METRICS.get(label.casefold())
        if metric in MEASURES:
            if (node.tag != "th" or len(cols) != 1 or not set(cols).issubset(period_columns)
                    or node.get("rowspan", "1") != "1"):
                return []
            metrics[metric].append((cols, label, node))
    if any(len(metrics[m]) != 1 for m in MEASURES):
        return []
    header_ids = Counter(node.get("id") for row in rows for _, _, node in row if node.tag == "th" and node.get("id"))

    scale = SCALE[monetary["scale"].casefold()]
    reported = {}
    reported_rows_ended = False
    for index, row in enumerate(rows[2:], 2):
        if not any(value for _, value, _ in row):
            continue
        label = REPORTED.fullmatch(row[0][1]) if row[0][0] == unit_cols and row[0][2].tag == "th" else None
        if not label:
            reported_rows_ended = True
            continue
        # A secondary heading can change the currency, scale, basis or
        # duration for a single column. Require one uninterrupted body.
        if reported_rows_ended or row[-1][0].stop != period_columns.stop:
            return []
        interval = period([ending, label["year"]])
        if not interval or interval[1] in reported or interval[1] > report_end:
            return []
        values = {}
        for metric in MEASURES:
            metric_cols, _, metric_node = metrics[metric][0]
            cell = next(((printed, node) for cols, printed, node in row[1:] if cols == metric_cols), None)
            if not cell or cell[1].tag != "td" or cell[1].get("rowspan", "1") != "1":
                return []
            assigned = {node.get("id") for node in (date_node, unit_node, metric_node, row[0][2])}
            if any(ref not in assigned or header_ids[ref] != 1 for ref in cell[1].get("headers", "").split()):
                return []
            amount = _amount(cell[0], scale)
            if amount is None:
                return []
            values[metric] = (*cell, amount)
        reported[interval[1]] = (interval[0], index, label, values)
    if report_end not in reported:
        return []
    start, index, label, values = reported[report_end]
    if doc.get("period_start") and doc["period_start"] != start:
        return []

    # Bare dollars can describe several currencies. Require the same earlier
    # reported pair, dated and corroborated as consolidated by SEC company facts.
    unit_token = monetary["currency"].upper()
    currency = CURRENCIES.get(unit_token, unit_token)
    witnesses = {}
    for end, (prior_start, _, _, comparative) in reported.items():
        if end == report_end:
            continue
        corroborated = {m: prior_units.get((m, prior_start, end, comparative[m][2]), {}) for m in MEASURES}
        for currency_code in set.intersection(*(set(rows) for rows in corroborated.values())):
            proof = {m: corroborated[m][currency_code] for m in MEASURES}
            if len({source["accession"] for source in proof.values()}) == 1:
                witnesses[currency_code] = proof
    if currency == "$":
        if len(witnesses) != 1:
            return []
        currency = next(iter(witnesses))
    if (witnesses and set(witnesses) != {currency}) or any(
            ("$" in printed and currency not in DOLLARS) or ("£" in printed and currency != "GBP")
            or ("€" in printed and currency != "EUR") for printed, _, _ in values.values()):
        return []

    title = headings[0]
    prefix = [text(title), *(value for row in rows[:index + 1] for _, value, _ in row)]
    # Bind the complete native table prefix as one contiguous source span.
    # The same row wording can appear elsewhere under another title or unit.
    bounds = row_span(prefix, source_text)
    if not bounds:
        return []
    context_start, row_end = bounds
    row_offsets = row_span([value for _, value, _ in rows[index]], source_text[context_start:row_end])
    if not row_offsets or context_start + row_offsets[1] != row_end:
        return []
    table_path = paths[table]
    basis = "US-GAAP" if currency in witnesses or label["basis"].casefold().startswith("u") else "GAAP"
    records = []
    for metric in MEASURES:
        printed, node, amount = values[metric]
        metric_cols, column, metric_node = metrics[metric][0]
        locator = paths[node]
        records.append({
            "id": stable_id("cell_", [document_id, info["sha256"], locator]), "kind": "financial_fact",
            "cell_type": "reported_gaap_amount_cell", "mode": info.get("mode", "replay"),
            "entity": doc["entity"], "form": doc.get("form"), "published_at": doc["published_at"],
            "document_id": document_id, "snapshot_hash": info["sha256"], "snapshot_path": doc["source"],
            "source_url": info["url"], "text_snapshot_path": doc["text"], "locator": locator,
            "table_id": stable_id("table_", [document_id, info["sha256"], table_path]), "table_xpath": table_path,
            "table_title": text(title), "table_title_locator": paths[title],
            "source_context_start": context_start, "source_row_start": context_start + row_offsets[0],
            "source_row_end": row_end,
            "row_label": label[0], "row_label_locator": paths[rows[index][0][2]], "row_index": index,
            "column_range_one_based": [metric_cols.start + 1, metric_cols.stop],
            "column_headers": [ending, column], "column_header_locators": [paths[date_node], paths[metric_node]],
            "printed_value": printed, "printed_scale": scale, "printed_unit": unit_label, "unit_locator": paths[unit_node],
            "currency_reference": witnesses.get(currency, {}).get(metric), "value": amount, "unit": currency,
            "currency": currency, "scale": 1, "business_scope": "consolidated", "basis": basis,
            "metric": metric, "concept": "issuer:" + metric, "period_start": start, "period_end": report_end,
            "period_start_basis": "Printed three-calendar-month duration and selected row year",
        })
    return records
