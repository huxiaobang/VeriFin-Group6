"""Bind a displayed operating-margin change to a dated calculation or source quotation."""
from decimal import Decimal
import re

from .data import DataError, parse_day
from .display_numbers import REJECTED_RATE, _quantity_spans
from .financial import duration_class
from .operating_margin import ATTRIBUTION, MARGIN, YOY


METRIC = "operating_income_margin_change"
SEQUENTIAL = re.compile(r"环比|上(?:一)?季(?:度)?|\b(?:qoq|sequential(?:ly)?|prior quarter|quarter.over.quarter)\b", re.I)
COMPARISON = re.compile(
    r"比较|相比|变动|变化|相差|差额|较|比|下降|下滑|降低|上升|提高|高于|低于|"
    r"\b(?:compar\w*|vs\.?|versus|chang\w*|difference|increas\w*|decreas\w*|lower|higher|fell|rose|down|up)\b", re.I)
OTHER_RATE = re.compile(r"毛利率|净利率|费用率|(?:\bgross|\bnet|\boperating expense) margins?\b", re.I)
DIRECTION = re.compile(
    r"(?P<down>下降|下滑|降低|低(?:于)?|\b(?:decreas\w*|fell|lower|down)\b)|"
    r"(?P<up>上升|提高|高(?:于)?|\b(?:increas\w*|rose|higher|up)\b)", re.I)
QUARTER_WORD = re.compile(r"季度|财季|財季|\bquarters?\b", re.I)
PAIR_LINK = re.compile(r"较|比|高于|低于|\b(?:versus|vs\.?|than)\b", re.I)
FISCAL = re.compile(r"(?<![A-Za-z0-9])(?:FY\s*(20\d\d)\s*)?Q([1-4])(?![A-Za-z0-9])", re.I)


def _target(prefix, constraints, requested_margin):
    margin = max((m.start() for m in MARGIN.finditer(prefix)), default=-1)
    other = max((m.start() for m in OTHER_RATE.finditer(prefix)), default=-1)
    explicit = margin > other or (other < 0 and constraints.get("metric") == METRIC)
    implicit = (requested_margin and other < 0 and not ATTRIBUTION.search(prefix)
                and (len(FISCAL.findall(prefix)) == 2 or YOY.search(prefix) or SEQUENTIAL.search(prefix)))
    return (explicit or implicit) and COMPARISON.search(prefix)


def _window(prefix):
    """Recognize the relationship and orientation when they are explicit."""
    labels = list(FISCAL.finditer(prefix))
    if len(labels) == 2:
        left, right = labels
        left_year, left_q = left.groups()
        right_year, right_q = right.groups()
        ordered = bool(PAIR_LINK.search(prefix[left.end():right.start()]))
        if (not left_year or not right_year or left_year == right_year) and abs(int(left_q) - int(right_q)) == 1:
            return "sequential", ordered and left_q < right_q
        if left_year and right_year and left_q == right_q and abs(int(left_year) - int(right_year)) == 1:
            return "yoy", ordered and left_year < right_year
    relations = [(m.start(), label) for pattern, label in ((YOY, "yoy"), (SEQUENTIAL, "sequential"))
                 for m in pattern.finditer(prefix)]
    return max(relations, default=(-1, None))[1], False


def _operating_rate(row, records, entity):
    operand_ids = row.get("operand_ids", [])
    if (row.get("operation") != "margin" or row.get("entity") != entity or row.get("stale")
            or not row.get("period_start") or not row.get("period_end") or len(operand_ids) != 2
            or any(ref not in records for ref in operand_ids)):
        return False
    return [records[ref].get("metric") for ref in operand_ids] == ["operating_income", "revenue"]


PRINTED_BASIS = re.compile(r"摘要表|披露表|整数(?:展示|利润率)|\b(?:summary table|as printed|reported table)\b", re.I)
SUMMARY_RATE_ROW = re.compile(
    r"\boperating margin\s+([-+]?\d+(?:\.\d+)?)\s*%\s+([-+]?\d+(?:\.\d+)?)\s*%"
    r"\s+(up|down)\s+(\d+(?:\.\d+)?)\s*(?:ppts?\b|percentage points?\b)", re.I)


def _published_delta(prefix, rows, documents, records):
    """Match a labelled printed comparison to the same two calculation operands."""
    if not PRINTED_BASIS.search(prefix):
        return None
    rates = [(value, tolerance) for _, _, (_, unit, value, tolerance) in _quantity_spans(prefix) if unit == "%"]
    if len(rates) != 2 or any(row.get("value") is None or
            abs(Decimal(str(row["value"])) - value) > tolerance for row, (value, tolerance) in zip(rows, rates)):
        return None
    left, right = (value for value, _ in rates)
    newest = max(rows, key=lambda row: row["period_end"])
    table_docs = {records[ref].get("document_id") for ref in newest["operand_ids"]}
    for document in documents:
        if (document.get("entity") != newest.get("entity") or not document.get("document_id")
                or table_docs != {document["document_id"]} or document.get("document_report_date") != newest["period_end"]):
            continue
        text = document.get("text", "")
        for match in SUMMARY_RATE_ROW.finditer(text):
            headers = list(re.finditer(r"\b((?:non[ -]?)?gaap|quarterly|annual)\b", text[:match.start()], re.I))
            periods = [m[1].lower() for m in headers if m[1].lower() in ("quarterly", "annual")]
            bases = [m[1].lower() for m in headers if m[1].lower().endswith("gaap")]
            if not periods or periods[-1] != "quarterly" or not bases or bases[-1] != "gaap":
                continue
            first, second, direction, size = match.groups()
            signed = Decimal(size) * (-1 if direction.lower() == "down" else 1)
            if (left, right) == (Decimal(first), Decimal(second)) and left - right == signed:
                return signed
    return None


def _matches(calc, records, prefix, printed, value, tolerance, constraints, documents):
    ids = calc.get("operand_ids", [])
    if len(ids) != 2 or any(ref not in records for ref in ids):
        return False
    current, prior = rows = tuple(records[ref] for ref in ids)
    if any(not _operating_rate(row, records, calc.get("entity")) for row in rows):
        return False
    try:
        if any(duration_class(row["period_start"], row["period_end"]) != "quarter_length" for row in rows):
            return False
        distance = (parse_day(current["period_start"]) - parse_day(prior["period_end"])).days
        year_days = (parse_day(current["period_end"]) - parse_day(prior["period_end"])).days
    except DataError:
        return False
    horizon, reverse = _window(prefix)
    if any(constraints.get(field) and constraints[field] != row.get(date)
           for row, fields in zip(rows[::-1] if reverse else rows,
                                  (("period_start", "period_end"), ("comparison_period_start", "comparison_period_end")))
           for field, date in zip(fields, ("period_start", "period_end"))):
        return False
    if distance < 1 or (horizon == "sequential" and distance > 7) or (horizon == "yoy" and not 350 <= year_days <= 380):
        return False
    delta = Decimal(str(calc["value"])) * (-1 if reverse else 1)
    explicit_sign = printed.startswith(("+", "-", "−", "正", "负"))
    words = list(DIRECTION.finditer(prefix))
    if words:
        negative = bool(words[-1]["down"])
        if explicit_sign and negative != printed.startswith(("-", "−", "负")):
            return False
        value = abs(value) * (-1 if negative else 1)
    elif not explicit_sign and not value.is_signed():
        delta = abs(delta)
    if delta.is_finite() and abs(value - delta) <= tolerance:
        return True
    published = _published_delta(prefix, rows[::-1] if reverse else rows, documents, records)
    return published is not None and value == published


def _quarterly(text):
    return bool(FISCAL.search(text) or QUARTER_WORD.search(text) or SEQUENTIAL.search(text))


def defects(claim, references, records, question):
    """Restrict the rule to labelled operating-margin percentage-point comparisons."""
    constraints = claim.get("constraints", {})
    if not isinstance(constraints, dict):
        return
    calcs = [record for record in references if record.get("metric") == METRIC
             and record.get("operation") == "percentage_point_change" and record.get("unit") == "percentage_points"
             and type(record.get("value")) in (int, float) and not record.get("stale")]
    cited_docs = [r for r in references if r.get("kind") == "document" and not r.get("stale")]
    documents = [" ".join(r.get("text", "").casefold().split()) for r in cited_docs]
    quarterly_question = _quarterly(question)
    requested_margin = bool(quarterly_question and MARGIN.search(question) and COMPARISON.search(question))
    clauses = re.split(r"[。；！？!?\n]|(?<=\.)\s+", claim["text"])
    for clause in clauses:
        if not quarterly_question and not _quarterly(clause):
            continue
        for start, _, (printed, unit, value, tolerance) in _quantity_spans(clause):
            prefix = clause[:start]
            if unit != "percentage_points" or REJECTED_RATE.search(prefix) or not _target(prefix, constraints, requested_margin):
                continue
            if not calcs and any(" ".join(clause.casefold().split()).rstrip(".") in text for text in documents):
                continue
            if not any(_matches(calc, records, prefix, printed, value, tolerance, constraints, cited_docs) for calc in calcs):
                yield printed, [calc["id"] for calc in calcs]
