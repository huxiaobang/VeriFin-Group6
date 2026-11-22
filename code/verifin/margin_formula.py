"""Source-backed calculations for explicitly requested standard profit margins."""
from collections import defaultdict
from decimal import Decimal
import re

from .data import DataError
from .display_numbers import CURRENCIES, CURRENCY, NUMBER, REJECTED_RATE, SCALE, SCALES, _matches, _quantity_spans, percent_multiplier
from .financial import duration_class
from .filing_links import _LINK, native_fact_quarter
from .metric_language import MARGIN_NUMERATORS as NUMERATORS, QUALIFIED_BASIS, metric_mentions, metric_spans
from .margin_reconciliation import quarter_side
from .operating_margin import NONCONSOLIDATED, SHORT_FY, YOY, headline_label
from .reporting_period import reported_period
from .request_review import _direct_metric, request_parts
from .skill_calculations import previous_quarter, quarter_label, requested_fiscal_year
from .statement_amount_cells import (
    CURRENCIES as STATEMENT_CURRENCIES,
    CURRENCY as STATEMENT_CURRENCY,
    DOLLARS,
)


CALCULATION = re.compile(
    r"[计計]算公式|[给給列写寫展示说說]出?[^。！？?]{0,14}(?:公式|算式|[计計]算[过過]程)|"
    r"(?:怎[么麼]|如何)[计計]?算|(?:[请請]|[并並])\s*[计計]算|"
    r"\b(?:calculate|compute)\b|\b(?:show|give|provide|include|explain|state|list)\b[^.!?]{0,80}"
    r"\b(?:formula|calculation|arithmetic|working|how\b[^.!?]{0,30}\bcalculat\w*)\b|"
    r"\bhow\b[^.!?]{0,55}\b(?:calculate\w*|compute\w*|derive\w*|work(?:ed)? out)\b", re.I)
NO_CALCULATION = re.compile(r"无需|無需|不用|不必|不要|不需|\b(?:without|no|don't|do not|skip|exclude)\b[^.!?]{0,30}"
                            r"\b(?:showing|calculat\w*|formula|arithmetic|computation)\b", re.I)
COMPARISON = re.compile(r"同比|环比|環比|[变變]动|差[额額]|百分点|百分點|\b(?:change|growth|difference|versus|vs\.?|"
                        r"year.over.year|quarter.over.quarter|comparison)\b", re.I)
VALUE_QUESTION = re.compile(r"多少|\b(?:what (?:was|were)|how much|reported|ended|quarter|fiscal year)\b", re.I)
_FORMULA_WORDS = r"(?:公式|算式|[计計]算[过過]程)"
_FORMULA_REFERENCE = re.compile(
    rf"(?:[请請]\s*)?(?:(?:能(?:不能)?|可(?:以|不可以))\s*)?"
    rf"(?:[给給](?:我|出|一下)?|提供|展示|列[出个個]?|[写寫]出)\s*(?:一下\s*)?"
    rf"{_FORMULA_WORDS}(?:\s*(?:或者?|和|及|/)\s*{_FORMULA_WORDS})?\s*[吗嗎呢]?[。！？?!.]*|"
    r"(?:(?:can|could|would) you |please )?(?:show|give|provide|explain) (?:me )?(?:the )?"
    r"(?:formula|calculation|working)(?: (?:or|and) (?:the )?(?:formula|calculation|working))?[?!.]*", re.I)
MATH = re.compile(r"divid\w*|ratio of|算式|除以|除[於于]|÷|(?<=\d)\s*/\s*[$€£]?\s*\d", re.I)
_MONEY = r"(?:" + CURRENCY + "|" + STATEMENT_CURRENCY.pattern + r")(?![A-Za-z])"
_AMOUNT_SUFFIX = rf"(?:{SCALE.pattern})?(?:\s*{_MONEY})?"
_BARE_AMOUNT = rf"(?:[+−-]\s*)?(?:{_MONEY}\s*)?(?:[+−-]\s*)?{NUMBER.pattern}{_AMOUNT_SUFFIX}"
# Parentheses around one financial amount express a negative value. In a
# grouped quotient "(profit ÷ sales)", neither amount has its own parentheses.
AMOUNT = rf"(?:(?:{_MONEY}\s*)?[（(]\s*{_BARE_AMOUNT}\s*[）)]{_AMOUNT_SUFFIX}|{_BARE_AMOUNT})"
DIVIDE = r"[/÷]|divided by|除以|除[於于]"
DIVISION = re.compile(r"(?P<n>" + AMOUNT + r")\s*(?:" + DIVIDE + r")\s*(?P<d>" + AMOUNT + r")", re.I)
MULTIPLIER = re.compile(r"\s*[)）]?\s*(?:[×*x]|times|乘以)\s*(" + NUMBER.pattern + r")", re.I)
SHARED_SCALE = re.compile(r"[（(](?:both |amounts )?(?:in )?(?:(?P<unit>" + _MONEY + r")\s+)?"
                          r"(?P<scale>thousands|millions|billions)[）)]", re.I)
CLAUSES = re.compile(r"[。；;！？!?\n]|(?<=\.)\s+")
RATE_CLAUSES = re.compile(r"[。；;！？!?，、\n]|,(?!\d)|(?<=\.)\s+")
RATE_CHANGE = re.compile(r"(?:[变變][动動]|上升|下降|提高|下跌|上[涨漲]|增[长長]|[减減]少|改善|"
                         r"\b(?:up|down|increased by|decreased by|rose by|fell by))\s*(?:了|约|約)?\s*$", re.I)
DIRECT_RATE_QUESTION = re.compile(r"多少|列出|列示|[给給]出|\b(?:what (?:was|were|is|are)|how much|list|report)\b", re.I)
GENERIC_MARGIN = re.compile(r"利[润潤]率|\b(?:profit(?:ability)? )?margins?\b", re.I)


def asks_calculation(text):
    return bool((CALCULATION.search(text) or formula_followup(text)) and not NO_CALCULATION.search(text))


def formula_followup(text):
    return bool(_FORMULA_REFERENCE.fullmatch(text.strip()))


def _request(context):
    return context.get("request") or context.get("episode", {}).get("request", "")


def requested_metric(context):
    question = _request(context)
    if target := context.get("margin_formula_target"):
        if formula_followup(question) and target.get("metric") in NUMERATORS:
            return target["metric"]
    parts = request_parts(question).values()
    if not asks_calculation(question) or QUALIFIED_BASIS.search(question) or NONCONSOLIDATED.search(question):
        return None
    measures = metric_mentions(question) & NUMERATORS.keys()
    if len(measures) != 1 or COMPARISON.search(question):
        return None
    metric = next(iter(measures))
    if parts and not any(asks_calculation(part) and (not metric_mentions(part) or metric in metric_mentions(part))
                         for part in parts):
        return None
    # A definition alone needs semantic review. A dated or expressly numerical
    # question asks for this result and its actual inputs.
    if (quarter_label(question) or requested_fiscal_year(question) or context.get("scope", {}).get("period_end")
            or VALUE_QUESTION.search(question)):
        return metric
    return None


def reviewed_followup_target(question, report, scope, current_records):
    """Resolve an elliptical formula request from one delivered, current margin receipt."""
    if not formula_followup(question) or not report.get("verification_available"):
        return None
    old_scope = report["scope"]
    entities = scope.get("entities")
    if (not entities or len(entities) != 1 or entities != old_scope.get("entities")
            or scope.get("cutoff") != old_scope.get("cutoff")
            or scope.get("metrics") != old_scope.get("metrics")):
        return None
    archives = {r["id"]: r for r in report["evidence"] + report["calculations"]}
    answered_claims = {ref for row in report.get("request_coverage", []) if row["status"] == "answered"
                       for ref in (row.get("claim_id"), *row.get("supporting_claim_ids", []),
                                   *row.get("joint_claim_ids", []))}
    versions = report.get("record_versions", {}).get("claim", {})
    targets = []
    for claim in report["claims"]:
        verdict = claim.get("critic_verdict") or {}
        if (claim["id"] not in answered_claims or claim.get("status") != "Supported" or claim.get("stale")
                or versions.get(claim["id"]) != claim.get("version") or claim.get("version") is None
                or claim.get("scope_version") != report.get("scope_version")
                or verdict.get("claim_id") != claim["id"] or verdict.get("status") != "Supported"):
            continue
        cited = set(claim.get("evidence_ids", [])) | set(claim.get("calculation_ids", []))
        for ref in set(claim.get("calculation_ids", [])).intersection(verdict.get("evidence_refs", [])):
            calc = archives.get(ref, {})
            metric = calc.get("metric")
            if metric not in NUMERATORS or calc.get("operation") != "margin":
                continue
            receipt = next(claim_receipts({**claim, "calculation_ids": [ref]}, archives, metric,
                                          {**scope, "entity": entities[0]}, request=report["request"]), None)
            if not receipt or not _has_duration(calc.get("period_start"), calc.get("period_end"), "quarter_length"):
                continue
            _, operands = receipt
            refs = {*cited, *(r["id"] for r in operands)}
            if any(not current_records.get(i) or current_records[i].get("stale")
                   or archives.get(i, {}).get("version") != current_records[i].get("version") for i in refs):
                continue
            if any(calc.get("operand_versions", {}).get(r["id"]) != r.get("version") for r in operands):
                continue
            targets.append({k: calc[k] for k in ("entity", "metric", "period_start", "period_end")})
    if targets and all(target == targets[0] for target in targets):
        target = targets[0]
        quarter = quarter_label(report["request"])
        if quarter:
            target["quarter"] = quarter
        return target
    return None


def calculation_scope(context):
    scope = context.get("scope", {})
    if target := context.get("margin_formula_target"):
        return {**scope, **{k: target[k] for k in ("entity", "period_start", "period_end")}}
    return scope


def _label(text, end):
    label = quarter_label(text)
    if label:
        return label
    short = set(SHORT_FY.findall(text))
    if isinstance(end, str) and re.match(r"^\d{4}-", end) and len(short) == 1:
        year, quarter = next(iter(short))
        years = [y for y in range(int(end[:4]) - 1, int(end[:4]) + 2) if str(y).endswith(year)]
        if years:
            return f"{years[0]}Q{quarter}"
    return None


def _has_duration(start, end, label):
    try:
        return duration_class(start, end) == label
    except DataError:
        return False


def _row_quarters(row):
    return {_label(row.get("fiscal_quarter") or "", row.get("period_end")), native_fact_quarter(row)} - {None}


def _quarter_ends(evidence, entity, quarter):
    ends = {r["document_report_date"] for r in evidence if r.get("kind") == "document"
            and not r.get("stale") and r.get("entity") == entity and r.get("start") == 0
            and r.get("document_report_date") and headline_label(r["text"], r["document_report_date"]) == quarter}
    ends.update(r["period_end"] for r in evidence if r.get("entity") == entity and not r.get("stale")
                and r.get("period_end") and _row_quarters(r) == {quarter})
    return ends


def draft_calculation(context):
    """Choose only a single source-backed reporting window and one comparable pair."""
    metric = requested_metric(context)
    scope = calculation_scope(context)
    entities = scope.get("entities", [])
    if not metric or len(entities) != 1 or not any(t["name"] == "calculate" for t in context["tools"]):
        return None
    entity = entities[0]
    start, end = scope.get("period_start"), scope.get("period_end")
    request = " ".join(context.get("request_parts", {}).values()) or context["episode"]["request"]
    quarter = quarter_label(request)
    fiscal_year = requested_fiscal_year(request)
    if quarter:
        ends = _quarter_ends(context["evidence"], entity, quarter)
        if len(ends) != 1 or (end and end not in ends):
            return None
        end = next(iter(ends))
    if not end or (not quarter and (not start or fiscal_year)):
        return None
    rows = defaultdict(list)
    for row in context["evidence"]:
        first = row.get("period_start")
        name = row.get("metric")
        if (row.get("stale") or row.get("entity") != entity or row.get("period_end") != end or not first
                or (start and start != first) or (quarter and not _has_duration(first, end, "quarter_length"))
                or name not in (NUMERATORS[metric], "revenue") or row.get("business_scope") != "consolidated"
                or row.get("basis") not in {"US-GAAP", "GAAP", "IFRS"} or not row.get("currency")
                or row.get("unit") != row["currency"] or row.get("scale", 1) != 1
                or type(row.get("value")) not in (int, float)):
            continue
        rows[name].append(row)
    # Select a consistent common reporting context, declining disputed inputs.
    pairs = [(a, b) for a in rows[NUMERATORS[metric]] for b in rows["revenue"] if b["value"] > 0
             and all(a[k] == b[k] for k in ("period_start", "basis", "currency"))]
    if len({(a["period_start"], a["basis"], a["currency"], a["value"], b["value"]) for a,b in pairs}) != 1:
        return None
    # Prefer cells from the same original statement when also available in another source.
    a, b = min(pairs, key=lambda p: (not (p[0].get("table_id") and p[0]["table_id"] == p[1].get("table_id")),
                                    p[0]["id"], p[1]["id"]))
    if any(_receipt(row, {r["id"]: r for r in context["evidence"]}, metric)
           and all(row.get(k) == a[k] for k in ("entity", "period_start", "period_end", "business_scope", "basis"))
           for row in context["calculations"]):
        return None
    args = {"operation": "margin", "operand_ids": [a["id"], b["id"]]}
    if any(r.get("tool") == "calculate" and r.get("arguments") == args
           for r in context["episode"].get("recent_tool_results", [])):
        return None
    return args


def _receipt(calc, records, metric):
    operation = calc.get("operation")
    numerator = NUMERATORS[metric]
    expected = ("%", metric) if operation == "margin" else ("ratio", numerator + "_to_revenue")
    ids = calc.get("operand_ids", [])
    if (operation not in {"margin", "ratio"} or (calc.get("unit"), calc.get("metric")) != expected
            or calc.get("stale") or len(ids) != 2 or any(ref not in records or records[ref].get("stale") for ref in ids)):
        return None
    a, b = (records[ref] for ref in ids)
    if ([a.get("metric"), b.get("metric")] != [numerator, "revenue"]
            or any(not a.get(k) or a[k] != b.get(k) for k in ("entity", "period_end", "basis", "business_scope", "unit"))
            or a.get("period_start") != b.get("period_start") or a.get("currency") != b.get("currency")
            or any(row.get("scale", 1) != 1 or type(row.get("value")) not in (int, float) for row in (a, b))
            or any(calc.get(k) != a.get(k) for k in ("entity", "period_start", "period_end", "basis", "business_scope"))
            or b["value"] <= 0):
        return None
    formula = f"{ids[0]} / {ids[1]}" + (" * 100" if operation == "margin" else "")
    recorded = calc.get("formula")
    return (a, b) if isinstance(recorded, str) and "".join(recorded.split()) == "".join(formula.split()) else None


def _margin_rates(text, default=None):
    """Bind a displayed rate to its local named measure, independently of other figures."""
    named = metric_mentions(text) & NUMERATORS.keys()
    for clause in RATE_CLAUSES.split(text):
        cursor = 0
        for start, end, quantity in _quantity_spans(clause):
            if (quantity[1] != "%" or percent_multiplier(clause, start, end, quantity)
                    or REJECTED_RATE.search(clause[:start])):
                continue
            prefix = clause[cursor:start]
            cursor = end
            names = [metric for _, metric in metric_spans(prefix)]
            if RATE_CHANGE.search(prefix):
                continue
            if names and names[-1] in NUMERATORS:
                yield names[-1], quantity
            elif MATH.search(prefix) and (margins := [name for name in names if name in NUMERATORS]):
                yield margins[-1], quantity
            elif (following := next(metric_spans(clause[end:]), None)) and following[1] in NUMERATORS and re.fullmatch(
                    r"\s*(?:的|(?:for|as)(?:\s+the)?)?\s*", clause[end:end + following[0].start()], re.I):
                yield following[1], quantity
            elif default and not named and not names:
                yield default, quantity


def _source_has_margin(metric, quantity, citations, records):
    """Match both the displayed amount and the measure of its cited result."""
    _, unit, value, tolerance = quantity
    for row in citations:
        if row.get("stale"):
            continue
        if row.get("operation"):
            if not _receipt(row, records, metric):
                continue
            if _matches(unit, value, tolerance, row):
                return True
        elif row.get("kind") == "document":
            for label, (_, _, reported, _) in _margin_rates(row.get("text") or ""):
                if label == metric and abs(reported - value) <= tolerance:
                    return True
        else:
            label = str(row.get("row_label") or row.get("metric", "")).replace("_", " ")
            if metric_mentions(label) & NUMERATORS.keys() == {metric} and _matches(unit, value, tolerance, row):
                return True
    return False


def _margin_value_defects(claim, citations, records):
    constraints = claim.get("constraints")
    declared = constraints.get("metric") if isinstance(constraints, dict) else None
    declaration = metric_mentions(declared.replace("_", " ")) & NUMERATORS.keys() if isinstance(declared, str) else set()
    rates = list(_margin_rates(claim["text"], next(iter(declaration)) if len(declaration) == 1 else None))
    measures = {metric for metric, _ in rates}
    if declaration and measures and declaration.isdisjoint(measures):
        yield "context_mismatch", [], "The named margin in the answer differs from the claim's declared metric."
    for metric, quantity in rates:
        if not _source_has_margin(metric, quantity, citations, records):
            yield "missing_reference", [], f"Displayed {metric.replace('_', ' ')} {quantity[0]} has no cited result for that measure and value."


def _currency_code(text):
    code = text.upper()
    return CURRENCIES.get(text.casefold()) or STATEMENT_CURRENCIES.get(code, code)


def _operand_display(text):
    number = NUMBER.search(text)
    suffix = SCALE.match(re.sub(r"[）)]", " ", text[number.end():]))
    factor = SCALES[suffix.group().strip().lower()] if suffix else 1
    units = {_currency_code(match.group()) for match in re.finditer(_MONEY, text, re.I)}
    prefix = text[:number.start()]
    value = Decimal(number.group().replace(",", ""))
    negative = bool(re.search(r"[-−]", prefix) or (re.search(r"[（(]", prefix) and "+" not in prefix))
    return (-value if negative else value), units, factor, bool(suffix)


def _reproduces(equation, a, b):
    x, x_units, x_scale, x_scaled = _operand_display(equation["n"])
    y, y_units, y_scale, y_scaled = _operand_display(equation["d"])
    if any(unit != row["unit"] and (unit != "$" or row["unit"] not in DOLLARS)
           for units, row in ((x_units, a), (y_units, b)) for unit in units):
        return False
    source_x, source_y = (Decimal(str(row["value"])) for row in (a, b))
    if y <= 0:
        return False
    shared = SHARED_SCALE.search(equation.string[equation.end():])
    if shared and shared["unit"]:
        unit = _currency_code(shared["unit"])
        if unit != a["unit"] and (unit != "$" or a["unit"] not in DOLLARS):
            return False
    if not shared and not any((x_units, y_units, x_scaled, y_scaled)):
        # Unitless inputs may share a conventional scale. Their ratio alone
        # cannot establish the individual amounts printed in the formula.
        return any(x * scale == source_x and y * scale == source_y for scale in {1, *SCALES.values()})
    common_scale = SCALES[shared["scale"].casefold()[:-1]] if shared else None
    x_factors = (x_scale,) if x_scaled else (common_scale,) if shared else (1, y_scale)
    y_factors = (y_scale,) if y_scaled else (common_scale,) if shared else (1, x_scale)
    return any(x * scale == source_x for scale in x_factors) and any(y * scale == source_y for scale in y_factors)


def claim_receipts(claim, records, metric, scope=None, *, request=""):
    constraints = claim.get("constraints")
    expected = (scope or {}, constraints if isinstance(constraints, dict) else {})
    quarter = quarter_label(request)
    fiscal_year = requested_fiscal_year(request)
    for ref in claim.get("calculation_ids", []):
        calc = records.get(ref, {})
        operands = _receipt(calc, records, metric)
        if not operands or any(bounds.get(k) and bounds[k] != calc.get(k) for bounds in expected
                               for k in ("entity", "period_start", "period_end", "basis", "business_scope")):
            continue
        first, last = calc.get("period_start"), calc.get("period_end")
        if fiscal_year and not _has_duration(first, last, "annual_length"):
            continue
        if quarter:
            if first and not _has_duration(first, last, "quarter_length"):
                continue
            labels = {label for row in operands for label in _row_quarters(row)}
            ends = _quarter_ends(records.values(), calc.get("entity"), quarter)
            if (labels and labels != {quarter}) or (ends and ends != {last}):
                continue
            if not (labels or ends or expected[0].get("period_end") and expected[0].get("period_start")):
                continue
        yield calc, operands


def _prior_quarter_receipts(claim, records, metric, scope, quarter, prior):
    """Allow a named year-ago comparison only with a source-backed quarterly period."""
    active_end = scope.get("period_end")
    prior_scope = {k: v for k, v in scope.items() if k not in ("period_start", "period_end")}
    for calc, operands in claim_receipts(claim, records, metric, prior_scope):
        entity = calc["entity"]
        if scope.get("entities") and entity not in scope["entities"]:
            continue
        ends = _quarter_ends(records.values(), entity, quarter)
        if len(ends) != 1 and not (active_end and not ends):
            continue
        if active_end and ends and ends != {active_end}:
            continue
        end = active_end or next(iter(ends))
        if quarter_side(calc, end) != 1:
            continue
        labels = {label for row in operands for label in _row_quarters(row)}
        prior_ends = _quarter_ends(records.values(), entity, prior)
        if (labels and labels != {prior}) or (prior_ends and prior_ends != {calc["period_end"]}):
            continue
        if labels or prior_ends:
            yield calc, operands
            continue

        # A comparative column may carry a date in place of an explicit fiscal
        # label. Bind it to the same original table's current-quarter columns.
        origin = ("document_id", "table_id", "snapshot_hash")
        anchor = tuple(operands[0].get(k) for k in origin)
        if not all(anchor) or tuple(operands[1].get(k) for k in origin) != anchor:
            continue
        measures = {row.get("metric") for row in records.values() if row.get("entity") == entity
                    and not row.get("stale") and row.get("basis") == calc["basis"]
                    and row.get("business_scope") == calc["business_scope"]
                    and row.get("currency") == operands[0].get("currency")
                    and tuple(row.get(k) for k in origin) == anchor
                    and quarter_side(row, end, scope.get("period_start")) == 0}
        if measures.issuperset({NUMERATORS[metric], "revenue"}):
            yield calc, operands


def defects(context, claim, citations, records):
    # The filing-link verifier receives the original text separately. A numeric
    # accession path is not a displayed arithmetic expression.
    text = _LINK.sub(" ", claim["text"])
    if text != claim["text"]:
        claim = {**claim, "text": text}
    yield from _margin_value_defects(claim, citations, records)
    metric = requested_metric(context)
    if not metric:
        return
    request = _request(context)
    quarter = quarter_label(request)
    scope = calculation_scope(context)
    prior = previous_quarter(quarter, year_on_year=True) if quarter and YOY.search(request) else None
    if prior and quarter_label(text) == prior and not context.get("margin_formula_target"):
        matching = list(_prior_quarter_receipts(claim, records, metric, scope, quarter, prior))
        quarter = prior
    else:
        matching = list(claim_receipts(claim, records, metric, scope, request=request))
    constraints = claim.get("constraints")
    named = metric in metric_mentions(text) or isinstance(constraints, dict) and constraints.get("metric") == metric
    if (target := context.get("margin_formula_target")) and (matching or named or DIVISION.search(text)):
        labels = metric_mentions(text) & NUMERATORS.keys()
        period = reported_period(text, statement=True) or {}
        displayed_quarter = quarter_label(text)
        if ((labels and metric not in labels)
                or (target.get("quarter") and displayed_quarter and displayed_quarter != target["quarter"])
                or (period.get("kind") == "annual")
                or any(period.get(k) and period[k] != target["period_" + k] for k in ("start", "end"))):
            yield "context_mismatch", [c["id"] for c, _ in matching], (
                "The displayed formula's metric or reporting period differs from the reviewed follow-up target.")
            return
    standalone_formula = _starts_with_division(text) and any(q[1] == "%" for _, _, q in _quantity_spans(text))
    if not named and not standalone_formula and not (matching and (MATH.search(text) or DIVISION.search(text))):
        return
    for clause in CLAUSES.split(text):
        clause_metrics = metric_mentions(clause) & NUMERATORS.keys()
        if clause_metrics and metric not in clause_metrics:
            continue
        for start, end, quantity in _quantity_spans(clause):
            literal, unit, value, tol = quantity
            prefix = clause[:start]
            if (unit != "%" or REJECTED_RATE.search(prefix) or
                    percent_multiplier(clause, start, end, quantity) or
                    (not clause_metrics and not MATH.search(prefix) and not (matching and "=" in prefix))):
                continue
            if not matching and not MATH.search(text) and any(
                    (r.get("metric") == metric and _matches(unit, value, tol, r)) or
                    (r.get("kind") == "document" and clause_metrics == {metric} and
                     " ".join(clause.strip().casefold().split()).rstrip(".") in " ".join(r.get("text", "").casefold().split()))
                    for r in citations if not r.get("stale")):
                continue
            applicable = [(calc, rows) for calc, rows in matching
                          if not (claim_label := _label(clause, calc.get("period_end"))) or not quarter or claim_label == quarter]
            if not any(_matches(unit, value, tol, calc) for calc, _ in applicable):
                yield "numerical_inconsistency" if matching else "missing_reference", [c["id"] for c, _ in matching], (
                    f"Displayed margin {literal} has no matching saved calculation for the requested period and operands.")
        for equation in DIVISION.finditer(clause):
            multiplier = MULTIPLIER.match(clause[equation.end():])
            valid_scale = not multiplier or Decimal(multiplier[1].replace(",", "")) == 100
            if not valid_scale or not any(_reproduces(equation, a, b) for _, (a, b) in matching):
                yield "numerical_inconsistency" if matching else "missing_reference", [c["id"] for c, _ in matching], (
                    f"Displayed division {equation.group().strip()} does not reproduce the requested margin from cited inputs.")
        for operator in re.finditer(r"divided by|除以|除[於于]", clause, re.I):
            operands = {NUMERATORS[metric], "revenue"}
            if (metric_mentions(clause[:operator.start()]) & operands == {"revenue"}
                    and metric_mentions(clause[operator.end():]) & operands == {NUMERATORS[metric]}):
                yield "numerical_inconsistency", [c["id"] for c, _ in matching], (
                    "The stated margin formula puts revenue before profit in the division.")


def _starts_with_division(text):
    equation = DIVISION.search(text)
    return equation is not None and not text[:equation.start()].strip("(（ \t\r\n")


def _shows_calculation(text, metric):
    text = _LINK.sub(" ", text)
    if DIVISION.search(text):
        return True
    numerator = NUMERATORS[metric]
    terms = {numerator, "revenue"}
    return any(metric_mentions(text[:operator.start()]) & terms == {numerator}
               and metric_mentions(text[operator.end():]) & terms == {"revenue"}
               for operator in re.finditer(DIVIDE, text, re.I))


def reusable_reference(claim, context):
    """Recover an omitted citation only from the exact original inputs in this draft."""
    metric = requested_metric(context)
    scope = calculation_scope(context)
    entities = scope.get("entities", [])
    if not metric or claim.get("calculation_ids") or len(entities) != 1:
        return None
    text = claim["text"]
    measures = metric_mentions(text) & NUMERATORS.keys()
    if measures != {metric} and (measures or not _starts_with_division(text)):
        return None
    cited = set(claim.get("evidence_ids", []))
    records = {r["id"]: r for r in context["evidence"] + context["calculations"]}
    candidates = [c["id"] for c in context["calculations"] if c.get("entity") == entities[0]
                  and set(c.get("operand_ids", ())).issubset(cited)]
    if not candidates:
        return None
    request = context["episode"]["request"]
    quarter = quarter_label(request)
    matches = list(claim_receipts({**claim, "calculation_ids": candidates}, records, metric, scope, request=request))
    if len(matches) != 1:
        return None
    calc, operands = matches[0]
    if quarter:
        if _quarter_ends(context["evidence"], entities[0], quarter) != {calc.get("period_end")}:
            return None
    elif not scope.get("period_end"):
        from .answer_periods import _checked_periods

        observations = [r for r in context["episode"].get("latest_quarter_observations", [])
                        if r["request"] == request and r["entity"] == entities[0]
                        and (not scope.get("cutoff") or r.get("cutoff") == scope["cutoff"])]
        periods, accession, _ = _checked_periods(observations, records, scope.get("cutoff"))
        if ((calc.get("period_start"), calc.get("period_end")) not in periods
                or accession and any(r.get("accession") != accession for r in operands)):
            return None
    if any(calc.get("operand_versions", {}).get(row["id"]) != row.get("version") for row in operands):
        return None
    rate = Decimal(str(operands[0]["value"])) / Decimal(str(operands[1]["value"]))
    if calc.get("value") != float(rate * (100 if calc["unit"] == "%" else 1)):
        return None
    trial = {**claim, "calculation_ids": [calc["id"]]}
    if (any(q[1] == "%" and not percent_multiplier(text, start, end, q) and _matches(*q[1:], calc)
            for start, end, q in _quantity_spans(text)) and not any(defects(context, trial, (*operands, calc), records))):
        return calc
    return None


def check_coverage(context, rows, verdicts):
    metric = requested_metric(context)
    records = {r["id"]: r for r in context["evidence"] + context["calculations"]}
    bad = {i["target_id"] for i in context["rule_findings"] if not i.get("scope")}
    claims = {c["id"]: c for c in context["claims"] if c["id"] not in bad and not c.get("stale")
              and verdicts.get(c["id"], {}).get("status") == "Supported"}
    request = context["episode"]["request"]
    for row in rows:
        question = context.get("request_parts", {}).get(row["part"], "")
        measures = metric_mentions(question)
        direct = _direct_metric(question)
        requested = measures & NUMERATORS.keys()
        level_question = (not COMPARISON.search(question)
                          and (direct in NUMERATORS or DIRECT_RATE_QUESTION.search(question)
                               and (requested and requested == measures or not measures and GENERIC_MARGIN.search(question))))
        refs = [row.get("claim_id"), *row.get("supporting_claim_ids", [])]
        usable = [claims[ref] for ref in refs if ref in claims]
        if row.get("status") in {"answered", "partial"} and level_question:
            answered = set()
            for claim in usable:
                quote = (row.get("answer_sentence") or "") if claim["id"] == row.get("claim_id") else claim["text"]
                confirmed = set(verdicts[claim["id"]].get("evidence_refs", []))
                cited = [records[ref] for ref in claim.get("evidence_ids", []) + claim.get("calculation_ids", [])
                         if ref in confirmed and ref in records]
                for label, quantity in _margin_rates(quote, direct if direct in NUMERATORS else None):
                    if _source_has_margin(label, quantity, cited, records):
                        answered.add(label)
            if not answered or not requested <= answered:
                row.update(status="gap", missing_support="答句尚未给出与所问利润率口径一致、且有原始证据或计算支持的数值。")
                continue
        needs_formula = asks_calculation(question)
        if not metric or row.get("status") != "answered" or not (needs_formula or metric in measures):
            continue
        if needs_formula:
            scope = calculation_scope(context)
            covered = any(next(claim_receipts(c, records, metric, scope, request=request), None) for c in usable)
            gap = "未找到与所问利润率及其报告期对应的已核验计算记录和原始输入。"
            if covered and context.get("margin_formula_target") and not _shows_calculation(row.get("answer_sentence") or "", metric):
                covered = False
                gap = "已有可核验计算记录，但本轮答复尚未列出与原始输入相符的算式。"
        else:
            covered = any(metric in metric_mentions(c["text"]) and any(
                          q[1] == "%" and not percent_multiplier(c["text"], start, end, q)
                          for start, end, q in _quantity_spans(c["text"])) for c in usable)
            gap = "所问利润率尚未通过核验。"
        if covered:
            continue
        row["missing_support"] = gap
        row["status"] = "partial" if row.get("claim_id") in claims and measures else "gap"
    return rows


def equation(calc, rows):
    """Render input amounts in the original scale when the source uses one for both."""
    scales = {r.get("printed_scale", 1) for r in rows}
    scale = next(iter(scales)) if len(scales) == 1 else 1
    unit = rows[0]["unit"]
    scale_name = {1_000: "thousands", 1_000_000: "millions", 1_000_000_000: "billions"}.get(scale)
    if not scale_name:
        scale = 1
    numbers = [format(Decimal(str(r["value"])) / scale, ",f") for r in rows]
    result = Decimal(str(calc["value"])) * (100 if calc["unit"] == "ratio" else 1)
    return f"{numbers[0]} ÷ {numbers[1]} × 100% = {result:.2f}% ({unit}{' ' + scale_name if scale_name else ''})."
