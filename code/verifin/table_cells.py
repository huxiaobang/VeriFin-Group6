"""Index explicitly dated percentage cells in issuer HTML reconciliation tables."""
import calendar
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
import re

from .data import stable_id


NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
PERCENT = re.compile(rf"(?P<signed>[+−-]?{NUMBER})\s*%|\(\s*(?P<negative>{NUMBER})\s*(?:%\s*\)|\)\s*%)")
DURATION = re.compile(r"\b(three|six|nine|twelve) months? ended\b|\byears? ended\b", re.I)
DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", re.I)


def column_period(headers):
    text = " ".join(headers)
    durations = DURATION.findall(text)
    dates = DATE.findall(text)
    if len(durations) != 1 or len(dates) != 1 or re.search(r"\b(?:change|increase|decrease|weeks?)\b", text, re.I):
        return None
    months = {"three":3, "six":6, "nine":9, "twelve":12, "":12}[durations[0].lower()]
    try:
        end = datetime.strptime(dates[0].replace(",", ""), "%B %d %Y").date()
    except ValueError:
        return None
    if end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    first = end.year * 12 + end.month - months
    return date(first // 12, first % 12 + 1, 1).isoformat(), end.isoformat()


def words(text):
    return set(re.findall(r"[a-z0-9]+", re.sub(r"pre[\s-]+tax", "pretax", text.lower())))


def _rows_with_columns(table):
    """Honor HTML row/column spans when finding the vertical header stack of a physical cell."""
    rows = defaultdict(list)
    for cell in table["source_cells"]:
        rows[cell["source_row"]].append(cell)
    occupied_until = {}
    for row_index, cells in rows.items():
        row = []
        col = 0
        for cell in cells:
            width = cell["colspan"]
            while any(occupied_until.get(i, -1) >= row_index for i in range(col, col + width)):
                col += 1
            row.append((col, cell))
            for i in range(col, col + width):
                occupied_until[i] = row_index + cell["rowspan"] - 1
            col += width
        yield row


def index_percentages(raw, document):
    from .structured_documents import parse_structured_html
    parsed = parse_structured_html(raw, source_url=document["source_url"], document_id=document["id"],
                                   snapshot_path=document["snapshot_path"])
    results = []
    for table in parsed["tables"]:
        if table["status"] != "parsed":
            continue
        headers = defaultdict(list)
        for row in _rows_with_columns(table):
            if not row:
                continue
            texts = [cell["text"] for _, cell in row]
            if any(DURATION.search(t) for t in texts):
                headers.clear()
            if any(DURATION.search(t) or DATE.search(t) or re.fullmatch(r"(?:19|20)\d\d", t) for t in texts[1:]):
                for col, cell in row:
                    for pos in range(col, col + cell["colspan"]):
                        if cell["text"]:
                            headers[pos].append(cell)
                continue
            if row[0][0] != 0 or not texts[0]:
                continue
            label = texts[0]
            tokens = words(label)
            adjusted = bool(re.search(r"\badjusted\b|\bnon[- ]?gaap\b", label, re.I))
            basis = "issuer_defined_non_GAAP" if adjusted else ("GAAP" if "gaap" in tokens else "as_reported_unspecified")
            ordered = re.findall(r"[a-z0-9]+", re.sub(r"pre[\s-]+tax", "pretax", label.lower()))
            metric_tokens = [term for term in ordered if term not in {"adjusted", "non", "gaap", "nongaap"}]
            if not metric_tokens:
                continue
            metric = "_".join(metric_tokens)
            for col, cell in row[1:]:
                hit = PERCENT.fullmatch(cell["text"])
                path = headers[col]
                if hit is None or cell["colspan"] != 1 or cell["rowspan"] != 1:
                    continue
                period = column_period([h["text"] for h in path])
                if period is None:
                    continue
                value = Decimal((hit["signed"] or hit["negative"]).replace(",", "").replace("−","-"))
                if hit["negative"]:
                    value = -value
                entry = {k:document[k] for k in ("entity","source_url","snapshot_hash","snapshot_path","published_at","mode")}
                results.append({**entry, "id":stable_id("cell_", [table["id"],cell["dom_xpath"]]),
                                "kind":"financial_fact","document_id":document["id"],"table_id":table["id"],
                                "row_label":label,"column_headers":[h["text"] for h in path],
                                "column_header_locators":[h["dom_xpath"] for h in path],
                                "row_label_locator":row[0][1]["dom_xpath"],"locator":cell["dom_xpath"],
                                "text":f"{label} | {' / '.join(h['text'] for h in path)} | {cell['text']}",
                                "value":float(value),"unit":"%","scale":1,"currency":None,"cell_text":cell["text"],
                                "metric":metric,"basis":basis,"business_scope":"as_disclosed_in_selected_table",
                                "period_start":period[0],"period_end":period[1]})
    return results


def rank_cells(records, query):
    terms = words(query) - {"the", "of", "in", "for", "quarter", "quarterly", "year", "ended", "months"}
    def score(record):
        label = words(record["row_label"])
        if record["basis"] == "issuer_defined_non_GAAP":
            label.add("adjusted")
        matched = len(label & terms)
        return (matched, matched / max(1, len(label)))
    return sorted((r for r in records if score(r)[0]), key=score, reverse=True)
