"""Extract quarter amounts from a source-located revenue-by-market-platform table."""
from datetime import timedelta
import re

from .data import DataError, parse_day, stable_id
from .financial import duration_class
from .skill_calculations import previous_quarter
from .statement_amount_cells import CURRENCIES, CURRENCY, row_span


TITLE = "Revenue by Market Platform"
CELL_TYPE = "market_platform_revenue_cell"
SUMMARY = re.compile(r"Q([1-4]) Fiscal (20\d\d) Summary", re.I)
PERIODS = re.compile(r"\(\$ in (millions|thousands)\) (Q[1-4] FY\d{2} Q[1-4] FY\d{2} Q[1-4] FY\d{2}) Q/Q Y/Y")
MONEY = r"\$?(?:\d{1,3}(?:,\d{3})*|\d+)"
CHANGE = r"(?:(?:Up|Down) \d+(?:\.\d+)?%|Flat|[-–—])"
ROW = re.compile(rf"([A-Za-z][A-Za-z &/-]+?) ({MONEY}) ({MONEY}) ({MONEY}) {CHANGE} {CHANGE}")


def _value(printed, scale):
    return int(printed.lstrip("$").replace(",", "")) * scale


def cells(page, number, doc, info, document_id, source_text, page_start, page_end, currency_witnesses):
    start, end = doc.get("period_start"), doc.get("period_end")
    if not start or not end or duration_class(start, end) != "quarter_length":
        return []
    native = page.extract_text_lines(return_chars=False)
    lines = [" ".join(line["text"].split()) for line in native]
    titles = [i for i, line in enumerate(lines) if line == TITLE]
    if len(titles) != 1 or titles[0] + 2 >= len(lines):
        return []
    title = titles[0]
    headers = {f"{match[2]}Q{match[1]}" for line in lines[:title] if (match := SUMMARY.fullmatch(line))}
    periods = PERIODS.fullmatch(lines[title + 1])
    if len(headers) != 1 or not periods:
        return []
    quarter = headers.pop()
    labels = [quarter, previous_quarter(quarter), previous_quarter(quarter, year_on_year=True)]
    expected_header = " ".join(f"Q{label[-1]} FY{label[2:4]}" for label in labels)
    if periods[2] != expected_header:
        return []
    units = periods[1]
    scale = 1_000_000 if units == "millions" else 1_000
    printed_period = f"Q{quarter[-1]} FY{quarter[2:4]}"

    # The issuer reports several definitions of revenue on one page. Establish
    # the consolidated total from its GAAP table before identifying platform shares.
    if lines[:title].count("GAAP") != 1 or lines[:title].count("Non-GAAP") != 1:
        return []
    gaap_start, gaap_end = lines.index("GAAP"), lines.index("Non-GAAP")
    gaap = lines[gaap_start + 1:gaap_end]
    gaap_revenue = [match for line in gaap if (match := ROW.fullmatch(line)) and match[1] == "Revenue"]
    header = expected_header + " Q/Q Y/Y"
    if (len(gaap_revenue) != 1 or not any(header in line for line in gaap)
            or not " ".join(line.replace(header, "").strip() for line in gaap if line != header).startswith(
                f"($ in {units}, except earnings per share)")):
        return []
    gaap_amounts = tuple(_value(printed, scale) for printed in gaap_revenue[0].groups()[1:])

    # An unqualified dollar sign alone does not identify a currency. Require
    # corroboration for the comparative GAAP total from the previous SEC filing.
    prior_end = (parse_day(start) - timedelta(days=1)).isoformat()
    witnesses = {(currency, fiscal_start): reference
                 for (metric, fiscal_start, fiscal_end, amount), matches in currency_witnesses.items()
                 if metric == "revenue" and fiscal_end == prior_end and amount == gaap_amounts[1]
                 and duration_class(fiscal_start, fiscal_end) == "quarter_length"
                 for currency, reference in matches.items()}
    if len(witnesses) != 1:
        return []
    (currency, _), reference = witnesses.popitem()
    explicit = {CURRENCIES.get(value.upper(), value.upper()) for value in CURRENCY.findall(source_text)}
    if currency != "USD" or (explicit and explicit != {currency}):
        return []

    source_page = source_text[page_start:page_end]
    heading_span = row_span([TITLE], source_page)
    if not heading_span:
        return []
    table_start = page_start + heading_span[0]
    source_table = source_text[table_start:page_end]
    table_id = stable_id("platform_table_", [document_id, info["sha256"], number, native[title]["top"], quarter])
    records = {}
    seen_labels = set()
    previous_top = native[title + 1]["top"]
    row_gap = previous_top - native[title]["top"]
    for index, line in enumerate(lines[title + 2:], title + 2):
        native_row = native[index]
        if native_row["top"] - previous_top > row_gap * 1.75:
            break
        previous_top = native_row["top"]
        match = ROW.fullmatch(line)
        if not match or abs(native_row["x0"] - native[title + 1]["x0"]) > 2:
            continue  # Indented component rows are not separate market platforms.
        label, printed = match[1], match[2]
        if label in seen_labels:
            return []
        seen_labels.add(label)
        span = row_span([line], source_table)
        value = _value(printed, scale)
        total = label == "Total"
        if span and value <= gaap_amounts[0] and (not total or tuple(
                _value(amount, scale) for amount in match.groups()[1:]) == gaap_amounts):
            box = [round(native_row[key], 2) for key in ("x0", "top", "x1", "bottom")]
            records[label] = {
                "id": stable_id("cell_", [table_id, label, box]), "kind": "financial_fact",
                "cell_type": CELL_TYPE, "table_id": table_id, "document_id": document_id,
                "entity": doc["entity"], "source_url": info["url"], "snapshot_hash": info["sha256"],
                "snapshot_path": doc["source"], "published_at": doc["published_at"],
                "locator": f"PDF p.{number}, {TITLE}, {label}, {printed_period}, row box {box}",
                "metric": "revenue" if total else "market_platform_revenue", "value": value,
                "unit": currency, "currency": currency, "scale": 1, "basis": "US-GAAP",
                "business_scope": "consolidated" if total else "market_platform:" + label,
                "market_platform": None if total else label, "fiscal_quarter": quarter,
                "period_start": start, "period_end": end, "row_label": label, "printed_period": printed_period,
                "printed_value": printed, "printed_scale": scale, "currency_reference": reference,
                "source_table_start": table_start, "source_row_start": table_start + span[0],
                "source_row_end": table_start + span[1], "text": line}
        if total:
            break
    table_end = max((record["source_row_end"] for record in records.values()), default=table_start)
    for record in records.values():
        record["source_table_end"] = table_end
        record["source_table_operand_count"] = len(records)
    return list(records.values())


def validate_share(numerator, denominator):
    """Only compare a disclosed market platform and the same table's consolidated total."""
    shared = ("table_id", "document_id", "snapshot_hash", "entity", "basis", "unit", "currency",
              "period_start", "period_end", "fiscal_quarter")
    if (any(row.get("cell_type") != CELL_TYPE or row.get("scale") != 1 for row in (numerator, denominator))
            or numerator.get("metric") != "market_platform_revenue" or not numerator.get("market_platform")
            or numerator.get("business_scope") != f"market_platform:{numerator['market_platform']}"
            or denominator.get("metric") != "revenue" or denominator.get("business_scope") != "consolidated"
            or any(not numerator.get(key) or numerator[key] != denominator.get(key) for key in shared)
            or not 0 <= numerator["value"] <= denominator["value"] or not denominator["value"]):
        raise DataError("A market-platform share requires that platform's revenue first and the same disclosed quarter, currency and table's consolidated total second.")
