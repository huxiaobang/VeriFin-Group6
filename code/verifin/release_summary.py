"""Source-located operands from explicitly labelled quarterly GAAP release tables."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from itertools import accumulate
import re

from .data import stable_id
from .statement_amount_cells import CURRENCIES, CURRENCY


_PERIOD = re.compile(r"\b(first|second|third|fourth) (?:fiscal )?quarter"
                     r"(?: (?:of )?(?:fiscal(?: year)? |FY\s*)(20\d\d))?"
                     r"(?:,? (?:which|that))? ended\s+([A-Za-z]{3,9}\.?(?:\s+\d{1,2}), \d{4})", re.I)
_FISCAL_LABEL = re.compile(
    r"\b(first|second|third|fourth) (?:fiscal )?quarter (?:of )?(?:fiscal(?: year)? |FY\s*)(20\d\d)\b", re.I)
_STATEMENT_TITLE = re.compile(
    r"\b(?:condensed )?consolidated statements? of (?:income|operations)\s*"
    r"(?P<unit>\((?:US\s*\$?\s+|USD\s+|\$\s+)?in (?P<scale>millions|thousands)(?:, except per share data)?(?:[;,] unaudited)?\))"
    r"(?:\s*\(unaudited\))?$", re.I)
_NON_GAAP = r"non[\s‐‑‒–—−-]*gaap"
_GAAP_DOLLARS = re.compile(r"\b(" + _NON_GAAP + r"|GAAP)\s+\(?(?:US\s*\$|USD|\$)\s+in ", re.I)
_GAAP_INCOME_IN_DOLLARS = re.compile(
    r"\b(" + _NON_GAAP + r"|GAAP) operating income in the (first|second|third|fourth) quarter"
    r" (?:was|of) \$\s*(\d{1,3}(?:,\d{3}){1,5}|\d{1,18})(\.\d{1,6})? (million|billion)\b", re.I)
_CURRENCY_DECLARATION = re.compile(
    r"\b(?:amounts|figures|presentation currency|reporting currency)\s*(?:[:,]\s*)?"
    r"(?:(?:are|is)\s+)?(?:(?:denominated|expressed|presented|reported|stated)\s+)?(?:in\s+)?"
    r"(?P<currency>" + CURRENCY.pattern + r")", re.I)
_MONTH_DAY = re.compile(r"[A-Za-z]{3,9}\.? \d{1,2},")
_ORDINALS = ("first", "second", "third", "fourth")
_HEADER = re.compile(r"Q([1-4])\s*FY\s*(\d{4}|\d{2})", re.I)
_AMOUNT = re.compile(r"(?:\$\s*)?(\(?-?(?:\d{1,3}(?:,\d{3}){1,5}|\d{1,18})(?:\.\d{1,6})?\)?)")
_METRICS = {"operating income": "operating_income", "income from operations": "operating_income",
            "revenue": "revenue", "total revenue": "revenue", "net sales": "revenue"}


def _date(text):
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.replace(".", ""), fmt).date()
        except ValueError:
            continue
    return None


def _period(text):
    periods = {(_ORDINALS.index(match[1].lower()) + 1, match[2], end, match[0])
               for match in _PERIOD.finditer(text) if (end := _date(match[3]))}
    return periods.pop() if len(periods) == 1 else None


def _amount(printed, scale):
    match = _AMOUNT.fullmatch(printed)
    if not match:
        return None
    number = match[1].replace(",", "")
    if number.startswith("(") != number.endswith(")") or number.startswith("(-"):
        return None
    amount = Decimal(number.strip("()")) * (-scale if number.startswith("(") else scale)
    return int(amount) if amount == amount.to_integral_value() else None


def _cell(record, locator, *, metric, amount, end, label, quote, row_label, column, unit, printed, scale, text):
    return {**record, "id": stable_id("cell_", [record["source_document_id"], record["snapshot_hash"],
                       record["source_revision_authoritative"], locator, quote]),
            "locator": locator, "metric": metric, "value": amount,
            "unit": "USD", "currency": "USD", "scale": 1, "basis": "US-GAAP",
            "business_scope": "consolidated", "period_end": end.isoformat(),
            "fiscal_quarter": label, "period_quote": quote,
            "row_label": row_label, "column_header": column, "unit_label": unit,
            "printed_value": printed, "printed_scale": scale, "text": text}


def _gaap_rows(rows):
    """A GAAP heading governs rows only until the next accounting-basis heading."""
    section = None
    for index, row in enumerate(rows):
        fields = [v for v in row if v and v != "$"]
        if not fields:
            continue
        if fields[0].casefold() in {"gaap", "non-gaap", "non–gaap", "non gaap"}:
            if section:
                yield section
            section = [] if fields == [fields[0]] and fields[0].casefold() == "gaap" else None
        elif section is not None:
            section.append((index, fields))
    if section:
        yield section


def _summary_cells(section, table_index, period, record):
    quarter, stated_year, end, quote = period
    if not section:
        return []
    _, header = section[0]
    units = re.match(r"^\(?\s*(?:US\s*\$|USD|\$)\s+in (millions|thousands)\b", header[0], re.I)
    if not units or len(header) < 2:
        return []
    label = _HEADER.fullmatch(header[1])
    if not label or label[1] != str(quarter):
        return []
    years = [str(y) for y in range(end.year - 1, end.year + 2) if str(y).endswith(label[2])]
    if len(years) != 1 or stated_year and years[0] != stated_year:
        return []
    scale = 1_000_000 if units[1].lower() == "millions" else 1_000
    cells = {}
    for row_index, fields in section[1:]:
        metric = _METRICS.get(fields[0].casefold())
        if not metric:
            continue
        if len(fields) != len(header) or metric in cells:
            return []
        # Never shift a comparative column into a missing current-quarter cell.
        for column, printed in zip(header[1:], fields[1:]):
            if _HEADER.fullmatch(column) and not _AMOUNT.fullmatch(printed):
                return []
        amount = _amount(fields[1], scale)
        if amount is None:
            return []
        locator = f"HTML table[{table_index}] row[{row_index}] | {fields[0]} | {label[0]}"
        cells[metric] = _cell(record, locator, metric=metric, amount=amount, end=end,
                              label=f"FY{years[0]} Q{quarter}", quote=quote, row_label=fields[0],
                              column=label[0], unit=header[0], printed=fields[1], scale=scale, text=" ".join(fields))
    return list(cells.values()) if len(cells) == 2 else []


def _positioned_rows(rows, widths):
    """Keep blank cells and their physical columns, including merged cells."""
    if len(rows) != len(widths) or any(len(row) != len(width) for row, width in zip(rows, widths)):
        return []
    positioned = []
    for row, width in zip(rows, widths):
        edges = list(accumulate(width, initial=0))
        positioned.append(list(zip(edges[:-1], edges[1:], row)))
    return positioned


def _quarter_column(rows, end):
    candidates = []
    for index, row in enumerate(rows[:-1]):
        quarters = [(start, stop) for start, stop, text in row if text.casefold() == "three months ended"]
        if not quarters:
            continue
        dates = [(start, stop, value) for start, stop, value in rows[index + 1]
                 if any(a <= start < stop <= b for a, b in quarters)]
        split_years = {(start, stop): text for start, stop, text in rows[index + 2]
                       if re.fullmatch(r"20\d\d", text)} if index + 2 < len(rows) else {}
        for start, stop, value in dates:
            if _MONTH_DAY.fullmatch(value) and (year := split_years.get((start, stop))):
                column, first_row = f"{value} {year}", index + 3
            else:
                column, first_row = value, index + 2
            if date := _date(column):
                candidates.append((date, start, stop, column, first_row, row[-1][1]))
    if not candidates or max(candidate[0] for candidate in candidates) != end:
        return None
    matching = [candidate[1:] for candidate in candidates if candidate[0] == end]
    return matching[0] if len(matching) == 1 else None


def _other_currency(currency):
    return CURRENCIES.get(currency.upper(), currency.upper()) != "USD"


def _statement_cells(rows, layout, table_index, period, record):
    quarter, stated_year, end, quote = period
    if not layout["supported"]:
        return []
    gaap_summary = any(match[1].casefold() == "gaap" for match in _GAAP_DOLLARS.finditer(record["text"]))
    gaap_narrative = [m for m in _GAAP_INCOME_IN_DOLLARS.finditer(record["text"])
                      if m[1].casefold() == "gaap" and _ORDINALS[quarter - 1] == m[2].casefold()]
    if not gaap_summary and (record.get("form") not in {"8-K", "8-K/A"} or not gaap_narrative):
        return []
    preceding = layout["preceding"]
    title = _STATEMENT_TITLE.search(preceding)
    if not title or re.search(r"(?:" + _NON_GAAP + r"|adjusted)\s*$", preceding[:title.start()], re.I):
        return []
    if any(_other_currency(currency) for currency in CURRENCY.findall(preceding)):
        return []
    years = {match[2] for match in _FISCAL_LABEL.finditer(record["text"])
             if _ORDINALS.index(match[1].lower()) + 1 == quarter}
    if stated_year:
        years.add(stated_year)
    if len(years) != 1:
        return []
    year = years.pop()
    positioned = _positioned_rows(rows, layout["widths"])
    selected = _quarter_column(positioned, end)
    if not selected:
        return []
    start, stop, column, first_row, width = selected
    scale = 1_000_000 if title["scale"].casefold() == "millions" else 1_000
    cells = {}
    for row_index, row in enumerate(positioned[first_row:], first_row):
        labels = [text for _, col_end, text in row if text and col_end <= start]
        if len(labels) != 1 or (metric := _METRICS.get(labels[0].casefold())) is None:
            continue
        # Read only complete cells within the matched quarter's header span.
        # A blank current cell cannot inherit a value from an adjacent column.
        printed = [text for col, col_end, text in row if text and text != "$" and start <= col < col_end <= stop]
        if (row[-1][1] != width or metric in cells or len(printed) != 1
                or (amount := _amount(printed[0], scale)) is None):
            return []
        label = labels[0]
        locator = f"HTML table[{table_index}] row[{row_index}] columns[{start}:{stop}] | {label} | {column}"
        cells[metric] = _cell(record, locator, metric=metric, amount=amount, end=end,
                              label=f"FY{year} Q{quarter}", quote=quote, row_label=label,
                              column=column, unit=title["unit"], printed=printed[0], scale=scale,
                              text=f"{label} {printed[0]}")
    if len(cells) != 2:
        return []
    if not gaap_summary:
        # A domestic SEC exhibit with an unqualified dollar symbol needs a
        # positive GAAP witness for this column as well as the statement title.
        if not any(value == "$" or value.startswith("$") for row in positioned[first_row:]
                   for col, col_end, value in row if start <= col < col_end <= stop):
            return []
        for match in gaap_narrative:
            printed = Decimal(match[3].replace(",", "") + (match[4] or ""))
            divisor = 10**9 if match[5].casefold() == "billion" else 10**6
            amount = Decimal(cells["operating_income"]["value"]) / divisor
            if amount.quantize(Decimal(1).scaleb(printed.as_tuple().exponent), rounding=ROUND_HALF_UP) != printed:
                return []
    return list(cells.values())


def quarterly_cells(tables, record, *, layouts=(), source_text=None):
    """Return both dated GAAP operands only when amounts align with source columns."""
    if any(_other_currency(match["currency"]) for match in _CURRENCY_DECLARATION.finditer(source_text or record["text"])):
        return []
    period = _period(record["text"])
    if not period or period[2].isoformat() > record["published_at"][:10]:
        return []
    summaries = [pair for index, rows in enumerate(tables) for section in _gaap_rows(rows)
                 if (pair := _summary_cells(section, index, period, record))]
    statements = [pair for index, (rows, layout) in enumerate(zip(tables, layouts))
                  if (pair := _statement_cells(rows, layout, index, period, record))]
    if not statements:
        return summaries[0] if len(summaries) == 1 else []
    if len(statements) != 1:
        return []
    amounts = {cell["metric"]: cell["value"] for cell in statements[0]}
    if any({cell["metric"]: cell["value"] for cell in pair} != amounts for pair in summaries):
        return []
    return statements[0]
