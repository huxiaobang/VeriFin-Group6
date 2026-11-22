"""Compare prior-quarter issuer revenue guidance with the target quarter's reported revenue."""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import islice
import re

from .data import cutoff_time, parse_day
from .financial import GUIDANCE_VARIANCE
from .revenue_bridge import layout
from .skill_calculations import previous_quarter
from .statement_amount_cells import CURRENCIES, CURRENCY, DATE, MONTHS, nearby, prior_currency_evidence, text
from .structured_documents import _hidden


ORDINAL = "first second third fourth".split()
QUARTER_WORDS = "|".join(ORDINAL)
PERIOD = rf"(?P<quarter>{QUARTER_WORDS})[ -]quarter(?:\s+(?:of|and))?\s+fiscal(?: year)?\s+(?P<year>20\d{{2}})"
RELEASE_TITLES = [re.compile(pattern, re.I) for pattern in (
    rf"\b(?:financial |earnings )?results\s+for\s+(?:the\s+)?{PERIOD}\b",
    rf"\b{PERIOD}\s+(?:(?:financial|earnings)\s+)?results\b",
)]
OUTLOOK_TITLE = re.compile(rf"\b(?:outlook|guidance)\s+for\s+(?:the\s+)?{PERIOD}\b", re.I)
SUMMARY_TITLE = re.compile(r"Q([1-4])\s+Fiscal\s+(20\d{2})\s+(?:(?:Results|Financial)\s+)?Summary", re.I)
QUARTER_HEADER = re.compile(r"Q([1-4])\s+FY(\d{2}|20\d{2})", re.I)
MONEY = r"(?:(?:USD|CAD|AUD|NZD|HKD|SGD|EUR|GBP|JPY|CNY|CHF)\s*|(?:US|CA|AU)?\$)"
AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
SCALES = {"thousand": Decimal("0.001"), "million": Decimal(1), "billion": Decimal(1000)}
UNIT_NOTE = re.compile(r"\b(thousand|million|billion)s\b", re.I)
REVENUE = r"(?:total\s+|consolidated\s+)?revenues?"
FORECAST = rf"{REVENUE}\s+(?:is expected to be|is projected to be|is forecast to be|guidance (?:is|of))\s+"
CENTER = re.compile(rf"{FORECAST}({MONEY})\s*({AMOUNT})\s+(thousand|million|billion)"
                    rf"(?:\s*,?\s*(?:(?:plus or minus|±)\s*({AMOUNT})\s*%))?\s*\.?", re.I)
RANGE = re.compile(rf"{FORECAST}(?:between\s+|from\s+)?({MONEY})\s*({AMOUNT})"
                   rf"(?:\s+(thousand|million|billion))?\s*(?:to|and|[-–—])\s*"
                   rf"({MONEY})?\s*({AMOUNT})\s+(thousand|million|billion)\s*\.?", re.I)
PRINTED_AMOUNT = re.compile(rf"({MONEY})?\s*({AMOUNT})", re.I)
BASIS = "reported consolidated revenue"
QUARTER_END = re.compile(rf"\breported\b.{{0,120}}?(?:{QUARTER_WORDS})[ -]quarter ended\s+{DATE.pattern}", re.I)


def period_label(match):
    return f"{match['year']}Q{ORDINAL.index(match['quarter'].lower()) + 1}"


def amount(value, scale):
    return Decimal(value.replace(",", "")) * SCALES[scale.lower()]


def explicit_currency(text_):
    currencies = {CURRENCIES.get(m.upper(), m.upper()) for m in CURRENCY.findall(text_)}
    currencies.update({"US$": "USD", "CA$": "CAD", "AU$": "AUD"}[m.upper()]
                      for m in re.findall(r"(?:US|CA|AU)\$", text_, re.I))
    return currencies


@dataclass(frozen=True)
class Amount:
    value: Decimal
    locator: str
    quote: str
    published_unit: str
    currencies: frozenset


def statements(doc):
    from lxml import etree, html
    try:
        root = html.document_fromstring(doc["raw"], parser=html.HTMLParser(encoding="utf-8", no_network=True, recover=True, remove_comments=True))
    except (etree.ParserError, etree.XMLSyntaxError):
        return None
    tree = root.getroottree()
    paths = {node: tree.getpath(node) for node in root.iter() if isinstance(node.tag, str)}
    title = root.xpath("string(//title)")
    for node in paths:
        if _hidden(node) and node.getparent() is not None:
            node.drop_tree()
    headings = [title, *(text(node) for node in root.xpath(
        "//h1 | //h2 | //h3 | //body//font[not(ancestor::table)] | "
        "//body//p[not(ancestor::table)] | //body//div[not(*)]"))]
    periods = {period_label(match) for heading in headings if len(heading) < 240
               for pattern in RELEASE_TITLES if (match := pattern.search(heading))}
    if len(periods) != 1:
        return None
    return root, paths, periods.pop()


def release_period_end(root, paths, expected_end):
    """Cross-check a printed quarter end when the press-release introduction provides one."""
    matches = {}
    for node in root.xpath("//body//p[not(ancestor::table)] | //body//font[not(ancestor::table)]"):
        line = text(node)
        if len(line) > 500 or not (m := QUARTER_END.search(line)):
            continue
        try:
            stated_end = date(int(m[3]), MONTHS[m[1].casefold()], int(m[2]))
        except ValueError:
            return False, None
        matches.setdefault(stated_end, paths[node])
    return (not matches or set(matches) == {expected_end}), matches.get(expected_end)


def forecast_amounts(root, paths, target):
    """Read a bounded, explicitly labeled issuer outlook paragraph and consecutive bullets."""
    results = []
    for node in root.xpath("//body//*[self::p or self::div or self::h2 or self::h3][not(ancestor::table)]"):
        header = text(node)
        if len(header) > 240 or not (match := OUTLOOK_TITLE.search(header)) or period_label(match) != target:
            continue
        if any(OUTLOOK_TITLE.search(text(child)) for child in node if child.tag in {"p", "div", "h2", "h3"}):
            continue  # Keep a single block when an outer wrapper repeats the text.
        for sibling in islice(node.itersiblings(), 12):
            if OUTLOOK_TITLE.search(text(sibling)) or sibling.xpath(".//table"):
                break
            if sibling.tag in {"ul", "ol"}:
                bullets = sibling.findall("li")
            elif sibling.tag in {"p", "div"}:
                bullets = [sibling]
            else:
                break
            for bullet in bullets:
                quote = re.sub(r"^[•●▪\s]+", "", text(bullet))
                center, span = CENTER.fullmatch(quote), RANGE.fullmatch(quote)
                if not center and not span:
                    continue
                locator = f"{paths[bullet]} (outlook period at {paths[node]})"
                currencies = frozenset(explicit_currency(quote + " " + header))
                if center:
                    currency, value, scale, _ = center.groups()
                    results.append((Amount(amount(value, scale), locator, quote, currency.strip() + " " + scale, currencies),))
                else:
                    left_currency, low, left_scale, right_currency, high, right_scale = span.groups()
                    right_currency = right_currency or left_currency
                    left_scale = left_scale or right_scale
                    points = (amount(low, left_scale), amount(high, right_scale))
                    if points[0] <= points[1] and len(currencies) <= 1:
                        results.append(tuple(Amount(value, f"{locator} ({side})", quote, unit, currencies)
                                             for value, side, unit in zip(
                                                 points, ("lower bound", "upper bound"),
                                                 (left_currency.strip() + " " + left_scale, right_currency.strip() + " " + right_scale))))
    return results


def summary_amounts(root, paths, target):
    """Map the GAAP quarterly-summary revenue row to explicit fiscal-quarter columns."""
    for table in root.xpath("//table[not(.//table)]"):
        titles = [(node, match) for node in nearby(table) if (match := SUMMARY_TITLE.fullmatch(text(node)))]
        if len(titles) != 1 or target != f"{titles[0][1][2]}Q{titles[0][1][1]}":
            continue
        try:
            rows = [row for row in layout(table) if any(cell[1] for cell in row)]
        except ValueError:
            continue
        if len(rows) < 3 or [printed.casefold() for _, printed, _ in rows[0] if printed] != ["gaap"]:
            continue
        heading, *columns = [(cols, printed, node) for cols, printed, node in rows[1] if printed]
        unit = UNIT_NOTE.search(heading[1])
        if not unit or ("$" not in heading[1] and not explicit_currency(heading[1])):
            continue
        revenue_rows = [row for row in rows[2:] if re.fullmatch(REVENUE, next(
            (printed for _, printed, _ in row if printed), ""), re.I)]
        if len(revenue_rows) != 1:
            continue
        results = {}
        for cols, header, header_node in columns:
            quarter = QUARTER_HEADER.fullmatch(header)
            if not quarter:
                continue
            year = quarter[2] if len(quarter[2]) == 4 else "20" + quarter[2]
            label = f"{year}Q{quarter[1]}"
            cells = [(printed, node) for cell_cols, printed, node in revenue_rows[0]
                     if cell_cols == cols and PRINTED_AMOUNT.fullmatch(printed)]
            if len(cells) != 1 or label in results:
                break
            printed, value_node = cells[0]
            locator = (f"{paths[value_node]} ({text(titles[0][0])} / column {header} at "
                       f"{paths[header_node]} / units at {paths[heading[2]]})")
            results[label] = Amount(amount(PRINTED_AMOUNT.fullmatch(printed)[2], unit[1]), locator,
                                    f"{header} Revenue {printed}", heading[1],
                                    frozenset(explicit_currency(heading[1] + " " + printed)))
        else:
            if target in results:
                yield results


def quarter_window(doc):
    if not doc.get("period_start") or not doc.get("period_end"):
        return None
    try:
        start, end = parse_day(doc["period_start"]), parse_day(doc["period_end"])
    except ValueError:
        return None
    if 76 <= (end - start).days <= 104 and end <= parse_day(doc["published_at"][:10]):
        return start, end
    return None


def report_currency(data, actual_doc, summary, prior_doc, prior_summaries, forecasts):
    actual = summary[actual_doc["fiscal_quarter"]]
    currencies = set(actual.currencies).union(*(point.currencies for point in forecasts))
    if len(currencies) > 1:
        return None, None
    if actual.currencies and all(point.currencies for point in forecasts):
        return currencies.pop(), None
    prior_label = prior_doc["fiscal_quarter"]
    comparative = summary.get(prior_label)
    prior_cells = [table[prior_label] for table in prior_summaries]
    if not data or not comparative or not prior_cells or any(cell.value != comparative.value for cell in prior_cells):
        return None, None
    # The same reported prior quarter in both releases can identify the currency
    # of unqualified dollars when it matches a previously filed currency witness.
    currencies.update(comparative.currencies)
    currencies.update(*[cell.currencies for cell in prior_cells])
    key = ("revenue", prior_doc["period_start"], prior_doc["period_end"], comparative.value * 1_000_000)
    references = prior_currency_evidence(data, actual_doc, metrics=("revenue",)).get(key, {})
    currencies.update(references)
    if len(currencies) != 1 or not references:
        return None, None
    currency = currencies.pop()
    return currency, references[currency]

def direct_comparison(target, documents, result, *, data, period_start=None, period_end=None):
    prior = previous_quarter(target)
    releases = []
    for doc in documents:
        if not doc["name"].lower().endswith((".htm", ".html")) or not (window := quarter_window(doc)):
            continue
        parsed = statements(doc)
        if parsed and parsed[2] in (target, prior):
            root, paths, label = parsed
            date_matches, end_locator = release_period_end(root, paths, window[1])
            if not date_matches:
                continue
            summaries = list(summary_amounts(root, paths, label))
            forecasts = forecast_amounts(root, paths, target) if label == prior else []
            release = dict(doc, fiscal_quarter=label, period_end_locator=end_locator)
            releases.append((release, window, summaries, forecasts))
    comparisons = []
    for actual_doc, actual_window, summaries, _ in releases:
        if (actual_doc["fiscal_quarter"] != target
                or period_start and actual_doc["period_start"] != period_start
                or period_end and actual_doc["period_end"] != period_end):
            continue
        for summary in summaries:
            for prior_doc, prior_window, prior_summaries, forecasts in releases:
                if (prior_doc["entity"] != actual_doc["entity"] or prior_doc["fiscal_quarter"] != prior
                        or prior_window[1] + timedelta(days=1) != actual_window[0]
                        or cutoff_time(prior_doc["published_at"]) >= cutoff_time(actual_doc["published_at"])):
                    continue
                for forecast in forecasts:
                    currency, reference = report_currency(data, actual_doc, summary, prior_doc, prior_summaries, forecast)
                    if currency and all(p.value > 0 for p in forecast):
                        comparisons.append((actual_doc, prior_doc, summary[target], forecast, currency, reference))
    if not comparisons:
        result.gap("未找到同一公司、财季、报告币种的原表实绩及上一季度公告中的直接收入指引。")
        return
    # Equivalent issuer and SEC copies have the same issuer-reported economics.
    # Conflicting values or release dates require choosing a specific edition with evidence.
    def signature(pair):
        actual, prior, cell, forecast, currency, _ = pair
        return (actual["period_start"], actual["period_end"], actual["published_at"][:10],
                prior["published_at"][:10], cell.value, tuple(p.value for p in forecast), currency)

    if len({signature(pair) for pair in comparisons}) != 1:
        result.gap("发现不同披露版次、金额或报告币种，无法唯一确定历史指引与实际值。")
        return
    actual_doc, prior_doc, cell, forecast, currency, reference = min(comparisons, key=lambda pair: (
        pair[0].get("form") != "8-K", pair[1].get("form") != "8-K", pair[0]["url"], pair[1]["url"]))
    unit = f"{currency} millions"
    period = f"FY{target[:4]} Q{target[-1]}"
    dimensions = dict(period_start=actual_doc["period_start"], period_end=actual_doc["period_end"],
                      fiscal_quarter=target, currency=currency, business_scope="consolidated", basis=BASIS,
                      currency_reference=reference)

    def fact(doc, cell, role):
        period_end_locator = doc.get("period_end_locator") if role == "reported_actual" else None
        return result.fact(doc, cell.locator, "revenue", cell.value, unit, period, cell.quote,
                           guidance_role=role, printed_unit=cell.published_unit,
                           period_end_locator=period_end_locator, **dimensions)

    actual = fact(actual_doc, cell, "reported_actual")
    if len(forecast) == 1:
        source_ids = [fact(prior_doc, forecast[0], "guidance_midpoint")]
        midpoint = source_ids[0]
    else:
        source_ids = [fact(prior_doc, bound, role) for bound, role in zip(forecast, ("guidance_low", "guidance_high"))]
        midpoint = result.calc(f"{period} revenue guidance midpoint", "midpoint", source_ids, unit)
    variance = result.calc(f"{period} revenue vs prior-quarter guidance midpoint", GUIDANCE_VARIANCE, [actual, midpoint], "%")
    guidance_value, actual_value, variance_value = (result.value(ref) for ref in (midpoint, actual, variance))
    direction = "高于" if variance_value > 0 else "低于" if variance_value < 0 else "等于"
    result.note(f"{period} 公司收入实际为 {actual_value:,} {unit}，上一季度公司的指引中点为 "
                f"{guidance_value:,} {unit}，实际{direction}指引中点。"
                f"（{actual_value:,} ÷ {guidance_value:,} − 1）× 100 = {variance_value:+.4f}%。",
                [actual, *source_ids, midpoint, variance], fiscal_quarter=target, period_end=dimensions["period_end"])
