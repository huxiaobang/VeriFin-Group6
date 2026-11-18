"""Compare explicitly unit-bearing display quantities to cited structured records."""
from decimal import Decimal
import re

from .financial import VALUATION_ALLOWANCE_EXPENSE
from .printed_amount import comparison_cell
from .record_context import compatible_records
from .metric_language import direct_amount_anchors, plain_amount_metric, QUALIFIED_BASIS


NUMBER = re.compile(r"(?<![a-zA-Z_0-9.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
SCALES = {"thousand": 1000, "k": 1000, "million": 10**6, "mn": 10**6, "mm": 10**6, "m": 10**6,
          "billion": 10**9, "bn": 10**9, "b": 10**9, "trillion": 10**12, "tn": 10**12, "t": 10**12,
          "万亿": 10**12, "十亿": 10**9, "亿": 10**8, "千万": 10**7, "百万": 10**6, "万": 10**4, "千": 1000}
SCALE = re.compile(r"\s*(thousand|million|billion|trillion|mn|mm|bn|tn|k|m|b|t)(?![a-z])|\s*(万亿|十亿|千万|百万|亿|万|千)", re.I)
CURRENCIES = {"美元":"USD", "人民币":"CNY", "元人民币":"CNY", "港元":"HKD", "欧元":"EUR", "英镑":"GBP",
              "usd":"USD", "us$":"USD", "us dollars":"USD", "dollars":"$", "$":"$",
              "eur":"EUR", "€":"EUR", "gbp":"GBP", "£":"GBP", "hkd":"HKD", "hk$":"HKD", "cny":"CNY", "rmb":"CNY"}
CURRENCY = r"us dollars|dollars|usd|us\$|eur|gbp|hkd|hk\$|cny|rmb|\$|€|£|美元|元人民币|人民币|港元|欧元|英镑"
PREFIX = re.compile(r"(?<![a-z])(?P<currency>" + CURRENCY + r")\s*[-+−负]?\s*$", re.I)
SUFFIX = re.compile(r"\s*(?P<unit>percentage[ -]points?|percent(?:age)?\b|basis[ -]points?|bps?\b|"
                    r"pp\b|个?百分点|个?基点|[%％]|shares?\b|股(?!价)|" + CURRENCY + r")", re.I)
PER_SHARE = re.compile(r"\s*(?:/\s*(?:shares?|股)|per\s+(?:diluted\s+|basic\s+)?share|每股)", re.I)
PER_SHARE_BEFORE = re.compile(r"(?:每股(?:摊薄|稀释|基本)?(?:亏损|收益|盈利|净利)?(?:为|约)?|"
                              r"\b(?:EPS|per (?:diluted |basic )?share)(?:(?:\s+(?:was|of|were|is))|为)?)\s*[-+−负]?\s*$", re.I)
DISPLAY_UNITS = {"%", "percentage_points", "shares", "USD/shares", *CURRENCIES.values(),
                 *(currency + "/share" for currency in CURRENCIES.values())}
DOWN = re.compile(r"(?:decreas(?:e|ed)|declin(?:e|ed)|down|fell|lower)(?:\s+(?:by|of))?\s*$|"
                  r"(?:(?:下降|减少|下滑|下跌|降低)|调减(?:本季|当期)?(?:经营现金流|净利润)?)了?约?\s*$", re.I)
LOWER_THAN = re.compile(r"(?:比|较)[^，,；;。.!！？?\n]{0,60}(?<!最)低(?:了)?(?:约)?\s*$")
BENEFIT_BEFORE = re.compile(r"(?:所得税收益|税收收益|税务收益)(?:金额)?(?:约为|为|约)?\s*$|"
                            r"\b(?:income )?tax benefit\s+(?:of|was)\s+(?:about\s+)?$", re.I)
RELEASE_BEFORE = re.compile(r"(?:释放|转回)(?:金额)?(?:约为|为|约)?\s*$")
BOOKED_BENEFIT = re.compile(r"^[，,]\s*(?:全部)?(?:作为|确认为|计为)[^非不未，,；;。.!！？?\n]{0,48}"
                            r"(?:所得税收益|税收收益)(?:入账|确认)?(?:[，,；;。.!！？?]|$)")
EXPENSE_SIGN = re.compile(r"所得税费用(?:方向|口径)(?:列示|呈列)?(?:时)?[，,]?\s*"
                          r"(?:其)?(?:符号)?(?:为|是)([正负])")
CONTRIBUTION_SIGN = re.compile(r"^\s*的(?P<sign>[正负])(?:向)?贡献(?=$|[，,。；;、！!？?\s])")
SHARED_RANGE = re.compile(r"\d\s*(?:[-–—~至到]|to\b)\s*[$€£]?\d[\d,.]*\s*(?="
                          r"%|％|个?百分点|基点|basis points?|bps?\b|shares?\b|million|billion|"
                          r"trillion|thousand|万|亿|千|" + CURRENCY + ")", re.I)

# Only a direct, bounded denial of a rate. Comparatives such as “not less
# than” and additive constructions such as “not only” assert different things.
REJECTED_RATE = re.compile(
    r"(?:而非|而不是|并非|并不是|(?:^|(?<=[，,；;。！？（(\s]))不是)"
    r"(?:同比|环比)?(?:增长|提高|上升|下降|减少|为)?(?:了)?(?:约)?\s*$|"
    r"(?:\brather than|\binstead of|(?:^|(?<=[,;:(]))\s*not)\s+"
    r"(?:(?:an?\s+)?(?:increase|decrease|gain|decline)\s+of\s+)?(?:about\s+)?$",
    re.I)


def _quantity_spans(text):
    for match in NUMBER.finditer(text):
        before, after = text[:match.start()], text[match.end():]
        prefix = PREFIX.search(before)
        scale = SCALE.match(after)
        factor = SCALES[scale.group().strip().lower()] if scale else 1
        suffix = SUFFIX.match(after, scale.end() if scale else 0)
        # A bare year, quarter, table index or technical identifier carries no amount unit.
        if not prefix and not suffix:
            continue
        start = prefix.start() if prefix else match.start()
        end = match.end() + (suffix.end() if suffix else scale.end() if scale else 0)
        raw_unit = (suffix["unit"] if suffix else prefix["currency"]).lower()
        unit = CURRENCIES.get(raw_unit)
        if unit is None:
            if raw_unit in ("shares", "share", "股"):
                unit = "shares"
            elif "point" in raw_unit or raw_unit.startswith(("bp", "pp", "个", "百分点", "基点")):
                unit = "percentage_points"
                if "basis" in raw_unit or "基点" in raw_unit or raw_unit.startswith("bp"):
                    factor = Decimal(factor) / 100
            else:
                unit = "%"
        elif (per_share := PER_SHARE.match(text[end:])) or PER_SHARE_BEFORE.search(text[:start]):
            unit += "/share"
            if per_share:
                end += per_share.end()
        left = text[:start]
        if left.endswith(("-", "−", "+", "负", "正")):
            # A dash between figures introduces the other endpoint of a range.
            if len(left) < 2 or not left[-2].isdigit():
                start -= 1
                left = text[:start]
        raw = text[start:end].strip()
        amount = Decimal(match.group().replace(",", ""))
        negative = (any(sign in text[start:match.start()] for sign in "-−负")
                    or (unit.split("/")[0] in CURRENCIES.values() and left.rstrip().endswith("(")
                        and text[end:].lstrip().startswith(")"))
                    or (unit.endswith("/share") and re.search(r"(?:loss(?:\s+(?:was|of))?|亏损(?:为|约)?)\s*$", left, re.I))
                    or bool(DOWN.search(left))
                    or (unit in {"%", "percentage_points"} and LOWER_THAN.search(left)))
        # Chinese can place the sign after the amount, e.g. “20万美元的负贡献”.
        contribution = CONTRIBUTION_SIGN.match(text[end:])
        if contribution and not negative and not any(mark in raw for mark in "+正"):
            negative = contribution["sign"] == "负"
        value = amount * factor * (-1 if negative else 1)
        # Accepted display error is half a unit of the last printed decimal place.
        tolerance = Decimal(1).scaleb(amount.as_tuple().exponent) * factor / 2
        yield start, end, (raw, unit, value, tolerance)


def _quantities(text):
    for _, _, quantity in _quantity_spans(text):
        yield quantity


def bare_stated_metric(text):
    """Return a named measure only when the entire sentence merely states one level."""
    name = plain_amount_metric(text)
    if name:
        amounts = list(_quantities(text))
        if len(amounts) == 1 and text.rstrip("。.!！ ").endswith(amounts[0][0]):
            return name
    return None


def _display_unit(unit):
    if isinstance(unit, str):
        currency, _, scale = unit.partition(" ")
        if currency in CURRENCIES.values() and scale in ("thousands", "millions", "billions"):
            return currency, SCALES[scale[:-1]]
    return unit, 1


def _matches(unit, value, tolerance, record):
    raw = record.get("value")
    if type(raw) not in {int, float} or record.get("stale") or record.get("scale", 1) != 1:
        return False
    ref_unit, multiplier = _display_unit(record.get("unit"))
    if ref_unit == "USD/shares":
        ref_unit = "USD/share"
    if unit in {"$", "$/share"}:
        unit = unit.replace("$", "USD") if ref_unit in {"USD", "USD/share"} else unit
    number = Decimal(str(raw)) * multiplier
    return number.is_finite() and ref_unit == unit and abs(value-number) <= tolerance


def assertion_fields(claim):
    """Read both persisted claim layouts. The verifier flags conflicting duplicates."""
    constraints = claim.get("constraints")
    nested = constraints if isinstance(constraints, dict) else {}
    return {k:claim.get(k, nested.get(k)) for k in ("asserted_value", "asserted_unit")
            if k in claim or k in nested}


def display_check(claim, records, *, record_index=None):
    """Check asserted quantities. Preserve explicit rejected rates for semantic review."""
    text = claim.get("text", "")
    asserted_text, amounts, rejected = [], [], []
    cursor = 0
    for start, end, quantity in _quantity_spans(text):
        contribution = CONTRIBUTION_SIGN.match(text[end:])
        if contribution and quantity[2] and (quantity[2] < 0) != (contribution["sign"] == "负"):
            return {"method":"mismatch", "unmatched":[quantity[0] + contribution[0]], "matched":[]}
        cue = REJECTED_RATE.search(text[:start]) if quantity[1] in {"%", "percentage_points"} else None
        if cue and re.match(r"\s*(?:$|[，,；;。.!！?？）)])", text[end:]):
            rejected.append({"text":text[cue.start():end].strip(), "display":quantity[0],
                             "start":start, "end":end})
            asserted_text.extend((text[cursor:cue.start()], " "))
            cursor = end
        else:
            amounts.append(quantity)
    claim = {**claim, **assertion_fields(claim)}
    if record_index is None:
        record_index = {r["id"]: r for r in records if "id" in r}
    if not rejected:
        return _check_amounts(claim, records, text, amounts, record_index)
    asserted_text.append(text[cursor:])
    checked = _check_amounts(claim, records, "".join(asserted_text), amounts, record_index) if amounts else {"method":"source_review"}
    return {**checked, "negated_displays":rejected}


def _check_amounts(claim, records, text, amounts, record_index):
    cited_document = any(r.get("kind") == "document" for r in records)
    if amounts and records and all(r.get("kind") == "source_metadata" for r in records):
        return {"method": "mismatch", "unmatched": [amount[0] for amount in amounts]}
    if not amounts or SHARED_RANGE.search(text):
        if amounts or "asserted_value" in claim or (cited_document and re.search(r"\d", text)):
            return {"method":"source_review"}
        return {}
    typed = [r for r in records if r.get("kind") != "document" and type(r.get("value")) in (int, float)
             and not r.get("stale")]
    has_numeric_source = bool(typed)
    matched, missing = [], []
    asserted = {"unit":claim.get("asserted_unit"), "value":claim.get("asserted_value")}
    if len(amounts) == 1:
        # When a claim declares a financial context, another period's identical
        # value cannot stand in for the result. Calculations carry their own context.
        anchors = [r for r in typed if "operation" in r] or typed
        dimensions = claim.get("constraints", {})
        if isinstance(dimensions, dict):
            typed = [r for r, _ in compatible_records(anchors, dimensions, record_index, citations=records)]
        literal, unit, value, tol = amounts[0]
        # A tax benefit is the opposite of its expense-signed reconciliation
        # fact. Bind the magnitude to this exact concept and to local wording.
        if (typed and unit in CURRENCIES.values() and
                all(r.get("kind") == "financial_fact" and r.get("concept") == VALUATION_ALLOWANCE_EXPENSE for r in typed)):
            before, _, after = text.partition(literal)
            if BENEFIT_BEFORE.search(before) or (RELEASE_BEFORE.search(before) and BOOKED_BENEFIT.match(after)):
                value = -value
                amounts = [(literal, unit, value, tol)]
                stated = EXPENSE_SIGN.search(after)
                if ((stated and ((stated[1] == "负") != (value < 0)))
                        or not any(_matches(unit, value, tol, r) for r in typed)):
                    return {"method":"mismatch", "unmatched":[literal], "matched":[]}
        # Parentheses can either denote an accounting loss or simply introduce
        # an aside. If the opposite sign alone fits, the convention needs reading.
        if (value and not any(sign in literal for sign in "-−负+")
                and re.search(r"[（(]\s*" + re.escape(literal) + r"\s*[)）]", text)
                and any(_matches(unit, -value, tol, anchor) for anchor in [*typed, asserted])):
            return {"method":"source_review"}
    # An explicit result must actually be displayed, even when the claim also
    # prints prior-period amounts from the same citations.
    if asserted["value"] is not None and (not isinstance(asserted["unit"], str) or _display_unit(asserted["unit"])[0] not in DISPLAY_UNITS):
        return {"method":"source_review"}
    if not has_numeric_source or (not typed and cited_document):
        if asserted["value"] is not None and not any(
                _matches(unit, value, tol, asserted) for _, unit, value, tol in amounts):
            return {"method":"mismatch", "unmatched":[literal for literal, *_ in amounts], "matched":[]}
        if len(amounts) == len(records) == 1 and records[0].get("kind") == "document":
            literal, unit, value, tolerance = amounts[0]
            cell = comparison_cell(claim, records[0], literal) if unit in {"USD","$"} else None
            if cell:
                printed = cell.pop("base_value")
                if abs(printed - value) > tolerance:
                    # A finer-grained number can fall inside the precision of
                    # the printed cell. Its extra precision is not established.
                    last_place = Decimal(cell["printed"].replace(",","")).as_tuple().exponent
                    cell_tolerance = Decimal(1).scaleb(last_place) * SCALES[cell["scale"]] / 2
                    if abs(printed - value) <= cell_tolerance:
                        return {"method":"source_review","printed_cell":cell}
                    return {"method":"mismatch","unmatched":[literal],"printed_cell":cell}
                return {"method":"matched_printed_cell","printed_cell":cell}
        return {"method":"source_review"}
    cursor = 0
    bound_count = 0
    contradictions = []
    qualified_context = False
    assertion_displayed = asserted["value"] is None
    for literal, unit, value, tolerance in amounts:
        position = text.find(literal,cursor)
        anchors = []
        if position >= 0:
            prefix = text[cursor:position]
            # A forecast/segment heading can govern several following amounts.
            # It must not undo a contradiction already found in an earlier clause.
            qualified_context = qualified_context or bool(QUALIFIED_BASIS.search(prefix))
            if not qualified_context:
                anchors = direct_amount_anchors(prefix, claim.get("constraints",{}), typed)
                if (not anchors and prefix.rstrip().endswith("(")
                        and text[position+len(literal):].lstrip().startswith(")")):
                    anchors = direct_amount_anchors(prefix, claim.get("constraints",{}), typed, parenthetical=True)
                    if (anchors and value < 0 and not any(sign in literal for sign in "-−负+")
                            and any(_matches(unit, -value, tolerance, r) for r in anchors)):
                        # The label binds this parenthetical aside to one
                        # source metric. Unlabelled accounting losses retain
                        # their sign and exact assertions still require checks.
                        value = -value
            cursor = position + len(literal)
        if not assertion_displayed:
            assertion_displayed = _matches(unit, value, tolerance, asserted)
        if anchors:
            bound_count += 1
        hits = [r["id"] for r in (anchors or typed) if _matches(unit, value, tolerance, r)]
        if hits:
            entry = {"display":literal, "record_ids":hits}
            if anchors:
                entry["metric"] = anchors[0]["metric"]
                entry["period_end"] = anchors[0]["period_end"]
            matched.append(entry)
        else:
            missing.append(literal)
            if anchors:
                source = anchors[0]
                contradictions.append({"display":literal, "record_ids":[r["id"] for r in anchors],
                                       **{k:source.get(k) for k in ("entity","metric","period_end","value","unit")}})
    if not assertion_displayed:
        return {"method":"mismatch", "unmatched":[literal for literal, *_ in amounts], "matched":[]}
    if contradictions:
        return {"method":"mismatch", "unmatched":[c["display"] for c in contradictions],
                "expected":contradictions,"matched":matched}
    # Additional figures in a mixed document/table claim can refer to disclosures
    # such as sales growth in a paragraph alongside the machine-readable revenue.
    if missing and cited_document and (len(amounts)>1 or asserted["value"] is None):
        return {"method":"source_review", "matched":matched}
    if missing:
        return {"method":"mismatch", "unmatched":missing, "matched":matched}
    if bound_count != len(amounts) and len(amounts) > 1 and len({unit for _, unit, _, _ in amounts}) < len(amounts):
        # The values can all be present but attached to the wrong periods or metrics.
        return {"method":"source_review", "matched":matched}
    return {"method":"matched_records", "matched":matched}


DISPLAY_LABELS = {
    "matched_records":"正文中识别出的金额或比率已与所引数值记录按单位和显示精度比对。",
    "matched_printed_cell":"正文金额已与所引段落中具名行、期间列和原表量级核对。",
    "source_review":"核验方式为原文审读，数字与期间、指标的对应关系需结合引文判断。",
    "mismatch":"正文中的数值或单位未通过所引记录核对。",
}
